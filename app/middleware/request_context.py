"""Assigns/propagates a request_id for every request and logs start/finish.

The request_id flows into structured logs now, and is the same correlation_id
that processing_jobs / event_outbox / audit_logs will carry in later stages.
"""
import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.logging import get_logger, request_id_ctx

log = get_logger("request")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_ctx.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        response.headers["X-Request-ID"] = rid
        return response
