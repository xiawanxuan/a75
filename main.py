import logging
import logging.handlers
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.config import config
from app.database.timescaledb import tsdb_pool
from app.database.mysql import mysql_pool
from app.middleware.middleware import RequestLoggingMiddleware
from app.error_codes import ErrorCode, BusinessException
from app.schemas.schemas import ApiResponse, ResponseStatus

from app.routers.ingestion_router import router as ingestion_router
from app.routers.analysis_router import router as analysis_router
from app.routers.query_router import router as query_router
from app.routers.callback_router import router as callback_router


def _setup_logging():
    log_config = config.logging
    os.makedirs(os.path.dirname(log_config.log_file), exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, log_config.level.upper(), logging.INFO),
        format=log_config.format,
        handlers=[
            logging.handlers.RotatingFileHandler(
                log_config.log_file,
                maxBytes=log_config.max_file_size_mb * 1024 * 1024,
                backupCount=log_config.backup_count,
                encoding="utf-8"
            ),
            logging.StreamHandler()
        ]
    )

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting turbine diagnosis platform...")

    try:
        await tsdb_pool.initialize()
        logger.info("TimescaleDB pool initialized")
    except Exception as e:
        logger.warning(f"TimescaleDB initialization failed (degraded mode): {e}")

    try:
        await mysql_pool.initialize()
        logger.info("MySQL pool initialized")
    except Exception as e:
        logger.warning(f"MySQL initialization failed (degraded mode): {e}")

    logger.info(f"Platform started: {config.openapi.title} v{config.openapi.version}")
    yield

    logger.info("Shutting down platform...")
    await tsdb_pool.close()
    await mysql_pool.close()
    logger.info("Platform shutdown complete")


app = FastAPI(
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json"
)

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingestion_router)
app.include_router(analysis_router)
app.include_router(query_router)
app.include_router(callback_router)


@app.get("/", tags=["系统"], summary="平台根路径")
async def root():
    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data={
            "name": config.openapi.title,
            "version": config.openapi.version,
            "description": config.openapi.description,
            "docs": "/api/docs",
            "redoc": "/api/redoc",
            "health": "/api/v1/analysis/health"
        }
    )


@app.get("/api/health", tags=["系统"], summary="全局健康检查")
async def health_check():
    import asyncio
    from datetime import datetime

    results = {}
    overall_healthy = True

    try:
        await tsdb_pool.fetchrow("SELECT 1")
        results["timescaledb"] = "healthy"
    except Exception as e:
        results["timescaledb"] = f"unhealthy: {str(e)}"
        overall_healthy = False

    try:
        await mysql_pool.fetch_one("SELECT 1")
        results["mysql"] = "healthy"
    except Exception as e:
        results["mysql"] = f"unhealthy: {str(e)}"
        overall_healthy = False

    return ApiResponse(
        code=ErrorCode.SUCCESS,
        message="success",
        status=ResponseStatus.SUCCESS,
        data={
            "status": "healthy" if overall_healthy else "degraded",
            "timestamp": datetime.utcnow().isoformat(),
            "version": config.openapi.version,
            "services": results
        }
    )


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=config.openapi.title,
        version=config.openapi.version,
        description=config.openapi.description,
        routes=app.routes,
        contact=config.openapi.contact,
        tags=[
            {
                "name": "系统",
                "description": "平台系统级接口"
            },
            {
                "name": "数据接入",
                "description": "高频应变波形分片上传与数据接入接口"
            },
            {
                "name": "数据分析",
                "description": "转速同步阶次重采样、阶次谱分解、疲劳损伤分析"
            },
            {
                "name": "数据查询",
                "description": "多条件原始波形、阶次结果、损伤特征查询"
            },
            {
                "name": "回调管理",
                "description": "共振损伤异常HTTP回调推送、记录查询、失败重试、配置管理"
            }
        ]
    )

    openapi_schema["components"]["securitySchemes"] = {
        "ApiKeyAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key"
        }
    }

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=config.server.host,
        port=config.server.port,
        workers=config.server.workers,
        log_level=config.logging.level.lower()
    )
