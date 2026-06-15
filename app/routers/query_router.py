import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Query

from app.database.timescaledb import tsdb_pool
from app.database.mysql import mysql_pool
from app.error_codes import ErrorCode, BusinessException
from app.schemas.schemas import (
    ApiResponse, ResponseStatus,
    WaveformQueryRequest, WaveformQueryResponse,
    OrderSpectrumQueryRequest, FatigueDamageQueryRequest,
    UnitInfo, BladeInfo, StatsResponse
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/query", tags=["数据查询"])


@router.get("/units", response_model=ApiResponse, summary="获取所有机组列表")
async def get_all_units():
    units = await mysql_pool.get_all_units()

    unit_list = [
        UnitInfo(
            unit_id=u["unit_id"],
            unit_name=u["unit_name"],
            plant_id=u["plant_id"],
            plant_name=u["plant_name"],
            unit_type=u["unit_type"],
            capacity_mw=float(u["capacity_mw"]),
            rated_rpm=u["rated_rpm"],
            min_rpm=u["min_rpm"],
            max_rpm=u["max_rpm"],
            status=u["status"]
        )
        for u in units
    ]

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data={
            "count": len(unit_list),
            "units": unit_list
        }
    )


@router.get("/units/{unit_id}", response_model=ApiResponse, summary="获取机组详细信息")
async def get_unit_info(unit_id: str):
    unit = await mysql_pool.get_unit_info(unit_id)
    if not unit:
        raise BusinessException(ErrorCode.UNIT_NOT_FOUND, f"机组 {unit_id} 不存在")

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data=UnitInfo(
            unit_id=unit["unit_id"],
            unit_name=unit["unit_name"],
            plant_id=unit["plant_id"],
            plant_name=unit["plant_name"],
            unit_type=unit["unit_type"],
            capacity_mw=float(unit["capacity_mw"]),
            rated_rpm=unit["rated_rpm"],
            min_rpm=unit["min_rpm"],
            max_rpm=unit["max_rpm"],
            status=unit["status"]
        )
    )


@router.get("/units/{unit_id}/blades", response_model=ApiResponse, summary="获取机组叶片列表")
async def get_unit_blades(unit_id: str):
    unit = await mysql_pool.get_unit_info(unit_id)
    if not unit:
        raise BusinessException(ErrorCode.UNIT_NOT_FOUND, f"机组 {unit_id} 不存在")

    blades = await mysql_pool.get_blades_by_unit(unit_id)

    blade_list = [
        BladeInfo(
            blade_id=b["blade_id"],
            unit_id=b["unit_id"],
            blade_number=b["blade_number"],
            stage=b["stage"],
            blade_type=b["blade_type"],
            material=b["material"],
            length_mm=float(b["length_mm"]),
            strain_gauge_count=b["strain_gauge_count"],
            natural_frequency_hz=float(b["natural_frequency_hz"]) if b["natural_frequency_hz"] else None,
            damping_ratio=float(b["damping_ratio"]) if b["damping_ratio"] else None
        )
        for b in blades
    ]

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data={
            "count": len(blade_list),
            "blades": blade_list
        }
    )


@router.get("/blades/{blade_id}", response_model=ApiResponse, summary="获取叶片详细信息")
async def get_blade_info(blade_id: str):
    blade = await mysql_pool.get_blade_info(blade_id)
    if not blade:
        raise BusinessException(ErrorCode.BLADE_NOT_FOUND, f"叶片 {blade_id} 不存在")

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data=BladeInfo(
            blade_id=blade["blade_id"],
            unit_id=blade["unit_id"],
            blade_number=blade["blade_number"],
            stage=blade["stage"],
            blade_type=blade["blade_type"],
            material=blade["material"],
            length_mm=float(blade["length_mm"]),
            strain_gauge_count=blade["strain_gauge_count"],
            natural_frequency_hz=float(blade["natural_frequency_hz"]) if blade["natural_frequency_hz"] else None,
            damping_ratio=float(blade["damping_ratio"]) if blade["damping_ratio"] else None
        )
    )


