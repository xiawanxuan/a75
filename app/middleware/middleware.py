import time
import uuid
import json
import logging
import traceback
from datetime import datetime
from typing import Callable, Dict, Any

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.concurrency import iterate_in_threadpool

from app.config import config
from app.error_codes import ErrorCode, BusinessException
from app.schemas.schemas import ApiResponse, ResponseStatus

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.log_level = config.logging.level
        self._concurrent_requests = 0
        self._max_concurrent = config.server.max_concurrent_requests

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())
        start_time = time.time()
        request.state.request_id = request_id

        self._concurrent_requests += 1
        try:
            if self._concurrent_requests > self._max_concurrent:
                logger.warning(
                    f"Concurrent limit exceeded: {self._concurrent_requests}/{self._max_concurrent}"
                )
                return self._create_error_response(
                    ErrorCode.CONCURRENT_LIMIT_EXCEEDED,
                    f"Concurrent limit: {self._max_concurrent}",
                    request_id
                )

            await self._log_request(request, request_id)

            response = await call_next(request)

            process_time = (time.time() - start_time) * 1000

            response_body = [section async for section in response.body_iterator]
            response.body_iterator = iterate_in_threadpool(iter(response_body))

            if response.status_code >= 400:
                try:
                    body_content = b"".join(response_body).decode()
                    logger.warning(
                        f"Request completed with error: {request.method} {request.url.path} "
                        f"status={response.status_code} duration={process_time:.2f}ms request_id={request_id}"
                    )
                except Exception:
                    pass

            response.headers["X-Request-ID"] = request_id
            response.headers["X-Process-Time-MS"] = f"{process_time:.2f}"

            return response

        except BusinessException as e:
            process_time = (time.time() - start_time) * 1000
            logger.error(
                f"Business exception: {e.code} {e.message} "
                f"request_id={request_id} duration={process_time:.2f}ms"
            )
            return self._create_error_response(e.code, e.message, request_id, e.details)

        except Exception as e:
            process_time = (time.time() - start_time) * 1000
            stack_trace = traceback.format_exc()
            logger.error(
                f"Unhandled exception: {str(e)} "
                f"request_id={request_id} duration={process_time:.2f}ms\n{stack_trace}"
            )
            return self._create_error_response(
                ErrorCode.INTERNAL_SERVER_ERROR,
                str(e),
                request_id,
                {"stack_trace": stack_trace}
            )
        finally:
            self._concurrent_requests -= 1

    async def _log_request(self, request: Request, request_id: str) -> None:
        try:
            client_ip = request.client.host if request.client else "unknown"
            user_agent = request.headers.get("user-agent", "unknown")
            content_length = request.headers.get("content-length", "0")

            logger.info(
                f"Request started: {request.method} {request.url.path} "
                f"client={client_ip} request_id={request_id} "
                f"user_agent={user_agent[:100]} content_length={content_length}"
            )

            if self.log_level == "DEBUG" and request.method in ["POST", "PUT", "PATCH"]:
                try:
                    body = await request.body()
                    if body and len(body) < 4096:
                        content_type = request.headers.get("content-type", "")
                        if "application/json" in content_type:
                            logger.debug(f"Request body: {body.decode()}")
                        else:
                            logger.debug(f"Request body size: {len(body)} bytes, type: {content_type}")
                except Exception as e:
                    logger.debug(f"Could not log request body: {e}")

        except Exception as e:
            logger.warning(f"Failed to log request: {e}")

    def _create_error_response(
        self,
        code: int,
        message: str,
        request_id: str,
        details: Any = None
    ) -> JSONResponse:
        from app.error_codes import get_error_info

        error_info = get_error_info(code)

        response = ApiResponse(
            code=code,
            message=message,
            status=ResponseStatus.ERROR,
            data=details,
            request_id=request_id,
            timestamp=datetime.utcnow()
        )

        return JSONResponse(
            status_code=error_info["http_code"],
            content=json.loads(response.model_dump_json()),
            headers={"X-Request-ID": request_id}
        )


class ConcurrentRequestLimiter:
    _request_semaphores: Dict[str, Any] = {}

    @classmethod
    def get_semaphore(cls, key: str, max_concurrent: int = 100):
        if key not in cls._request_semaphores:
            import asyncio
            cls._request_semaphores[key] = asyncio.Semaphore(max_concurrent)
        return cls._request_semaphores[key]


class IngestionRateLimiter:
    def __init__(self):
        self._last_request_time: Dict[str, float] = {}
        self._request_count: Dict[str, int] = {}
        self._max_requests_per_second = 1000
        self._window_size = 1.0

    async def check_rate_limit(self, client_id: str) -> bool:
        current_time = time.time()
        window_start = current_time - self._window_size

        if client_id not in self._last_request_time:
            self._last_request_time[client_id] = current_time
            self._request_count[client_id] = 0

        if self._last_request_time[client_id] < window_start:
            self._request_count[client_id] = 0
            self._last_request_time[client_id] = current_time

        if self._request_count[client_id] >= self._max_requests_per_second:
            return False

        self._request_count[client_id] += 1
        return True


rate_limiter = IngestionRateLimiter()


class RequestContext:
    @staticmethod
    def get_current_request() -> Request:
        from starlette.requests import Request as StarletteRequest
        try:
            return StarletteRequest(scope=getattr(StarletteRequest, '_scope', {}))
        except Exception:
            return None

    @staticmethod
    def get_request_id() -> str:
        return getattr(RequestContext.get_current_request(), 'state', {}).get('request_id', 'unknown')
