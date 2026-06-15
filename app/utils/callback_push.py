import logging
import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

import aiohttp
import numpy as np

from app.config import config
from app.error_codes import ErrorCode, BusinessException
from app.database.timescaledb import tsdb_pool

logger = logging.getLogger(__name__)


@dataclass
class ResonanceAlert:
    unit_id: str
    blade_id: str
    channel_id: int
    analysis_time: datetime
    base_order: float
    resonance_orders: List[float]
    resonance_amplitudes: List[float]
    snr: float
    rpm_range: List[float]
    avg_rpm: float
    max_damage: float
    damage_accumulated: float
    remaining_life_hours: float
    max_stress: float
    stress_amplitude: float
    cycle_count: int
    spectral_centroid: float
    spectral_bandwidth: float
    noise_floor: float
    harmonic_orders: List[float]
    harmonic_amplitudes: List[float]
    sideband_orders: List[float]
    sideband_amplitudes: List[float]
    threshold_exceeded: List[Dict[str, Any]] = field(default_factory=list)
    blade_number: Optional[int] = None
    stage: Optional[int] = None
    blade_type: Optional[str] = None
    material: Optional[str] = None
    unit_name: Optional[str] = None
    plant_name: Optional[str] = None
    location_mm: Optional[float] = None
    angle_deg: Optional[float] = None


@dataclass
class CallbackTarget:
    name: str
    url: str
    secret: str
    enabled: bool = True
    timeout: int = 10
    retry_max: int = 3
    retry_backoff: float = 2.0


@dataclass
class CallbackResult:
    success: bool
    status_code: Optional[int] = None
    response_text: Optional[str] = None
    error: Optional[str] = None
    retry_count: int = 0


