import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from fastapi import APIRouter, UploadFile, File, Form, Depends, Query, BackgroundTasks
from fastapi import HTTPException

from app.config import config
from app.database.timescaledb import tsdb_pool
from app.database.mysql import mysql_pool
from app.algorithms.waveform_io import waveform_io
from app.algorithms.order_resampling import order_resampler
from app.algorithms.spectral_decomposition import spectral_decomposer
from app.algorithms.fatigue_damage import fatigue_calculator
from app.error_codes import ErrorCode, BusinessException
from app.schemas.schemas import (
    ApiResponse, ResponseStatus,
    WaveformShardUploadRequest, WaveformShardUploadResponse,
    WaveformUploadCompleteRequest, WaveformUploadCompleteResponse
)
from app.utils.failure_storage import failure_storage
from app.utils.callback_push import callback_service, ResonanceAlert
from app.middleware.middleware import ConcurrentRequestLimiter, rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ingestion", tags=["数据接入"])

_upload_status: Dict[str, Dict[str, Any]] = {}
_executor = ThreadPoolExecutor(max_workers=32)


async def _validate_unit_and_blade(unit_id: str, blade_id: str) -> Dict[str, Any]:
    blade_info = await mysql_pool.get_unit_blade_info(unit_id, blade_id)
    if not blade_info:
        raise BusinessException(
            ErrorCode.BLADE_NOT_FOUND,
            f"叶片 {blade_id} 在机组 {unit_id} 中不存在或已停用"
        )
    return blade_info


