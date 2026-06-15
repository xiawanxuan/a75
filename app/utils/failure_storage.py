import os
import json
import logging
import pickle
import gzip
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

import numpy as np

from app.config import config
from app.database.timescaledb import tsdb_pool
from app.error_codes import ErrorCode

logger = logging.getLogger(__name__)


class FailureDataStorage:
    def __init__(self):
        self.backup_enabled = config.ingestion.backup_failed_data
        self.backup_dir = Path(config.ingestion.backup_directory)
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            raw_dir = self.backup_dir / "raw"
            meta_dir = self.backup_dir / "metadata"
            raw_dir.mkdir(parents=True, exist_ok=True)
            meta_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create backup directories: {e}")

    def _generate_backup_path(
        self,
        upload_id: str,
        unit_id: str,
        data_type: str
    ) -> Tuple[Path, Path]:
        date_str = datetime.now().strftime("%Y%m%d")
        raw_filename = f"{upload_id}_{data_type}.npy.gz"
        meta_filename = f"{upload_id}_meta.json"

        raw_path = self.backup_dir / "raw" / date_str / raw_filename
        meta_path = self.backup_dir / "metadata" / date_str / meta_filename

        (self.backup_dir / "raw" / date_str).mkdir(parents=True, exist_ok=True)
        (self.backup_dir / "metadata" / date_str).mkdir(parents=True, exist_ok=True)

        return raw_path, meta_path

    async def save_analysis_failure(
        self,
        unit_id: str,
        blade_id: str,
        upload_id: str,
        error_code: int,
        error_message: str,
        raw_strain: Optional[np.ndarray] = None,
        raw_rpm: Optional[np.ndarray] = None,
        algorithm_params: Optional[Dict[str, Any]] = None,
        stack_trace: Optional[str] = None,
        timestamp: Optional[datetime] = None
    ) -> int:
        if timestamp is None:
            timestamp = datetime.utcnow()

        failure_data = {
            "time": timestamp,
            "unit_id": unit_id,
            "blade_id": blade_id,
            "upload_id": upload_id,
            "error_code": error_code,
            "error_message": error_message,
            "stack_trace": stack_trace,
            "raw_strain_data": None,
            "raw_rpm_data": None,
            "algorithm_params": algorithm_params,
        }

        if self.backup_enabled:
            try:
                if raw_strain is not None:
                    strain_path, meta_path = self._generate_backup_path(
                        upload_id, unit_id, "strain"
                    )
                    with gzip.open(strain_path, "wb") as f:
                        np.save(f, raw_strain.astype(np.float32))
                    failure_data["raw_strain_data"] = str(strain_path).encode()

                if raw_rpm is not None:
                    rpm_path, _ = self._generate_backup_path(
                        upload_id, unit_id, "rpm"
                    )
                    with gzip.open(rpm_path, "wb") as f:
                        np.save(f, raw_rpm.astype(np.float64))
                    failure_data["raw_rpm_data"] = str(rpm_path).encode()

                meta_data = {
                    "upload_id": upload_id,
                    "unit_id": unit_id,
                    "blade_id": blade_id,
                    "error_code": error_code,
                    "error_message": error_message,
                    "timestamp": timestamp.isoformat(),
                    "strain_shape": list(raw_strain.shape) if raw_strain is not None else None,
                    "rpm_shape": list(raw_rpm.shape) if raw_rpm is not None else None,
                    "algorithm_params": algorithm_params,
                }
                _, meta_path = self._generate_backup_path(upload_id, unit_id, "meta")
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta_data, f, ensure_ascii=False, indent=2)

            except Exception as e:
                logger.error(f"Failed to save backup files: {e}")

        try:
            failure_id = await tsdb_pool.save_analysis_failure(failure_data)
            logger.info(
                f"Saved analysis failure: upload_id={upload_id}, "
                f"error_code={error_code}, failure_id={failure_id}"
            )
            return failure_id
        except Exception as e:
            logger.error(f"Failed to save failure to database: {e}")
            return 0

    def load_raw_data(
        self,
        file_path: str
    ) -> Optional[np.ndarray]:
        try:
            path = Path(file_path)
            if not path.exists():
                logger.error(f"Backup file not found: {file_path}")
                return None

            if path.suffix == ".gz":
                with gzip.open(path, "rb") as f:
                    return np.load(f)
            elif path.suffix == ".npy":
                return np.load(path)
            else:
                logger.error(f"Unsupported file format: {path.suffix}")
                return None
        except Exception as e:
            logger.error(f"Failed to load raw data from {file_path}: {e}")
            return None

    def list_failed_backups(
        self,
        unit_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        error_code: Optional[int] = None
    ) -> list:
        try:
            meta_dir = self.backup_dir / "metadata"
            if not meta_dir.exists():
                return []

            failures = []
            for date_dir in sorted(meta_dir.iterdir()):
                if not date_dir.is_dir():
                    continue

                if start_date and date_dir.name < start_date.strftime("%Y%m%d"):
                    continue
                if end_date and date_dir.name > end_date.strftime("%Y%m%d"):
                    continue

                for meta_file in date_dir.glob("*_meta.json"):
                    try:
                        with open(meta_file, "r", encoding="utf-8") as f:
                            meta = json.load(f)

                        if unit_id and meta.get("unit_id") != unit_id:
                            continue
                        if error_code and meta.get("error_code") != error_code:
                            continue

                        failures.append(meta)
                    except Exception as e:
                        logger.warning(f"Failed to read meta file {meta_file}: {e}")

            return failures
        except Exception as e:
            logger.error(f"Failed to list failed backups: {e}")
            return []

    async def mark_resolved(
        self,
        failure_id: int,
        resolved_note: Optional[str] = None
    ) -> bool:
        try:
            query = """
                UPDATE analysis_failures
                SET resolved = TRUE,
                    resolved_at = NOW(),
                    error_message = CONCAT(error_message, $1)
                WHERE failure_id = $2
            """
            note = f" [RESOLVED: {resolved_note}]" if resolved_note else " [RESOLVED]"
            result = await tsdb_pool.execute(query, note, failure_id)
            return "UPDATE" in result
        except Exception as e:
            logger.error(f"Failed to mark failure as resolved: {e}")
            return False

    async def retry_failed_analysis(
        self,
        failure_id: int
    ) -> Dict[str, Any]:
        try:
            query = """
                SELECT * FROM analysis_failures
                WHERE failure_id = $1
            """
            failure = await tsdb_pool.fetchrow(query, failure_id)

            if not failure:
                return {"success": False, "message": "Failure record not found"}

            raw_strain = None
            raw_rpm = None

            if failure["raw_strain_data"]:
                raw_strain = self.load_raw_data(failure["raw_strain_data"].decode())
            if failure["raw_rpm_data"]:
                raw_rpm = self.load_raw_data(failure["raw_rpm_data"].decode())

            await tsdb_pool.execute(
                """
                UPDATE analysis_failures
                SET retry_count = retry_count + 1,
                    last_retry_time = NOW()
                WHERE failure_id = $1
                """,
                failure_id
            )

            return {
                "success": True,
                "failure_id": failure_id,
                "unit_id": failure["unit_id"],
                "blade_id": failure["blade_id"],
                "upload_id": failure["upload_id"],
                "error_code": failure["error_code"],
                "raw_strain": raw_strain,
                "raw_rpm": raw_rpm,
                "algorithm_params": failure["algorithm_params"],
            }

        except Exception as e:
            logger.error(f"Failed to retry analysis: {e}")
            return {"success": False, "message": str(e)}


failure_storage = FailureDataStorage()