@router.post("/waveforms", response_model=ApiResponse, summary="查询原始应变波形")
async def query_waveforms(request: WaveformQueryRequest):
    unit = await mysql_pool.get_unit_info(request.unit_id)
    if not unit:
        raise BusinessException(ErrorCode.UNIT_NOT_FOUND, f"机组 {request.unit_id} 不存在")

    conditions = [
        "unit_id = $1",
        "time >= $2",
        "time <= $3"
    ]
    params = [request.unit_id, request.start_time, request.end_time]
    param_index = 4

    if request.blade_id:
        blade = await mysql_pool.get_blade_info(request.blade_id)
        if not blade or blade["unit_id"] != request.unit_id:
            raise BusinessException(
                ErrorCode.BLADE_NOT_FOUND,
                f"叶片 {request.blade_id} 不属于机组 {request.unit_id}"
            )
        conditions.append(f"blade_id = ${param_index}")
        params.append(request.blade_id)
        param_index += 1

    if request.channel_id:
        conditions.append(f"channel_id = ${param_index}")
        params.append(request.channel_id)
        param_index += 1

    if request.rpm_min:
        conditions.append(f"rpm >= ${param_index}")
        params.append(request.rpm_min)
        param_index += 1

    if request.rpm_max:
        conditions.append(f"rpm <= ${param_index}")
        params.append(request.rpm_max)
        param_index += 1

    where_clause = " AND ".join(conditions)
    limit_param = param_index

    query = f"""
        SELECT time, unit_id, blade_id, channel_id, sample_rate,
               rpm, strain_values, sample_count
        FROM strain_waveforms
        WHERE {where_clause}
        ORDER BY time DESC
        LIMIT ${limit_param}
    """
    params.append(request.limit)

    results = await tsdb_pool.fetch(query, *params)

    waveforms = [
        WaveformQueryResponse(
            time=r["time"],
            unit_id=r["unit_id"],
            blade_id=r["blade_id"],
            channel_id=r["channel_id"],
            sample_rate=r["sample_rate"],
            rpm=float(r["rpm"]),
            strain_values=list(r["strain_values"]),
            sample_count=r["sample_count"]
        )
        for r in results
    ]

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data={
            "count": len(waveforms),
            "total_samples": sum(w.sample_count for w in waveforms),
            "waveforms": waveforms
        }
    )


@router.post("/order-spectrum", response_model=ApiResponse, summary="查询阶次谱分析结果")
async def query_order_spectrum(request: OrderSpectrumQueryRequest):
    unit = await mysql_pool.get_unit_info(request.unit_id)
    if not unit:
        raise BusinessException(ErrorCode.UNIT_NOT_FOUND, f"机组 {request.unit_id} 不存在")

    conditions = [
        "os.unit_id = $1",
        "os.time >= $2",
        "os.time <= $3"
    ]
    params = [request.unit_id, request.start_time, request.end_time]
    param_index = 4

    if request.blade_id:
        conditions.append(f"os.blade_id = ${param_index}")
        params.append(request.blade_id)
        param_index += 1

    if request.rpm_min or request.rpm_max:
        conditions.append(f"""
            os.time IN (
                SELECT time FROM strain_waveforms sw
                WHERE sw.unit_id = $1 AND sw.time >= $2 AND sw.time <= $3
        """)
        if request.rpm_min:
            conditions.append(f"AND sw.rpm >= ${param_index}")
            params.append(request.rpm_min)
            param_index += 1
        if request.rpm_max:
            conditions.append(f"AND sw.rpm <= ${param_index}")
            params.append(request.rpm_max)
            param_index += 1
        conditions.append(")")

    where_clause = " AND ".join(conditions)
    limit_param = param_index

    query = f"""
        SELECT os.time, os.unit_id, os.blade_id, os.channel_id,
               os.resonance_orders, os.resonance_amplitudes,
               os.harmonic_orders, os.harmonic_amplitudes,
               os.sideband_orders, os.sideband_amplitudes,
               os.noise_floor, os.snr
        FROM order_spectrum os
        WHERE {where_clause}
        ORDER BY os.time DESC
        LIMIT ${limit_param}
    """
    params.append(request.limit)

    results = await tsdb_pool.fetch(query, *params)

    spectrum_list = [
        {
            "time": r["time"],
            "unit_id": r["unit_id"],
            "blade_id": r["blade_id"],
            "channel_id": r["channel_id"],
            "resonance_orders": list(r["resonance_orders"]),
            "resonance_amplitudes": list(r["resonance_amplitudes"]),
            "harmonic_orders": list(r["harmonic_orders"]),
            "harmonic_amplitudes": list(r["harmonic_amplitudes"]),
            "sideband_orders": list(r["sideband_orders"]),
            "sideband_amplitudes": list(r["sideband_amplitudes"]),
            "noise_floor": float(r["noise_floor"]),
            "snr": float(r["snr"])
        }
        for r in results
    ]

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data={
            "count": len(spectrum_list),
            "spectrums": spectrum_list
        }
    )


