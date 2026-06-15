import logging
from datetime import datetime
from typing import Optional, Dict, Any

import numpy as np
from fastapi import APIRouter, BackgroundTasks

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
    WaveformAnalysisRequest,
    OrderResamplingResult, OrderSpectrumResult, FatigueDamageResult
)
from app.utils.failure_storage import failure_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/analysis", tags=["数据分析"])


async def _run_analysis_pipeline(
    unit_id: str,
    blade_id: str,
    channel_id: int,
    start_time: datetime,
    end_time: datetime,
    blade_count: int,
    rpm_override: Optional[float] = None,
    options: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    blade_info = await mysql_pool.get_unit_blade_info(unit_id, blade_id)
    if not blade_info:
        raise BusinessException(
            ErrorCode.BLADE_NOT_FOUND,
            f"叶片 {blade_id} 在机组 {unit_id} 中不存在"
        )

    query = """
        SELECT time, strain_values, sample_rate, rpm, channel_id
        FROM strain_waveforms
        WHERE unit_id = $1 AND blade_id = $2
          AND time >= $3 AND time <= $4
        ORDER BY time
    """
    waveform_records = await tsdb_pool.fetch(
        query, unit_id, blade_id, start_time, end_time
    )

    if not waveform_records:
        raise BusinessException(
            ErrorCode.WAVEFORM_EMPTY,
            f"在指定时间范围内未找到波形数据"
        )

    all_strain = []
    sample_rate = waveform_records[0]["sample_rate"]
    base_rpm = rpm_override if rpm_override else waveform_records[0]["rpm"]
    start_timestamp = waveform_records[0]["time"]

    for record in waveform_records:
        all_strain.extend(record["strain_values"])

    if len(all_strain) == 0:
        raise BusinessException(ErrorCode.WAVEFORM_EMPTY, "波形数据为空")

    try:
        strain_array = waveform_io.detrend_waveform(
            all_strain, method="linear"
        )

        num_samples = len(strain_array)
        rpm_array = order_resampler.compute_rpm_profile(
            rpm_value=base_rpm,
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

        job_id = f"analysis_{unit_id}_{blade_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        order_resampling_data = {
            "base_order": resample_result["base_order"],
            "order_values": resample_result["fft_order_axis"] if "fft_order_axis" in resample_result else resample_result["order_values"],
            "amplitude_values": resample_result["amplitude_values"],
            "phase_values": resample_result["phase_values"],
            "rpm_range": resample_result["rpm_range"],
            "analysis_window_seconds": resample_result["analysis_window_seconds"]
        }

        return {
            "job_id": job_id,
            "unit_id": unit_id,
            "blade_id": blade_id,
            "channel_id": channel_id,
            "sample_count": num_samples,
            "sample_rate": sample_rate,
            "rpm": base_rpm,
            "blade_count": blade_count,
            "order_resampling": OrderResamplingResult(**order_resampling_data),
            "spectral_decomposition": OrderSpectrumResult(**decompose_result),
            "fatigue_damage": FatigueDamageResult(**fatigue_result),
            "analysis_time": datetime.utcnow()
        }

    except BusinessException:
        raise
    except Exception as e:
        logger.error(f"Analysis pipeline failed: {e}")
        import traceback
        stack_trace = traceback.format_exc()

        upload_id = f"manual_{unit_id}_{blade_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        await failure_storage.save_analysis_failure(
            unit_id=unit_id,
            blade_id=blade_id,
            upload_id=upload_id,
            error_code=getattr(e, "code", ErrorCode.INTERNAL_SERVER_ERROR),
            error_message=str(e),
            raw_strain=all_strain if isinstance(all_strain, list) else None,
            raw_rpm=None,
            algorithm_params={
                "blade_count": blade_count,
                "sample_rate": sample_rate,
                "rpm": base_rpm,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat()
            },
            stack_trace=stack_trace,
            timestamp=start_timestamp
        )
        raise BusinessException(
            ErrorCode.ANALYSIS_FAILED,
            f"分析失败: {str(e)}"
        )