async def _trigger_analysis_pipeline(
    unit_id: str,
    blade_id: str,
    channel_id: int,
    upload_id: str,
    strain_data: List[float],
    rpm: float,
    sample_rate: int,
    start_time: datetime,
    blade_count: int,
    blade_info: Dict[str, Any]
) -> None:
    try:
        strain_array = waveform_io.detrend_waveform(
            strain_data, method="linear"
        )

        num_samples = len(strain_array)
        rpm_array = order_resampler.compute_rpm_profile(
            rpm_value=rpm,
            num_samples=num_samples,
            sample_rate=sample_rate
        )

        resample_result = order_resampler.process_waveform(
            strain_data=strain_array,
            rpm=rpm_array,
            sample_rate=sample_rate,
            blade_count=blade_count
        )

        order_signal = np.array(resample_result["order_signal"], dtype=np.float64)
        order_axis = np.array(resample_result["order_values"], dtype=np.float64)

        decompose_result = spectral_decomposer.decompose(
            order_domain_signal=order_signal,
            order_axis=order_axis,
            base_order=blade_count
        )

        await tsdb_pool.batch_insert(
            table="order_resampled_waveforms",
            columns=[
                "time", "unit_id", "blade_id", "channel_id",
                "base_order", "order_values", "amplitude_values",
                "phase_values", "rpm_range", "analysis_window", "upload_id"
            ],
            records=[[
                start_time, unit_id, blade_id, channel_id,
                resample_result["base_order"],
                resample_result["order_values"],
                resample_result["amplitude_values"],
                resample_result["phase_values"],
                resample_result["rpm_range"],
                f"{resample_result['analysis_window_seconds']} seconds",
                upload_id
            ]]
        )

        await tsdb_pool.batch_insert(
            table="order_spectrum",
            columns=[
                "time", "unit_id", "blade_id", "channel_id",
                "resonance_orders", "resonance_amplitudes",
                "harmonic_orders", "harmonic_amplitudes",
                "sideband_orders", "sideband_amplitudes",
                "noise_floor", "snr", "upload_id"
            ],
            records=[[
                start_time, unit_id, blade_id, channel_id,
                decompose_result["resonance_orders"],
                decompose_result["resonance_amplitudes"],
                decompose_result["harmonic_orders"],
                decompose_result["harmonic_amplitudes"],
                decompose_result["sideband_orders"],
                decompose_result["sideband_amplitudes"],
                decompose_result["noise_floor"],
                decompose_result["snr"],
                upload_id
            ]]
        )

        elastic_modulus = float(blade_info.get("elastic_modulus_gpa", 185.0))
        stress_data = waveform_io.convert_strain_to_stress(
            strain_array, elastic_modulus
        )

        material_params = {
            "sn_slope": float(blade_info.get("sn_slope", config.fatigue_damage.sn_curve_slope)),
            "sn_intercept": float(blade_info.get("sn_intercept", config.fatigue_damage.sn_curve_intercept)),
            "fatigue_limit": config.fatigue_damage.fatigue_limit_stress,
            "ultimate_strength_mpa": config.fatigue_damage.ultimate_tensile_strength,
        }

        fatigue_result = fatigue_calculator.calculate(
            stress_history=stress_data,
            material_params=material_params,
            operation_hours=num_samples / sample_rate / 3600.0,
            accumulated_damage=0.0,
            design_life_hours=float(blade_info.get("design_life_hours", 100000.0))
        )

        await tsdb_pool.batch_insert(
            table="fatigue_damage",
            columns=[
                "time", "unit_id", "blade_id", "channel_id",
                "damage_value", "remaining_life", "cycle_count",
                "max_stress", "min_stress", "mean_stress",
                "stress_amplitude", "damage_accumulated", "upload_id"
            ],
            records=[[
                start_time, unit_id, blade_id, channel_id,
                fatigue_result["damage_value"],
                fatigue_result["remaining_life_hours"],
                fatigue_result["cycle_count"],
                fatigue_result["max_stress"],
                fatigue_result["min_stress"],
                fatigue_result["mean_stress"],
                fatigue_result["stress_amplitude"],
                fatigue_result["damage_accumulated"],
                upload_id
            ]]
        )

        has_exceedance, exceeded = callback_service.detect_resonance_exceedance(
            decompose_result=decompose_result,
            fatigue_result=fatigue_result,
            unit_id=unit_id,
            blade_id=blade_id
        )

        if has_exceedance:
            try:
                unit_info = await mysql_pool.get_unit_info(unit_id)
                blade_info_full = await mysql_pool.get_blade_info(blade_id)
                channel_info = await mysql_pool.get_channel_info(channel_id)

                alert = ResonanceAlert(
                    unit_id=unit_id,
                    blade_id=blade_id,
                    channel_id=channel_id,
                    analysis_time=datetime.utcnow(),
                    base_order=float(blade_count),
                    resonance_orders=list(decompose_result.get("resonance_orders", [])),
                    resonance_amplitudes=list(decompose_result.get("resonance_amplitudes", [])),
                    snr=float(decompose_result.get("snr", 0.0)),
                    rpm_range=list(resample_result.get("rpm_range", [0.0, 0.0])),
                    avg_rpm=float(rpm),
                    max_damage=float(fatigue_result.get("damage_value", 0.0)),
                    damage_accumulated=float(fatigue_result.get("damage_accumulated", 0.0)),
                    remaining_life_hours=float(fatigue_result.get("remaining_life_hours", 0.0)),
                    max_stress=float(fatigue_result.get("max_stress", 0.0)),
                    stress_amplitude=float(fatigue_result.get("stress_amplitude", 0.0)),
                    cycle_count=int(fatigue_result.get("cycle_count", 0)),
                    spectral_centroid=float(decompose_result.get("spectral_centroid", 0.0)),
                    spectral_bandwidth=float(decompose_result.get("spectral_bandwidth", 0.0)),
                    noise_floor=float(decompose_result.get("noise_floor", 0.0)),
                    harmonic_orders=list(decompose_result.get("harmonic_orders", [])),
                    harmonic_amplitudes=list(decompose_result.get("harmonic_amplitudes", [])),
                    sideband_orders=list(decompose_result.get("sideband_orders", [])),
                    sideband_amplitudes=list(decompose_result.get("sideband_amplitudes", [])),
                    threshold_exceeded=exceeded,
                    blade_number=blade_info.get("blade_number"),
                    stage=blade_info.get("stage"),
                    blade_type=blade_info.get("blade_type"),
                    material=blade_info.get("material"),
                    unit_name=unit_info.get("unit_name") if unit_info else None,
                    plant_name=unit_info.get("plant_name") if unit_info else None,
                    location_mm=channel_info.get("location_mm") if channel_info else None,
                    angle_deg=channel_info.get("angle_deg") if channel_info else None,
                )

                await callback_service.push_resonance_alert(alert)
                logger.info(
                    f"Resonance alert callback triggered for upload_id={upload_id}, "
                    f"exceeded_metrics={len(exceeded)}, "
                    f"damage={fatigue_result['damage_value']:.6f}"
                )
            except Exception as callback_error:
                logger.error(
                    f"Callback push failed for upload_id={upload_id}: {callback_error}",
                    exc_info=True
                )

        logger.info(
            f"Analysis pipeline completed for upload_id={upload_id}, "
            f"damage={fatigue_result['damage_value']:.6f}, "
            f"snr={decompose_result['snr']:.2f}dB"
            f"{', resonance_alert_triggered' if has_exceedance else ''}"
        )

    except Exception as e:
        logger.error(f"Analysis pipeline failed for upload_id={upload_id}: {e}")
        import traceback
        stack_trace = traceback.format_exc()

        await failure_storage.save_analysis_failure(
            unit_id=unit_id,
            blade_id=blade_id,
            upload_id=upload_id,
            error_code=getattr(e, "code", ErrorCode.INTERNAL_SERVER_ERROR),
            error_message=str(e),
            raw_strain=strain_data if isinstance(strain_data, list) else None,
            raw_rpm=None,
            algorithm_params={
                "blade_count": blade_count,
                "sample_rate": sample_rate,
                "rpm": rpm
            },
            stack_trace=stack_trace,
            timestamp=start_time
        )