@router.post("/fatigue-damage", response_model=ApiResponse, summary="查询疲劳损伤计算结果")
async def query_fatigue_damage(request: FatigueDamageQueryRequest):
    unit = await mysql_pool.get_unit_info(request.unit_id)
    if not unit:
        raise BusinessException(ErrorCode.UNIT_NOT_FOUND, f"机组 {request.unit_id} 不存在")

    aggregation = request.aggregation
    use_cagg = aggregation == "hourly"

    bucket_expr = None
    if aggregation == "hourly":
        bucket_expr = "1 hour"
    elif aggregation == "daily":
        bucket_expr = "1 day"
    elif aggregation == "weekly":
        bucket_expr = "1 week"

    params = [request.unit_id, request.start_time, request.end_time]
    param_index = 4

    conditions = [
        "unit_id = $1",
        "time >= $2",
        "time <= $3"
    ]

    if request.blade_id:
        conditions.append(f"blade_id = ${param_index}")
        params.append(request.blade_id)
        param_index += 1

    if request.min_damage and not aggregation:
        conditions.append(f"damage_value >= ${param_index}")
        params.append(request.min_damage)
        param_index += 1

    where_clause = " AND ".join(conditions)

    if aggregation and bucket_expr:
        if use_cagg:
            cagg_where = [
                "unit_id = $1",
                "bucket >= $2",
                "bucket <= $3"
            ]
            cagg_params = [request.unit_id, request.start_time, request.end_time]
            cagg_idx = 4
            if request.blade_id:
                cagg_where.append(f"blade_id = ${cagg_idx}")
                cagg_params.append(request.blade_id)
                cagg_idx += 1
            if request.min_damage:
                cagg_where.append(f"avg_damage >= ${cagg_idx}")
                cagg_params.append(request.min_damage)
                cagg_idx += 1

            cagg_where_clause = " AND ".join(cagg_where)
            query = f"""
                SELECT bucket as time, unit_id, blade_id,
                       avg_damage, max_damage, total_damage,
                       avg_remaining_life, max_stress, sample_count
                FROM fatigue_damage_hourly
                WHERE {cagg_where_clause}
                ORDER BY bucket DESC
            """
            results = await tsdb_pool.fetch(query, *cagg_params)
        else:
            query = f"""
                SELECT
                    time_bucket('{bucket_expr}', time) AS time,
                    unit_id,
                    blade_id,
                    AVG(damage_value) AS avg_damage,
                    MAX(damage_value) AS max_damage,
                    SUM(damage_value) AS total_damage,
                    AVG(remaining_life) AS avg_remaining_life,
                    MAX(max_stress) AS max_stress,
                    COUNT(*) AS sample_count
                FROM fatigue_damage
                WHERE {where_clause}
                GROUP BY time_bucket('{bucket_expr}', time), unit_id, blade_id
                ORDER BY time DESC
            """
            results = await tsdb_pool.fetch(query, *params)
    else:
        query = f"""
            SELECT time, unit_id, blade_id, channel_id,
                   damage_value, remaining_life, cycle_count,
                   max_stress, min_stress, mean_stress,
                   stress_amplitude, damage_accumulated
            FROM fatigue_damage
            WHERE {where_clause}
            ORDER BY time DESC
            LIMIT ${param_index}
        """
        params.append(10000)
        results = await tsdb_pool.fetch(query, *params)

    damage_list = [dict(r) for r in results]

    total_damage = sum(
        d.get("total_damage", d.get("damage_value", 0))
        for d in damage_list
    )
    max_damage = max(
        (d.get("max_damage", d.get("damage_value", 0)) for d in damage_list),
        default=0
    )

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data={
            "count": len(damage_list),
            "total_damage": total_damage,
            "max_damage": max_damage,
            "aggregation": aggregation if aggregation else "raw",
            "damages": damage_list
        }
    )


@router.get("/stats", response_model=ApiResponse, summary="获取系统统计信息")
async def get_system_stats():
    units = await mysql_pool.get_all_units()

    total_blades = 0
    total_channels = 0
    for unit in units:
        blades = await mysql_pool.get_blades_by_unit(unit["unit_id"])
        total_blades += len(blades)
        for blade in blades:
            channels = await mysql_pool.fetch_all(
                "SELECT COUNT(*) as cnt FROM measurement_channels WHERE blade_id = %s AND status = 1",
                (blade["blade_id"],)
            )
            if channels:
                total_channels += channels[0]["cnt"]

    waveform_count = await tsdb_pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM strain_waveforms"
    )
    spectrum_count = await tsdb_pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM order_spectrum"
    )
    damage_count = await tsdb_pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM fatigue_damage"
    )

    stats = StatsResponse(
        total_units=len(units),
        total_blades=total_blades,
        total_channels=total_channels,
        total_waveform_records=waveform_count["cnt"] if waveform_count else 0,
        total_analysis_records=spectrum_count["cnt"] if spectrum_count else 0,
        total_damage_records=damage_count["cnt"] if damage_count else 0
    )

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data=stats
    )