class CallbackPushService:
    def __init__(self):
        self.callback_config = config.callback
        self.session: Optional[aiohttp.ClientSession] = None
        self._targets: Dict[str, CallbackTarget] = {}
        self._load_targets()

    def _load_targets(self):
        for target in self.callback_config.targets:
            self._targets[target["name"]] = CallbackTarget(
                name=target["name"],
                url=target["url"],
                secret=target["secret"],
                enabled=target.get("enabled", True),
                timeout=target.get("timeout", 10),
                retry_max=target.get("retry_max", 3),
                retry_backoff=target.get("retry_backoff", 2.0)
            )

    def _ensure_session(self):
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(
                limit=self.callback_config.max_concurrent,
                ttl_dns_cache=300
            )
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout
            )

    def _generate_signature(self, payload: Dict[str, Any], secret: str) -> str:
        payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        signature = hmac.new(
            secret.encode("utf-8"),
            payload_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _build_payload(
        self,
        alert: ResonanceAlert,
        event_id: str,
        timestamp: datetime
    ) -> Dict[str, Any]:
        payload = {
            "event_id": event_id,
            "event_type": "resonance_damage_alert",
            "timestamp": timestamp.isoformat(),
            "source": "turbine_diagnosis_platform",
            "version": "1.0",
            "plant": {
                "plant_name": alert.plant_name
            },
            "unit": {
                "unit_id": alert.unit_id,
                "unit_name": alert.unit_name,
                "avg_rpm": alert.avg_rpm,
                "rpm_range": alert.rpm_range
            },
            "blade": {
                "blade_id": alert.blade_id,
                "blade_number": alert.blade_number,
                "stage": alert.stage,
                "blade_type": alert.blade_type,
                "material": alert.material
            },
            "channel": {
                "channel_id": alert.channel_id,
                "location_mm": alert.location_mm,
                "angle_deg": alert.angle_deg
            },
            "resonance": {
                "base_order": alert.base_order,
                "resonance_orders": alert.resonance_orders,
                "resonance_amplitudes": alert.resonance_amplitudes,
                "harmonic_orders": alert.harmonic_orders,
                "harmonic_amplitudes": alert.harmonic_amplitudes,
                "sideband_orders": alert.sideband_orders,
                "sideband_amplitudes": alert.sideband_amplitudes,
                "spectral_centroid": alert.spectral_centroid,
                "spectral_bandwidth": alert.spectral_bandwidth,
                "noise_floor": alert.noise_floor,
                "snr": alert.snr
            },
            "fatigue_damage": {
                "max_damage": alert.max_damage,
                "damage_accumulated": alert.damage_accumulated,
                "remaining_life_hours": alert.remaining_life_hours,
                "max_stress": alert.max_stress,
                "stress_amplitude": alert.stress_amplitude,
                "cycle_count": alert.cycle_count
            },
            "threshold_exceeded": alert.threshold_exceeded,
            "analysis_time": alert.analysis_time.isoformat()
        }
        return payload

    async def _push_single_target(
        self,
        target: CallbackTarget,
        payload: Dict[str, Any],
        event_id: str
    ) -> CallbackResult:
        if not target.enabled:
            return CallbackResult(success=True, response_text="target_disabled")

        self._ensure_session()

        timestamp = datetime.utcnow()
        signature = self._generate_signature(payload, target.secret)

        headers = {
            "Content-Type": "application/json",
            "X-Event-ID": event_id,
            "X-Timestamp": timestamp.isoformat(),
            "X-Signature": f"sha256={signature}",
            "X-Source": "turbine_diagnosis_platform"
        }

        result = CallbackResult(success=False, retry_count=0)

        for attempt in range(target.retry_max):
            try:
                async with self.session.post(
                    target.url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=target.timeout)
                ) as response:
                    result.status_code = response.status
                    result.response_text = await response.text()
                    result.retry_count = attempt + 1

                    if 200 <= response.status < 300:
                        result.success = True
                        logger.info(
                            f"Callback success: target={target.name}, "
                            f"event={event_id}, status={response.status}, "
                            f"attempt={attempt + 1}/{target.retry_max}"
                        )
                        return result
                    else:
                        logger.warning(
                            f"Callback failed: target={target.name}, "
                            f"event={event_id}, status={response.status}, "
                            f"attempt={attempt + 1}/{target.retry_max}, "
                            f"response={result.response_text[:200]}"
                        )

            except asyncio.TimeoutError:
                result.error = "timeout"
                logger.warning(
                    f"Callback timeout: target={target.name}, "
                    f"event={event_id}, attempt={attempt + 1}/{target.retry_max}"
                )
            except aiohttp.ClientError as e:
                result.error = str(e)
                logger.warning(
                    f"Callback error: target={target.name}, "
                    f"event={event_id}, error={e}, "
                    f"attempt={attempt + 1}/{target.retry_max}"
                )
            except Exception as e:
                result.error = str(e)
                logger.error(
                    f"Callback unexpected error: target={target.name}, "
                    f"event={event_id}, error={e}"
                )

            if attempt < target.retry_max - 1:
                wait_time = target.retry_backoff ** attempt
                await asyncio.sleep(wait_time)

        logger.error(
            f"Callback finally failed: target={target.name}, "
            f"event={event_id}, error={result.error}"
        )
        return result

    async def push_resonance_alert(
        self,
        alert: ResonanceAlert,
        enabled_targets: Optional[List[str]] = None
    ) -> Dict[str, CallbackResult]:
        if not self.callback_config.enabled:
            logger.info("Callback push is disabled in config")
            return {}

        event_id = str(uuid.uuid4())
        timestamp = datetime.utcnow()
        payload = self._build_payload(alert, event_id, timestamp)

        targets_to_push = []
        if enabled_targets:
            for name in enabled_targets:
                if name in self._targets:
                    targets_to_push.append(self._targets[name])
        else:
            targets_to_push = [
                t for t in self._targets.values() if t.enabled
            ]

        if not targets_to_push:
            logger.warning(f"No enabled callback targets for event {event_id}")
            return {}

        results: Dict[str, CallbackResult] = {}

        tasks = []
        for target in targets_to_push:
            task = self._push_single_target(target, payload, event_id)
            tasks.append(task)

        try:
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"Callback gather error: {e}")
            task_results = [e] * len(tasks)

        for target, task_result in zip(targets_to_push, task_results):
            if isinstance(task_result, Exception):
                results[target.name] = CallbackResult(
                    success=False,
                    error=str(task_result)
                )
            else:
                results[target.name] = task_result

        await self._record_callback_result(
            event_id=event_id,
            alert=alert,
            payload=payload,
            timestamp=timestamp,
            results=results
        )

        return results

    async def _record_callback_result(
        self,
        event_id: str,
        alert: ResonanceAlert,
        payload: Dict[str, Any],
        timestamp: datetime,
        results: Dict[str, CallbackResult]
    ):
        try:
            for target_name, result in results.items():
                query = """
                    INSERT INTO callback_push_records (
                        event_id, time, unit_id, blade_id, channel_id,
                        target_name, target_url, success, status_code,
                        response_text, error_message, retry_count,
                        payload, resonance_orders, resonance_amplitudes,
                        max_damage, avg_rpm, created_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                        $13, $14, $15, $16, $17, $18
                    )
                """
                await tsdb_pool.execute(
                    query,
                    event_id,
                    timestamp,
                    alert.unit_id,
                    alert.blade_id,
                    alert.channel_id,
                    target_name,
                    self._targets[target_name].url if target_name in self._targets else "",
                    result.success,
                    result.status_code,
                    result.response_text[:500] if result.response_text else None,
                    result.error,
                    result.retry_count,
                    json.dumps(payload),
                    alert.resonance_orders,
                    alert.resonance_amplitudes,
                    alert.max_damage,
                    alert.avg_rpm,
                    datetime.utcnow()
                )
        except Exception as e:
            logger.error(f"Failed to record callback result: {e}")

    def detect_resonance_exceedance(
        self,
        decompose_result: Dict[str, Any],
        fatigue_result: Dict[str, Any],
        unit_id: str,
        blade_id: str
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        if not self.callback_config.enabled:
            return False, []

        threshold_config = self.callback_config.thresholds
        exceeded = []

        resonance_orders = decompose_result.get("resonance_orders", [])
        resonance_amplitudes = decompose_result.get("resonance_amplitudes", [])
        snr = decompose_result.get("snr", 0.0)
        max_damage = fatigue_result.get("damage_value", 0.0)
        remaining_life = fatigue_result.get("remaining_life_hours", float("inf"))

        for order, amp in zip(resonance_orders, resonance_amplitudes):
            if amp >= threshold_config.resonance_amplitude:
                exceeded.append({
                    "metric": "resonance_amplitude",
                    "order": float(order),
                    "value": float(amp),
                    "threshold": threshold_config.resonance_amplitude,
                    "severity": "high" if amp >= threshold_config.resonance_amplitude * 1.5 else "warning"
                })

        if snr < threshold_config.min_snr:
            exceeded.append({
                "metric": "snr",
                "value": float(snr),
                "threshold": threshold_config.min_snr,
                "severity": "warning"
            })

        if max_damage >= threshold_config.damage_value:
            exceeded.append({
                "metric": "damage_value",
                "value": float(max_damage),
                "threshold": threshold_config.damage_value,
                "severity": "critical" if max_damage >= threshold_config.damage_value * 10 else "high"
            })

        if remaining_life <= threshold_config.remaining_life_hours:
            exceeded.append({
                "metric": "remaining_life_hours",
                "value": float(remaining_life),
                "threshold": threshold_config.remaining_life_hours,
                "severity": "critical" if remaining_life <= threshold_config.remaining_life_hours * 0.1 else "high"
            })

        has_exceedance = len(exceeded) > 0
        return has_exceedance, exceeded

    async def retry_callback(self, event_id: str) -> Optional[Dict[str, CallbackResult]]:
        query = """
            SELECT * FROM callback_push_records
            WHERE event_id = $1
            ORDER BY created_at DESC
            LIMIT 10
        """
        records = await tsdb_pool.fetch(query, event_id)

        if not records:
            raise BusinessException(
                ErrorCode.CALLBACK_EVENT_NOT_FOUND,
                f"Callback event {event_id} not found"
            )

        record = records[0]

        try:
            payload = json.loads(record["payload"])
        except Exception as e:
            logger.error(f"Failed to parse payload for retry: {e}")
            raise BusinessException(
                ErrorCode.CALLBACK_PAYLOAD_INVALID,
                f"Invalid payload for event {event_id}: {e}"
            )

        target_name = record["target_name"]
        if target_name not in self._targets:
            raise BusinessException(
                ErrorCode.CALLBACK_TARGET_NOT_FOUND,
                f"Callback target {target_name} not found"
            )

        target = self._targets[target_name]
        result = await self._push_single_target(target, payload, event_id)

        await self._update_callback_record(event_id, target_name, result)

        return {target_name: result}

    async def _update_callback_record(
        self,
        event_id: str,
        target_name: str,
        result: CallbackResult
    ):
        try:
            query = """
                UPDATE callback_push_records
                SET success = $1, status_code = $2, response_text = $3,
                    error_message = $4, retry_count = retry_count + $5,
                    updated_at = $6
                WHERE event_id = $7 AND target_name = $8
            """
            await tsdb_pool.execute(
                query,
                result.success,
                result.status_code,
                result.response_text[:500] if result.response_text else None,
                result.error,
                result.retry_count,
                datetime.utcnow(),
                event_id,
                target_name
            )
        except Exception as e:
            logger.error(f"Failed to update callback record: {e}")

    async def get_callback_records(
        self,
        unit_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        success: Optional[bool] = None,
        target_name: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        conditions = []
        params = []
        idx = 1

        if unit_id:
            conditions.append(f"unit_id = ${idx}")
            params.append(unit_id)
            idx += 1

        if start_time:
            conditions.append(f"time >= ${idx}")
            params.append(start_time)
            idx += 1

        if end_time:
            conditions.append(f"time <= ${idx}")
            params.append(end_time)
            idx += 1

        if success is not None:
            conditions.append(f"success = ${idx}")
            params.append(success)
            idx += 1

        if target_name:
            conditions.append(f"target_name = ${idx}")
            params.append(target_name)
            idx += 1

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
            SELECT event_id, time, unit_id, blade_id, channel_id,
                   target_name, target_url, success, status_code,
                   error_message, retry_count, resonance_orders,
                   resonance_amplitudes, max_damage, avg_rpm,
                   created_at, updated_at
            FROM callback_push_records
            WHERE {where_clause}
            ORDER BY time DESC
            LIMIT ${idx}
        """
        params.append(limit)

        records = await tsdb_pool.fetch(query, *params)
        return [dict(r) for r in records]

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


callback_service = CallbackPushService()
