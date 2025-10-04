from __future__ import annotations

"""Logging configuration helpers."""

import json
import logging
import logging.config
from contextvars import ContextVar
from typing import Any, Iterable

from .settings import Settings

REQUEST_ID_CTX_VAR: ContextVar[str | None] = ContextVar("request_id", default=None)
_REDACT_KEYS = {"password", "token", "secret", "authorization"}
_STANDARD_ATTRS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "process",
    "processName",
    "message",
    "asctime",
    "request_id",
}


class RequestIdFilter(logging.Filter):
    """Attach the current request id to each log record."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        record.request_id = REQUEST_ID_CTX_VAR.get()
        return True


class JsonFormatter(logging.Formatter):
    """Emit application logs as structured JSON."""

    def __init__(self, *args: Any, redacted_keys: Iterable[str] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._redacted_keys = set(redacted_keys or _REDACT_KEYS)

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "request_id": getattr(record, "request_id", None),
        }

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS:
                continue
            payload[key] = self._maybe_redact(key, value)

        return json.dumps(payload, default=str)

    def _maybe_redact(self, key: str, value: Any) -> Any:
        normalized_key = key.lower()
        if normalized_key in self._redacted_keys:
            return "***redacted***"
        return value


def configure_logging(settings: Settings) -> None:
    """Configure logging for the application."""

    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "request_id": {
                "()": RequestIdFilter,
            }
        },
        "formatters": {
            "json": {
                "()": JsonFormatter,
                "redacted_keys": list(_REDACT_KEYS),
            }
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "filters": ["request_id"],
                "stream": "ext://sys.stdout",
            }
        },
        "root": {
            "level": settings.LOG_LEVEL.upper(),
            "handlers": ["default"],
        },
        "loggers": {
            "uvicorn": {
                "level": settings.LOG_LEVEL.upper(),
                "handlers": ["default"],
                "propagate": False,
            },
            "uvicorn.error": {
                "level": settings.LOG_LEVEL.upper(),
                "handlers": ["default"],
                "propagate": False,
            },
            "uvicorn.access": {
                "level": settings.LOG_LEVEL.upper(),
                "handlers": ["default"],
                "propagate": False,
            },
        },
    }

    logging.config.dictConfig(logging_config)
