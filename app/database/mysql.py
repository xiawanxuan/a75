import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, Optional

import aiomysql
from sqlalchemy import MetaData
from app.config import config
from app.error_codes import ErrorCode, BusinessException

logger = logging.getLogger(__name__)

metadata = MetaData()


class MySQLPool:
    def __init__(self):
        self._pool = None
        self._config = config.mysql

    async def initialize(self):
        if self._pool is None:
            logger.info(f"Initializing MySQL pool: {self._config.host}:{self._config.port}/{self._config.database}")
            try:
                self._pool = await aiomysql.create_pool(
                    host=self._config.host,
                    port=self._config.port,
                    user=self._config.user,
                    password=self._config.password,
                    db=self._config.database,
                    maxsize=self._config.max_connections,
                    minsize=self._config.min_connections,
                    charset="utf8mb4",
                    cursorclass=aiomysql.DictCursor,
                    autocommit=False,
                )
                logger.info("MySQL pool initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize MySQL pool: {e}")
                raise BusinessException(
                    ErrorCode.DATABASE_CONNECTION_ERROR,
                    f"MySQL connection failed: {str(e)}"
                )

    async def close(self):
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            logger.info("MySQL pool closed")

    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator[aiomysql.Connection, None]:
        await self.initialize()
        try:
            async with self._pool.acquire() as conn:
                yield conn
        except aiomysql.OperationalError as e:
            logger.error(f"MySQL connection error: {e}")
            raise BusinessException(
                ErrorCode.DATABASE_CONNECTION_ERROR,
                f"Connection error: {str(e)}"
            )
        except aiomysql.Error as e:
            logger.error(f"MySQL error: {e}")
            raise BusinessException(
                ErrorCode.DATABASE_QUERY_ERROR,
                f"Query error: {str(e)}"
            )

    async def execute(self, query: str, args: tuple = None) -> int:
        async with self.get_connection() as conn:
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, args or ())
                    await conn.commit()
                    return cursor.rowcount
            except aiomysql.Error as e:
                await conn.rollback()
                logger.error(f"Query execution failed: {e}, query: {query}")
                raise BusinessException(
                    ErrorCode.DATABASE_INSERT_ERROR,
                    f"Execution error: {str(e)}"
                )

    async def fetch_all(self, query: str, args: tuple = None) -> List[Dict[str, Any]]:
        async with self.get_connection() as conn:
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, args or ())
                    return await cursor.fetchall()
            except aiomysql.Error as e:
                logger.error(f"Query fetch failed: {e}, query: {query}")
                raise BusinessException(
                    ErrorCode.DATABASE_QUERY_ERROR,
                    f"Fetch error: {str(e)}"
                )

    async def fetch_one(self, query: str, args: tuple = None) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, args or ())
                    return await cursor.fetchone()
            except aiomysql.Error as e:
                logger.error(f"Query fetchone failed: {e}, query: {query}")
                raise BusinessException(
                    ErrorCode.DATABASE_QUERY_ERROR,
                    f"Fetchone error: {str(e)}"
                )

    async def get_unit_info(self, unit_id: str) -> Optional[Dict[str, Any]]:
        query = """
            SELECT u.*, p.plant_name, p.plant_id
            FROM units u
            JOIN power_plants p ON u.plant_id = p.plant_id
            WHERE u.unit_id = %s AND u.status = 1
        """
        return await self.fetch_one(query, (unit_id,))

    async def get_blade_info(self, blade_id: str) -> Optional[Dict[str, Any]]:
        query = """
            SELECT b.*, u.unit_name, u.rated_rpm
            FROM blades b
            JOIN units u ON b.unit_id = u.unit_id
            WHERE b.blade_id = %s AND b.status = 1
        """
        return await self.fetch_one(query, (blade_id,))

    async def get_blades_by_unit(self, unit_id: str) -> List[Dict[str, Any]]:
        query = """
            SELECT * FROM blades
            WHERE unit_id = %s AND status = 1
            ORDER BY stage, blade_number
        """
        return await self.fetch_all(query, (unit_id,))

    async def get_unit_blade_info(self, unit_id: str, blade_id: str) -> Optional[Dict[str, Any]]:
        query = """
            SELECT b.*, u.rated_rpm, u.unit_type, m.elastic_modulus_gpa, m.sn_slope, m.sn_intercept
            FROM blades b
            JOIN units u ON b.unit_id = u.unit_id
            LEFT JOIN material_sn_curves m ON b.material = m.material AND m.temperature_c = 600
            WHERE b.blade_id = %s AND b.unit_id = %s AND b.status = 1
            LIMIT 1
        """
        return await self.fetch_one(query, (blade_id, unit_id))

    async def get_channel_info(self, channel_id: int) -> Optional[Dict[str, Any]]:
        query = """
            SELECT mc.*, b.blade_id, b.material
            FROM measurement_channels mc
            JOIN blades b ON mc.blade_id = b.blade_id
            WHERE mc.channel_id = %s AND mc.status = 1
        """
        return await self.fetch_one(query, (channel_id,))

    async def get_material_sn_params(self, material: str, temperature: int) -> Optional[Dict[str, Any]]:
        query = """
            SELECT * FROM material_sn_curves
            WHERE material = %s
            ORDER BY ABS(temperature_c - %s)
            LIMIT 1
        """
        return await self.fetch_one(query, (material, temperature))

    async def get_all_units(self) -> List[Dict[str, Any]]]:
        query = """
            SELECT u.*, p.plant_name
            FROM units u
            JOIN power_plants p ON u.plant_id = p.plant_id
            WHERE u.status = 1
            ORDER BY p.plant_name, u.unit_name
        """
        return await self.fetch_all(query)

    async def update_upload_task_progress(self, task_id: str, size_mb: float) -> None:
        query = """
            UPDATE data_collection_tasks
            SET upload_count = upload_count + 1,
                total_size_mb = total_size_mb + %s,
                status = CASE
                    WHEN status = 0 THEN 1
                    ELSE status
                END,
                actual_start = CASE
                    WHEN actual_start IS NULL THEN NOW()
                    ELSE actual_start
                END
            WHERE task_id = %s
        """
        await self.execute(query, (size_mb, task_id))

    async def complete_task(self, task_id: str) -> None:
        query = """
            UPDATE data_collection_tasks
            SET status = 2, actual_end = NOW()
            WHERE task_id = %s
        """
        await self.execute(query, (task_id,))


mysql_pool = MySQLPool()