@router.post("/waveform/shard", response_model=ApiResponse, summary="上传波形分片")
async def upload_waveform_shard(
    request: WaveformShardUploadRequest,
    waveform_data: UploadFile = File(..., description="二进制波形数据文件"),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    client_id = f"{request.unit_id}_{request.blade_id}"
    if not await rate_limiter.check_rate_limit(client_id):
        raise BusinessException(ErrorCode.RATE_LIMIT_EXCEEDED)

    sem = ConcurrentRequestLimiter.get_semaphore(
        f"ingest_{request.unit_id}",
        max_concurrent=config.ingestion.max_concurrent_writers
    )

    async with sem:
        await _validate_unit_and_blade(request.unit_id, request.blade_id)

        if request.sample_count <= 0:
            raise BusinessException(
                ErrorCode.PARAM_VALIDATION_ERROR,
                "采样点数必须大于0"
            )

        max_bytes = config.ingestion.max_shard_size_mb * 1024 * 1024
        data = await waveform_data.read()

        if len(data) > max_bytes:
            raise BusinessException(
                ErrorCode.WAVEFORM_TOO_LARGE,
                f"分片大小 {len(data)} 超过限制 {max_bytes}"
            )

        if len(data) == 0:
            raise BusinessException(ErrorCode.WAVEFORM_EMPTY)

        try:
            strain_waveform = waveform_io.parse_binary_waveform(
                binary_data=data,
                sample_count=request.sample_count,
                compression=request.compression
            )
        except BusinessException:
            raise
        except Exception as e:
            raise BusinessException(
                ErrorCode.WAVEFORM_PARSE_ERROR,
                f"波形解析失败: {str(e)}"
            )

        waveform_io.validate_waveform(strain_waveform)

        db_chunks = waveform_io.chunk_for_database(
            waveform=strain_waveform,
            base_time=request.start_time,
            sample_rate=request.sample_rate
        )

        insert_records = []
        for chunk in db_chunks:
            insert_records.append({
                "time": chunk["time"],
                "unit_id": request.unit_id,
                "blade_id": request.blade_id,
                "channel_id": request.channel_id,
                "sample_rate": request.sample_rate,
                "rpm": request.rpm,
                "strain_values": chunk["strain_values"],
                "sample_count": chunk["sample_count"],
                "shard_id": request.shard_id,
                "upload_id": request.upload_id
            })

        try:
            inserted = await tsdb_pool.insert_strain_waveforms_batch(insert_records)
        except BusinessException:
            raise
        except Exception as e:
            raise BusinessException(
                ErrorCode.DATABASE_BATCH_INSERT_ERROR,
                f"数据库插入失败: {str(e)}"
            )

        upload_key = f"{request.upload_id}_{request.unit_id}_{request.blade_id}"
        if upload_key not in _upload_status:
            _upload_status[upload_key] = {
                "total_shards": request.total_shards,
                "received_shards": set(),
                "total_samples": 0,
                "first_sample_time": request.start_time,
                "strain_data": [],
                "blade_count": 0
            }

        status = _upload_status[upload_key]
        status["received_shards"].add(request.shard_index)
        status["total_samples"] += request.sample_count

        if request.task_id:
            size_mb = len(data) / (1024 * 1024)
            try:
                await mysql_pool.update_upload_task_progress(request.task_id, size_mb)
            except Exception as e:
                logger.warning(f"Failed to update task progress: {e}")

        response = WaveformShardUploadResponse(
            shard_id=request.shard_id,
            upload_id=request.upload_id,
            shard_index=request.shard_index,
            success=inserted > 0,
            records_inserted=inserted,
            message=f"成功插入 {inserted} 条记录"
        )

        return ApiResponse(
            code=ErrorCode.SUCCESS,
            message="success",
            status=ResponseStatus.SUCCESS,
            data=response
        )


@router.post("/waveform/complete", response_model=ApiResponse, summary="完成波形上传并触发分析")
async def complete_upload_and_analyze(
    request: WaveformUploadCompleteRequest,
    background_tasks: BackgroundTasks
):
    upload_key = f"{request.upload_id}_{request.unit_id}_{request.blade_id}"
    status = _upload_status.get(upload_key)

    if not status:
        raise BusinessException(
            ErrorCode.PARAM_VALIDATION_ERROR,
            f"未找到上传任务 {request.upload_id}"
        )

    received = len(status["received_shards"])
    if received < request.total_shards:
        raise BusinessException(
            ErrorCode.PARAM_VALIDATION_ERROR,
            f"分片不完整: 已接收 {received}/{request.total_shards}"
        )

    blade_info = await _validate_unit_and_blade(request.unit_id, request.blade_id)

    analysis_job_id = None
    analysis_triggered = False

    if request.trigger_analysis:
        try:
            query = """
                SELECT time, strain_values, sample_rate, rpm, channel_id
                FROM strain_waveforms
                WHERE upload_id = $1
                ORDER BY time
            """
            waveform_records = await tsdb_pool.fetch(query, request.upload_id)

            if not waveform_records:
                raise BusinessException(
                    ErrorCode.WAVEFORM_EMPTY,
                    f"未找到上传ID {request.upload_id} 的波形数据"
                )

            all_strain = []
            for record in waveform_records:
                all_strain.extend(record["strain_values"])

            all_strain = waveform_io.detrend_waveform(all_strain)
            sample_rate = waveform_records[0]["sample_rate"]
            rpm = waveform_records[0]["rpm"]
            channel_id = waveform_records[0]["channel_id"]
            start_time = waveform_records[0]["time"]

            blade_stage = blade_info.get("stage", 1)
            all_blades = await mysql_pool.get_blades_by_unit(request.unit_id)
            stage_blades = [
                b for b in all_blades
                if b.get("stage") == blade_stage
            ]
            blade_num = len(stage_blades) if stage_blades else len(all_blades)
            if blade_num <= 0:
                blade_num = 92

            analysis_job_id = f"job_{request.upload_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

            background_tasks.add_task(
                _trigger_analysis_pipeline,
                unit_id=request.unit_id,
                blade_id=request.blade_id,
                channel_id=channel_id,
                upload_id=request.upload_id,
                strain_data=all_strain,
                rpm=rpm,
                sample_rate=sample_rate,
                start_time=start_time,
                blade_count=blade_num,
                blade_info=blade_info
            )

            analysis_triggered = True
            logger.info(f"Analysis triggered for upload_id={request.upload_id}")

        except Exception as e:
            logger.error(f"Failed to trigger analysis: {e}")
            import traceback
            stack_trace = traceback.format_exc()

            await failure_storage.save_analysis_failure(
                unit_id=request.unit_id,
                blade_id=request.blade_id,
                upload_id=request.upload_id,
                error_code=getattr(e, "code", ErrorCode.INTERNAL_SERVER_ERROR),
                error_message=str(e),
                raw_strain=None,
                raw_rpm=None,
                algorithm_params={"triggered_from": "complete_upload"},
                stack_trace=stack_trace
            )

    if request.task_id:
        try:
            await mysql_pool.complete_task(request.task_id)
        except Exception as e:
            logger.warning(f"Failed to complete task: {e}")

    if upload_key in _upload_status:
        del _upload_status[upload_key]

    response = WaveformUploadCompleteResponse(
        upload_id=request.upload_id,
        success=True,
        total_shards_received=received,
        analysis_triggered=analysis_triggered,
        analysis_job_id=analysis_job_id
    )

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data=response
    )


