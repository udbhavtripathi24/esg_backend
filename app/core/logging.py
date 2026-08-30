"""Structured JSON logging + per-request correlation ID.

Foundation for Cloud Logging (decision GCP §7): logs are JSON with a
request_id that ties every log line and (later) every processing_job /
audit_log / event back to the originating request.
"""
import logging
import sys
import structlog
from contextvars import ContextVar

# request_id is set by middleware per request and read by the log processor.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def _add_request_id(_logger, _method, event_dict):
    event_dict["request_id"] = request_id_ctx.get()
    return event_dict


def configure_logging(json_output: bool = True) -> None:
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_request_id,
    ]
    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "esg"):
    return structlog.get_logger(name)