@router.post("/waveform", response_model=ApiResponse, summary="手动触发波形分析")
async def analyze_waveform(
    request: WaveformAnalysisRequest,
    background_tasks: BackgroundTasks
):
    unit = await mysql_pool.get_unit_info(request.unit_id)
    if not unit:
        raise BusinessException(ErrorCode.UNIT_NOT_FOUND, f"机组 {request.unit_id} 不存在")

    blade = await mysql_pool.get_blade_info(request.blade_id)
    if not blade or blade["unit_id"] != request.unit_id:
        raise BusinessException(
            ErrorCode.BLADE_NOT_FOUND,
            f"叶片 {request.blade_id} 不属于机组 {request.unit_id}"
        )

    if request.end_time <= request.start_time:
        raise BusinessException(
            ErrorCode.PARAM_VALIDATION_ERROR,
            "结束时间必须晚于开始时间"
        )

    job_id = f"job_{request.unit_id}_{request.blade_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    background_tasks.add_task(
        _run_analysis_pipeline,
        unit_id=request.unit_id,
        blade_id=request.blade_id,
        channel_id=request.channel_id,
        start_time=request.start_time,
        end_time=request.end_time,
        blade_count=request.blade_count,
        rpm_override=request.rpm,
        options=request.options
    )

    logger.info(f"Manual analysis triggered: job_id={job_id}, unit={request.unit_id}, blade={request.blade_id}")

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data={
            "job_id": job_id,
            "unit_id": request.unit_id,
            "blade_id": request.blade_id,
            "channel_id": request.channel_id,
            "analysis_triggered": True,
            "estimated_completion_seconds": 30
        }
    )


@router.post("/waveform/sync", response_model=ApiResponse, summary="同步执行波形分析")
async def analyze_waveform_sync(request: WaveformAnalysisRequest):
    unit = await mysql_pool.get_unit_info(request.unit_id)
    if not unit:
        raise BusinessException(ErrorCode.UNIT_NOT_FOUND, f"机组 {request.unit_id} 不存在")

    blade = await mysql_pool.get_blade_info(request.blade_id)
    if not blade or blade["unit_id"] != request.unit_id:
        raise BusinessException(
            ErrorCode.BLADE_NOT_FOUND,
            f"叶片 {request.blade_id} 不属于机组 {request.unit_id}"
        )

    if request.end_time <= request.start_time:
        raise BusinessException(
            ErrorCode.PARAM_VALIDATION_ERROR,
            "结束时间必须晚于开始时间"
        )

    result = await _run_analysis_pipeline(
        unit_id=request.unit_id,
        blade_id=request.blade_id,
        channel_id=request.channel_id,
        start_time=request.start_time,
        end_time=request.end_time,
        blade_count=request.blade_count,
        rpm_override=request.rpm,
        options=request.options
    )

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data=result
    )


@router.get("/algorithms/params", response_model=ApiResponse, summary="获取算法超参数")
async def get_algorithm_params():
    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data={
            "order_resampling": {
                "max_order": config.order_resampling.max_order,
                "samples_per_order": config.order_resampling.samples_per_order,
                "interpolation_method": config.order_resampling.interpolation_method,
                "filter_order": config.order_resampling.filter_order,
                "cutoff_ratio": config.order_resampling.cutoff_ratio
            },
            "spectral_decomposition": {
                "fft_window": config.spectral_decomposition.fft_window,
                "overlap_ratio": config.spectral_decomposition.overlap_ratio,
                "peak_prominence": config.spectral_decomposition.peak_prominence,
                "peak_distance_orders": config.spectral_decomposition.peak_distance_orders,
                "noise_floor_percentile": config.spectral_decomposition.noise_floor_percentile,
                "snr_threshold_db": config.spectral_decomposition.snr_threshold_db,
                "sideband_tolerance": config.spectral_decomposition.sideband_tolerance
            },
            "fatigue_damage": {
                "mean_stress_correction": config.fatigue_damage.mean_stress_correction,
                "sn_curve_slope": config.fatigue_damage.sn_curve_slope,
                "sn_curve_intercept": config.fatigue_damage.sn_curve_intercept,
                "fatigue_limit_stress_mpa": config.fatigue_damage.fatigue_limit_stress,
                "ultimate_tensile_strength_mpa": config.fatigue_damage.ultimate_tensile_strength,
                "yield_strength_mpa": config.fatigue_damage.yield_strength
            }
        }
    )


@router.get("/health", response_model=ApiResponse, summary="分析服务健康检查")
async def analysis_health_check():
    try:
        db_ok = True
        try:
            await tsdb_pool.fetchrow("SELECT 1")
        except Exception as e:
            logger.warning(f"TimescaleDB health check failed: {e}")
            db_ok = False

        try:
            await mysql_pool.fetch_one("SELECT 1")
        except Exception as e:
            logger.warning(f"MySQL health check failed: {e}")
            db_ok = False

        return ApiResponse(
            code=ErrorCode.SUCCESS,
            message="success",
            status=ResponseStatus.SUCCESS,
            data={
                "service": "analysis",
                "status": "healthy" if db_ok else "degraded",
                "database_connection": db_ok,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    except Exception as e:
        return ApiResponse(
            code=ErrorCode.INTERNAL_SERVER_ERROR,
            message=f"健康检查失败: {str(e)}",
            status=ResponseStatus.ERROR,
            data=None
        )
