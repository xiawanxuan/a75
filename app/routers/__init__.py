from app.routers.ingestion_router import router as ingestion_router
from app.routers.analysis_router import router as analysis_router
from app.routers.query_router import router as query_router

__all__ = [
    "ingestion_router",
    "analysis_router",
    "query_router",
]
