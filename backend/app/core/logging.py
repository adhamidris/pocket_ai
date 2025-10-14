from __future__ import annotations

"""Logging configuration helpers."""

import json
import logging
import logging.config
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

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
_IGNORED_ATTRS = {
    "color_message",
    "taskName",
    "request_event",
}


def _iter_extra_fields(record: logging.LogRecord) -> Iterable[tuple[str, Any]]:
    """Yield non-standard logging attributes attached to a record."""

    for key, value in record.__dict__.items():
        if key in _STANDARD_ATTRS or key in _IGNORED_ATTRS:
            continue
        yield key, value


class RequestIdFilter(logging.Filter):
    """Attach the current request id to each log record."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        if getattr(record, "request_id", None) is None:
            record.request_id = REQUEST_ID_CTX_VAR.get()
        return True


class ErrorReferenceFilter(logging.Filter):
    """Assign a short reference id to error records for cross-linking logs."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        should_tag = bool(record.exc_info) or (
            record.levelno >= logging.ERROR and not getattr(record, "request_event", False)
        )
        if should_tag and getattr(record, "error_reference", None) is None:
            record.error_reference = self._generate_reference()
        return True

    @staticmethod
    def _generate_reference() -> str:
        return f"E{uuid4().hex[:8].upper()}"


class RedactingFormatter(logging.Formatter):
    """Logging formatter that hides sensitive keys in extras."""

    def __init__(self, *args: Any, redacted_keys: Iterable[str] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._redacted_keys = set(redacted_keys or _REDACT_KEYS)

    def _maybe_redact(self, key: str, value: Any) -> Any:
        normalized_key = key.lower()
        if normalized_key in self._redacted_keys:
            return "***redacted***"
        return value


class JsonFormatter(RedactingFormatter):
    """Emit application logs as structured JSON."""

    def __init__(self, *args: Any, redacted_keys: Iterable[str] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, redacted_keys=redacted_keys, **kwargs)

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

        for key, value in _iter_extra_fields(record):
            payload[key] = self._maybe_redact(key, value)

        return json.dumps(payload, default=str)


class ConsoleFormatter(RedactingFormatter):
    """Render logs in a readable, colorized console layout."""

    _LEVEL_COLORS = {
        logging.DEBUG: "\033[36m",  # Cyan
        logging.INFO: "\033[32m",  # Green
        logging.WARNING: "\033[33m",  # Yellow
        logging.ERROR: "\033[31m",  # Red
        logging.CRITICAL: "\033[41m\033[97m",  # White on red background
    }
    _RESET = "\033[0m"
    _DIM = "\033[2m"

    def __init__(
        self,
        *args: Any,
        use_colors: bool | None = None,
        redacted_keys: Iterable[str] | None = None,
        error_log_path: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, redacted_keys=redacted_keys, **kwargs)
        self._use_colors = use_colors if use_colors is not None else sys.stdout.isatty()
        self._error_log_path = error_log_path

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, self.datefmt or "%Y-%m-%d %H:%M:%S")
        level_display = record.levelname.ljust(7)
        if self._use_colors:
            level_display = self._colorize(level_display, record.levelno)

        logger_name = record.name
        message = record.getMessage() or ""
        message_lines = message.splitlines()
        headline = message_lines[0] if message_lines else ""

        error_reference = getattr(record, "error_reference", None)
        exc_summary = self._exception_summary(record)
        if exc_summary:
            if headline:
                headline = f"{headline} - {exc_summary}"
            else:
                headline = exc_summary

        base_line = f"{timestamp} | {level_display} | {logger_name}"
        if headline:
            base_line = f"{base_line} | {headline}"

        lines = [base_line]
        indent = "    "

        if error_reference:
            request_id = getattr(record, "request_id", None)
            reference_line = f"check error {error_reference}"
            if self._error_log_path:
                reference_line = f"{reference_line} -> {self._error_log_path}"
            if request_id:
                reference_line = f"{reference_line} | rid={request_id}"
            if self._use_colors:
                reference_line = f"{self._DIM}{reference_line}{self._RESET}"
            lines.append(f"{indent}{reference_line}")
            request_id = None
        else:
            request_id = getattr(record, "request_id", None)

        is_request_event = getattr(record, "request_event", False)

        context_parts: list[str] = []
        if request_id and not is_request_event:
            context_parts.append(f"rid={request_id}")

        if not is_request_event:
            for key, value in sorted(_iter_extra_fields(record), key=lambda item: item[0]):
                if key == "error_reference":
                    continue
                sanitized_value = self._maybe_redact(key, value)
                context_parts.append(f"{key}={self._stringify(sanitized_value)}")

        if context_parts:
            context_line = " ".join(context_parts)
            if self._use_colors:
                context_line = f"{self._DIM}{context_line}{self._RESET}"
            lines.append(f"{indent}{context_line}")

        return "\n".join(lines)

    @staticmethod
    def _exception_summary(record: logging.LogRecord) -> str | None:
        if not record.exc_info:
            return None
        exc_type, exc_value, _ = record.exc_info
        if exc_type is None and exc_value is None:
            return None
        type_name = getattr(exc_type, "__name__", str(exc_type)) if exc_type else ""
        value_text = str(exc_value) if exc_value else ""
        if type_name and value_text:
            return f"{type_name}: {value_text}"
        return type_name or value_text or None

    def _colorize(self, text: str, levelno: int) -> str:
        color = self._LEVEL_COLORS.get(levelno)
        if not color:
            return text
        return f"{color}{text}{self._RESET}"

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return str(value)
        try:
            return json.dumps(value, default=str)
        except (TypeError, ValueError):
            return str(value)


def configure_logging(settings: Settings) -> None:
    """Configure logging for the application."""

    base_dir = Path(__file__).resolve().parents[2]
    error_log_path: Path | None = None
    if settings.LOG_ERROR_FILE:
        candidate = Path(settings.LOG_ERROR_FILE).expanduser()
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        candidate.parent.mkdir(parents=True, exist_ok=True)
        error_log_path = candidate

    formatter_name = "json"
    formatters: dict[str, dict[str, Any]] = {
        "json": {
            "()": JsonFormatter,
            "redacted_keys": list(_REDACT_KEYS),
        }
    }

    if settings.log_format == "console":
        console_config: dict[str, Any] = {
            "()": ConsoleFormatter,
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "redacted_keys": list(_REDACT_KEYS),
        }
        if error_log_path:
            try:
                console_config["error_log_path"] = str(error_log_path.relative_to(base_dir))
            except ValueError:
                console_config["error_log_path"] = str(error_log_path)
        formatters["console"] = console_config
        formatter_name = "console"

    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "request_id": {"()": RequestIdFilter},
            "error_reference": {"()": ErrorReferenceFilter},
        },
        "formatters": formatters,
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": formatter_name,
                "filters": ["request_id", "error_reference"],
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

    if error_log_path:
        logging_config["handlers"]["error_file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "ERROR",
            "formatter": "json",
            "filters": ["request_id", "error_reference"],
            "filename": str(error_log_path),
            "maxBytes": 5_000_000,
            "backupCount": 5,
            "encoding": "utf-8",
        }
        logging_config["root"]["handlers"].append("error_file")
        logging_config["loggers"]["uvicorn"]["handlers"].append("error_file")
        logging_config["loggers"]["uvicorn.access"]["handlers"].append("error_file")
        logging_config["loggers"]["uvicorn.error"]["handlers"] = ["error_file"]

    logging.config.dictConfig(logging_config)