@router.get("/upload/status/{upload_id}", response_model=ApiResponse, summary="查询上传状态")
async def get_upload_status(upload_id: str, unit_id: str, blade_id: str):
    upload_key = f"{upload_id}_{unit_id}_{blade_id}"
    status = _upload_status.get(upload_key)

    if not status:
        query = """
            SELECT COUNT(*) as count, COUNT(DISTINCT shard_id) as shard_count
            FROM strain_waveforms
            WHERE upload_id = $1 AND unit_id = $2 AND blade_id = $3
        """
        result = await tsdb_pool.fetchrow(query, upload_id, unit_id, blade_id)
        if result and result["count"] > 0:
            return ApiResponse(
                code=ErrorCode.SUCCESS,
                message="success",
                status=ResponseStatus.SUCCESS,
                data={
                    "upload_id": upload_id,
                    "status": "completed",
                    "total_records": result["count"],
                    "shard_count": result["shard_count"]
                }
            )

        raise BusinessException(
            ErrorCode.PARAM_VALIDATION_ERROR,
            f"未找到上传任务 {upload_id}"
        )

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data={
            "upload_id": upload_id,
            "status": "in_progress",
            "received_shards": len(status["received_shards"]),
            "total_shards": status["total_shards"],
            "total_samples": status["total_samples"],
            "progress": f"{len(status['received_shards'])}/{status['total_shards']}"
        }
    )


