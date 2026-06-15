import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Query, BackgroundTasks

from app.config import config
from app.error_codes import ErrorCode, BusinessException
from app.schemas.schemas import ApiResponse, ResponseStatus
from app.utils.callback_push import callback_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/callback", tags=["回调管理"])


class CallbackRecordQueryRequest:
    pass


class CallbackRetryRequest:
    pass


@router.get("/records", response_model=ApiResponse, summary="查询回调推送记录")
async def get_callback_records(
    unit_id: Optional[str] = Query(None, description="机组ID"),
    start_time: Optional[datetime] = Query(None, description="开始时间"),
    end_time: Optional[datetime] = Query(None, description="结束时间"),
    success: Optional[bool] = Query(None, description="是否成功"),
    target_name: Optional[str] = Query(None, description="目标名称"),
    limit: int = Query(100, ge=1, le=1000, description="返回数量限制")
):
    records = await callback_service.get_callback_records(
        unit_id=unit_id,
        start_time=start_time,
        end_time=end_time,
        success=success,
        target_name=target_name,
        limit=limit
    )

    success_count = sum(1 for r in records if r["success"])
    failed_count = len(records) - success_count

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data={
            "count": len(records),
            "success_count": success_count,
            "failed_count": failed_count,
            "records": records
        }
    )


@router.post("/retry/{event_id}", response_model=ApiResponse, summary="重试指定事件的回调推送")
async def retry_callback(
    event_id: str,
    background_tasks: BackgroundTasks
):
    try:
        results = await callback_service.retry_callback(event_id)
        if not results:
            raise BusinessException(
                ErrorCode.CALLBACK_PUSH_FAILED,
                f"回调重试失败: {event_id}"
            )

        target_name = list(results.keys())[0]
        result = results[target_name]

        return ApiResponse(
            code=ErrorCode.SUCCESS,
            message="success",
            status=ResponseStatus.SUCCESS,
            data={
                "event_id": event_id,
                "target_name": target_name,
                "success": result.success,
                "status_code": result.status_code,
                "retry_count": result.retry_count,
                "error": result.error
            }
        )
    except BusinessException:
        raise
    except Exception as e:
        logger.error(f"Retry callback failed for event {event_id}: {e}")
        raise BusinessException(
            ErrorCode.CALLBACK_PUSH_FAILED,
            f"回调重试失败: {str(e)}"
        )


@router.post("/retry-batch", response_model=ApiResponse, summary="批量重试失败的回调推送")
async def retry_failed_callbacks(
    start_time: Optional[datetime] = Query(None, description="开始时间"),
    end_time: Optional[datetime] = Query(None, description="结束时间"),
    unit_id: Optional[str] = Query(None, description="机组ID"),
    max_retry: int = Query(10, ge=1, le=100, description="最大重试数量")
):
    failed_records = await callback_service.get_callback_records(
        unit_id=unit_id,
        start_time=start_time,
        end_time=end_time,
        success=False,
        limit=max_retry
    )

    if not failed_records:
        return ApiResponse(
            code=ErrorCode.SUCCESS,
            message="success",
            status=ResponseStatus.SUCCESS,
            data={
                "message": "没有需要重试的失败回调记录",
                "total": 0,
                "results": []
            }
        )

    results = []
    event_ids = set()
    for record in failed_records:
        event_id = record["event_id"]
        if event_id in event_ids:
            continue
        event_ids.add(event_id)

        try:
            retry_result = await callback_service.retry_callback(event_id)
            if retry_result:
                target_name = list(retry_result.keys())[0]
                res = retry_result[target_name]
                results.append({
                    "event_id": event_id,
                    "target_name": target_name,
                    "success": res.success,
                    "status_code": res.status_code,
                    "error": res.error
                })
        except Exception as e:
            results.append({
                "event_id": event_id,
                "target_name": record["target_name"],
                "success": False,
                "error": str(e)
            })

    success_count = sum(1 for r in results if r["success"])

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data={
            "total": len(results),
            "success_count": success_count,
            "failed_count": len(results) - success_count,
            "results": results
        }
    )


@router.get("/config", response_model=ApiResponse, summary="查询回调推送配置")
async def get_callback_config():
    targets_info = []
    for target in config.callback.targets:
        targets_info.append({
            "name": target["name"],
            "url": target["url"],
            "enabled": target.get("enabled", True),
            "timeout": target.get("timeout", 10),
            "retry_max": target.get("retry_max", 3),
            "retry_backoff": target.get("retry_backoff", 2.0)
        })

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data={
            "enabled": config.callback.enabled,
            "max_concurrent": config.callback.max_concurrent,
            "targets": targets_info,
            "thresholds": {
                "resonance_amplitude": config.callback.thresholds.resonance_amplitude,
                "min_snr": config.callback.thresholds.min_snr,
                "damage_value": config.callback.thresholds.damage_value,
                "remaining_life_hours": config.callback.thresholds.remaining_life_hours
            }
        }
    )


@router.get("/health", response_model=ApiResponse, summary="回调推送服务健康检查")
async def callback_health():
    target_statuses = []
    for target in config.callback.targets:
        target_statuses.append({
            "name": target["name"],
            "enabled": target.get("enabled", True),
            "configured": True
        })

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data={
            "service_enabled": config.callback.enabled,
            "service_available": True,
            "targets_configured": len(config.callback.targets),
            "targets_enabled": sum(1 for t in config.callback.targets if t.get("enabled", True)),
            "target_statuses": target_statuses,
            "timestamp": datetime.utcnow().isoformat()
        }
    )


@router.get("/stats", response_model=ApiResponse, summary="回调推送统计信息")
async def get_callback_stats(
    start_time: Optional[datetime] = Query(None, description="统计开始时间"),
    end_time: Optional[datetime] = Query(None, description="统计结束时间")
):
    if end_time is None:
        end_time = datetime.utcnow()
    if start_time is None:
        start_time = end_time - datetime.timedelta(hours=24)

    all_records = await callback_service.get_callback_records(
        start_time=start_time,
        end_time=end_time,
        limit=10000
    )

    total = len(all_records)
    success_count = sum(1 for r in all_records if r["success"])
    failed_count = total - success_count

    target_stats: Dict[str, Dict[str, Any]] = {}
    for record in all_records:
        target_name = record["target_name"]
        if target_name not in target_stats:
            target_stats[target_name] = {
                "total": 0,
                "success": 0,
                "failed": 0,
                "avg_retry_count": 0.0,
                "total_retry_count": 0
            }
        target_stats[target_name]["total"] += 1
        if record["success"]:
            target_stats[target_name]["success"] += 1
        else:
            target_stats[target_name]["failed"] += 1
        target_stats[target_name]["total_retry_count"] += record.get("retry_count", 0)

    for name in target_stats:
        if target_stats[name]["total"] > 0:
            target_stats[name]["success_rate"] = target_stats[name]["success"] / target_stats[name]["total"]
            target_stats[name]["avg_retry_count"] = target_stats[name]["total_retry_count"] / target_stats[name]["total"]
        else:
            target_stats[name]["success_rate"] = 0.0
        del target_stats[name]["total_retry_count"]

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data={
            "time_range": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat()
            },
            "summary": {
                "total": total,
                "success": success_count,
                "failed": failed_count,
                "success_rate": success_count / total if total > 0 else 0.0
            },
            "by_target": target_stats
        }
    )
