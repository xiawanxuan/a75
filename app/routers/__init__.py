from app.routers.ingestion_router import router as ingestion_router
from app.routers.analysis_router import router as analysis_router
from app.routers.query_router import router as query_router
from app.routers.callback_router import router as callback_router

__all__ = [
    "ingestion_router",
    "analysis_router",
    "query_router",
    "callback_router",
]