@router.get("/failures", response_model=ApiResponse, summary="查询分析失败记录")
async def get_analysis_failures(
    unit_id: Optional[str] = Query(None, description="机组ID"),
    blade_id: Optional[str] = Query(None, description="叶片ID"),
    upload_id: Optional[str] = Query(None, description="上传ID"),
    unresolved_only: bool = Query(True, description="仅显示未解决"),
    limit: int = Query(100, ge=1, le=1000, description="返回数量限制")
):
    conditions = []
    params = []
    param_index = 1

    if unit_id:
        conditions.append(f"unit_id = ${param_index}")
        params.append(unit_id)
        param_index += 1

    if blade_id:
        conditions.append(f"blade_id = ${param_index}")
        params.append(blade_id)
        param_index += 1

    if upload_id:
        conditions.append(f"upload_id = ${param_index}")
        params.append(upload_id)
        param_index += 1

    if unresolved_only:
        conditions.append(f"resolved = ${param_index}")
        params.append(False)
        param_index += 1

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    query = f"""
        SELECT failure_id, time, unit_id, blade_id, upload_id,
               error_code, error_message, retry_count, resolved, created_at
        FROM analysis_failures
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT ${param_index}
    """
    params.append(limit)

    results = await tsdb_pool.fetch(query, *params)

    failures = [
        {
            "failure_id": r["failure_id"],
            "time": r["time"],
            "unit_id": r["unit_id"],
            "blade_id": r["blade_id"],
            "upload_id": r["upload_id"],
            "error_code": r["error_code"],
            "error_message": r["error_message"],
            "retry_count": r["retry_count"],
            "resolved": r["resolved"],
            "created_at": r["created_at"]
        }
        for r in results
    ]

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data={
            "count": len(failures),
            "failures": failures
        }
    )


@router.post("/failures/{failure_id}/retry", response_model=ApiResponse, summary="重试失败的分析")
async def retry_failed_analysis(failure_id: int, background_tasks: BackgroundTasks):
    result = await failure_storage.retry_failed_analysis(failure_id)

    if not result.get("success"):
        raise BusinessException(
            ErrorCode.PARAM_VALIDATION_ERROR,
            result.get("message", "重试失败")
        )

    if result.get("raw_strain") is not None:
        blade_info = await _validate_unit_and_blade(
            result["unit_id"], result["blade_id"]
        )

        background_tasks.add_task(
            _trigger_analysis_pipeline,
            unit_id=result["unit_id"],
            blade_id=result["blade_id"],
            channel_id=1,
            upload_id=result["upload_id"],
            strain_data=result["raw_strain"].tolist(),
            rpm=3000.0,
            sample_rate=25600,
            start_time=datetime.utcnow(),
            blade_count=92,
            blade_info=blade_info
        )

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data={
            "failure_id": failure_id,
            "retry_triggered": True,
            "upload_id": result.get("upload_id")
        }
    )
