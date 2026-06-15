import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, List, Dict
from datetime import datetime

import asyncpg
from app.config import config
from app.error_codes import ErrorCode, BusinessException

logger = logging.getLogger(__name__)


class TimescaleDBPool:
    def __init__(self):
        self._pool = None
        self._config = config.timescaledb

    async def initialize(self):
        if self._pool is None or self._pool.is_closing():
            logger.info(f"Initializing TimescaleDB pool: {self._config.host}:{self._config.port}/{self._config.database}")
            try:
                self._pool = await asyncpg.create_pool(
                    host=self._config.host,
                    port=self._config.port,
                    user=self._config.user,
                    password=self._config.password,
                    database=self._config.database,
                    max_size=self._config.max_connections,
                    min_size=self._config.min_connections,
                    command_timeout=self._config.command_timeout / 1000.0,
                )
                logger.info("TimescaleDB pool initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize TimescaleDB pool: {e}")
                raise BusinessException(
                    ErrorCode.DATABASE_CONNECTION_ERROR,
                    f"TimescaleDB connection failed: {str(e)}"
                )

    async def close(self):
        if self._pool and not self._pool.is_closing():
            await self._pool.close()
            logger.info("TimescaleDB pool closed")

    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator[asyncpg.Connection, None]:
        await self.initialize()
        try:
            async with self._pool.acquire() as conn:
                yield conn
        except asyncpg.exceptions.PostgresConnectionError as e:
            logger.error(f"TimescaleDB connection error: {e}")
            raise BusinessException(
                ErrorCode.DATABASE_CONNECTION_ERROR,
                f"Connection error: {str(e)}"
            )
        except asyncpg.exceptions.PostgresError as e:
            logger.error(f"TimescaleDB error: {e}")
            raise BusinessException(
                ErrorCode.DATABASE_QUERY_ERROR,
                f"Query error: {str(e)}"
            )

    async def execute(self, query: str, *args: Any) -> str:
        async with self.get_connection() as conn:
            try:
                return await conn.execute(query, *args)
            except asyncpg.exceptions.PostgresError as e:
                logger.error(f"Query execution failed: {e}, query: {query}")
                raise BusinessException(
                    ErrorCode.DATABASE_INSERT_ERROR,
                    f"Execution error: {str(e)}"
                )

    async def fetch(self, query: str, *args: Any) -> List[asyncpg.Record]:
        async with self.get_connection() as conn:
            try:
                return await conn.fetch(query, *args)
            except asyncpg.exceptions.PostgresError as e:
                logger.error(f"Query fetch failed: {e}, query: {query}")
                raise BusinessException(
                    ErrorCode.DATABASE_QUERY_ERROR,
                    f"Fetch error: {str(e)}"
                )

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record:
        async with self.get_connection() as conn:
            try:
                return await conn.fetchrow(query, *args)
            except asyncpg.exceptions.PostgresError as e:
                logger.error(f"Query fetchrow failed: {e}, query: {query}")
                raise BusinessException(
                    ErrorCode.DATABASE_QUERY_ERROR,
                    f"Fetchrow error: {str(e)}"
                )

    async def insert_strain_waveforms_batch(self, records: List[Dict[str, Any]]) -> int:
        if not records:
            return 0

        query = """
            INSERT INTO strain_waveforms (
                time, unit_id, blade_id, channel_id, sample_rate,
                rpm, strain_values, sample_count, shard_id, upload_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (time, unit_id, blade_id, channel_id) DO NOTHING
        """

        async with self.get_connection() as conn:
            try:
                results = await asyncio.gather(
                    *[conn.execute(query, *[
                        r["time"], r["unit_id"], r["blade_id"], r["channel_id"],
                        r["sample_rate"], r["rpm"], r["strain_values"],
                        r["sample_count"], r["shard_id"], r["upload_id"]
                    ]) for r in records],
                    return_exceptions=True
                )

                success_count = sum(
                    1 for r in results
                    if not isinstance(r, Exception)
                )

                if success_count < len(records):
                    exceptions = [
                        str(r) for r in results if isinstance(r, Exception)
                    ]
                    logger.warning(
                        f"Batch insert partial failure: {success_count}/{len(records)} "
                        f"success. Errors: {exceptions[:3]}"
                    )

                return success_count

            except Exception as e:
                logger.error(f"Batch insert failed: {e}")
                raise BusinessException(
                    ErrorCode.DATABASE_BATCH_INSERT_ERROR,
                    f"Batch insert error: {str(e)}"
                )

    async def batch_insert(self, table: str, columns: List[str],
                         records: List[List[Any]]) -> int:
        if not records:
            return 0

        placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
        column_names = ", ".join(columns)
        query = f"""
            INSERT INTO {table} ({column_names})
            VALUES ({placeholders})
            ON CONFLICT DO NOTHING
        """

        async with self.get_connection() as conn:
            try:
                statement = await conn.prepare(query)
                results = await statement.executemany(
                    records,
                    timeout=300.0
                )
                return len(records)
            except Exception as e:
                logger.error(f"Batch insert into {table} failed: {e}")
                raise BusinessException(
                    ErrorCode.DATABASE_BATCH_INSERT_ERROR,
                    f"Batch insert error: {str(e)}"
                )

    async def save_analysis_failure(self, failure_data: Dict[str, Any]) -> int:
        query = """
            INSERT INTO analysis_failures (
                time, unit_id, blade_id, upload_id, error_code,
                error_message, stack_trace, raw_strain_data,
                raw_rpm_data, algorithm_params
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING failure_id
        """

        async with self.get_connection() as conn:
            try:
                result = await conn.fetchrow(
                    query,
                    failure_data["time"],
                    failure_data["unit_id"],
                    failure_data["blade_id"],
                    failure_data["upload_id"],
                    failure_data["error_code"],
                    failure_data["error_message"],
                    failure_data.get("stack_trace"),
                    failure_data.get("raw_strain_data"),
                    failure_data.get("raw_rpm_data"),
                    failure_data.get("algorithm_params"),
                )
                return result["failure_id"]
            except Exception as e:
                logger.error(f"Failed to save analysis failure: {e}")
                return 0


tsdb_pool = TimescaleDBPool()
