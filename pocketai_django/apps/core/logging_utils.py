"""
User-friendly logging utilities with emoji support and clean formatting.

This module provides enhanced logging helpers that make logs more scannable
and easier to understand at a glance.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from django.conf import settings


# Emoji mapping for different log categories
class LogEmoji:
    """Emoji constants for consistent logging."""
    
    # Status & Progress
    SUCCESS = "✅"
    START = "🚀"
    PROCESSING = "⚙️"
    COMPLETE = "🎉"
    
    # Operations
    SEARCH = "🔍"
    DOCUMENT = "📄"
    TABLE = "📊"
    DATABASE = "💾"
    CACHE = "🎯"
    UPLOAD = "📤"
    DOWNLOAD = "📥"
    
    # Performance & Issues
    WARNING = "⚠️"
    ERROR = "❌"
    TIME = "⏱️"
    SLOW = "🐌"
    
    # Actors
    USER = "👤"
    AI = "🤖"
    LOCK = "🔒"
    
    # Network & System
    NETWORK = "🌐"
    QUEUE = "📋"
    VECTOR = "🧮"
    
    # Debug
    DEBUG = "🔧"
    INFO = "ℹ️"


def _timestamp() -> str:
    """Get formatted timestamp for logs."""
    tz_name = getattr(settings, "PORTAL_TRACE_TIMEZONE", "Africa/Cairo")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_duration(ms: float) -> str:
    """Format duration in a human-readable way."""
    if ms < 1000:
        return f"{ms:.0f}ms"
    elif ms < 60000:
        return f"{ms/1000:.1f}s"
    else:
        minutes = int(ms / 60000)
        seconds = (ms % 60000) / 1000
        return f"{minutes}m {seconds:.1f}s"


def _format_size(bytes_count: int | None) -> str:
    """Format file size in human-readable format."""
    if bytes_count is None:
        return "unknown"
    if bytes_count < 1024:
        return f"{bytes_count}B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count/1024:.1f}KB"
    elif bytes_count < 1024 * 1024 * 1024:
        return f"{bytes_count/(1024*1024):.1f}MB"
    else:
        return f"{bytes_count/(1024*1024*1024):.2f}GB"


def _truncate_id(id_str: str | None, length: int = 8) -> str:
    """Truncate UUIDs for readability."""
    if not id_str:
        return "none"
    return str(id_str)[:length] + "..."


def log_start(
    logger: logging.Logger,
    operation: str,
    summary: str,
    details: Mapping[str, Any] | None = None,
    emoji: str = LogEmoji.START,
) -> None:
    """
    Log the start of an operation.
    
    Args:
        logger: Logger instance to use
        operation: Operation name (e.g., "INGEST", "SEARCH")
        summary: Brief description of what's starting
        details: Optional dict of key details to log
        emoji: Emoji prefix (default: 🚀)
    
    Example:
        log_start(logger, "INGEST", "Document: fees.pdf", 
                  {"job_id": "abc123", "size": 2400000})
        
        Output:
        🚀 INGEST START Document: fees.pdf
            └─ Job ID: abc123... | Size: 2.4MB
    """
    msg = f"{emoji} {operation.upper()} START {summary}"
    
    if details:
        detail_parts = []
        for key, value in details.items():
            # Format common keys nicely
            if "id" in key.lower() and isinstance(value, str) and len(value) > 16:
                formatted_value = _truncate_id(value)
            elif "size" in key.lower() or "bytes" in key.lower():
                formatted_value = _format_size(value)
            elif "duration" in key.lower() or "ms" in key.lower():
                formatted_value = _format_duration(value)
            else:
                formatted_value = str(value)
            
            # Convert snake_case to Title Case
            formatted_key = key.replace("_", " ").title()
            detail_parts.append(f"{formatted_key}: {formatted_value}")
        
        logger.info("%s\n    └─ %s", msg, " | ".join(detail_parts))
    else:
        logger.info(msg)


def log_success(
    logger: logging.Logger,
    operation: str,
    summary: str,
    metrics: Mapping[str, Any] | None = None,
    emoji: str = LogEmoji.SUCCESS,
) -> None:
    """
    Log successful completion of an operation.
    
    Args:
        logger: Logger instance to use
        operation: Operation name
        summary: Success summary
        metrics: Optional metrics dict
        emoji: Emoji prefix (default: ✅)
    
    Example:
        log_success(logger, "CHUNKS", "95 chunks persisted",
                    {"upload_id": "8d14...", "duration_ms": 84181})
        
        Output:
        ✅ CHUNKS COMMITTED 95 chunks persisted
            └─ Upload ID: 8d14... | Duration: 84.2s
    """
    msg = f"{emoji} {operation.upper()} {summary}"
    
    if metrics:
        metric_parts = []
        for key, value in metrics.items():
            if "id" in key.lower() and isinstance(value, str) and len(value) > 16:
                formatted_value = _truncate_id(value)
            elif "size" in key.lower() or "bytes" in key.lower():
                formatted_value = _format_size(value)
            elif "duration" in key.lower() or "ms" in key.lower():
                formatted_value = _format_duration(value)
            else:
                formatted_value = str(value)
            
            formatted_key = key.replace("_", " ").title()
            metric_parts.append(f"{formatted_key}: {formatted_value}")
        
        logger.info("%s\n    └─ %s", msg, " | ".join(metric_parts))
    else:
        logger.info(msg)


def log_progress(
    logger: logging.Logger,
    operation: str,
    summary: str,
    current: int | None = None,
    total: int | None = None,
    emoji: str = LogEmoji.PROCESSING,
) -> None:
    """
    Log progress of an ongoing operation.
    
    Args:
        logger: Logger instance to use
        operation: Operation name
        summary: Progress description
        current: Current item/step
        total: Total items/steps
        emoji: Emoji prefix (default: ⚙️)
    
    Example:
        log_progress(logger, "EXTRACTING", "Tables from page 1", 3, 8)
        
        Output:
        ⚙️ EXTRACTING Tables from page 1 (3/8)
    """
    if current is not None and total is not None:
        msg = f"{emoji} {operation.upper()} {summary} ({current}/{total})"
    else:
        msg = f"{emoji} {operation.upper()} {summary}"
    
    logger.info(msg)


def log_warning(
    logger: logging.Logger,
    issue: str,
    context: Mapping[str, Any] | None = None,
    emoji: str = LogEmoji.WARNING,
) -> None:
    """
    Log a warning with context.
    
    Args:
        logger: Logger instance to use
        issue: Description of the issue
        context: Optional context dict
        emoji: Emoji prefix (default: ⚠️)
    
    Example:
        log_warning(logger, "RLS discarded rows",
                    {"expected": 95, "actual": 0})
        
        Output:
        ⚠️ WARNING RLS discarded rows
            └─ Expected: 95 | Actual: 0
    """
    msg = f"{emoji} WARNING {issue}"
    
    if context:
        context_parts = []
        for key, value in context.items():
            formatted_key = key.replace("_", " ").title()
            context_parts.append(f"{formatted_key}: {value}")
        
        logger.warning("%s\n    └─ %s", msg, " | ".join(context_parts))
    else:
        logger.warning(msg)


def log_error(
    logger: logging.Logger,
    error: str,
    details: Mapping[str, Any] | None = None,
    exception: Exception | None = None,
    emoji: str = LogEmoji.ERROR,
) -> None:
    """
    Log an error with details.
    
    Args:
        logger: Logger instance to use
        error: Error description
        details: Optional error details dict
        exception: Optional exception object
        emoji: Emoji prefix (default: ❌)
    
    Example:
        log_error(logger, "Chunk persistence failed",
                  {"upload_id": "8d14...", "chunks": 95})
        
        Output:
        ❌ ERROR Chunk persistence failed
            └─ Upload ID: 8d14... | Chunks: 95
    """
    msg = f"{emoji} ERROR {error}"
    
    if details:
        detail_parts = []
        for key, value in details.items():
            if "id" in key.lower() and isinstance(value, str) and len(value) > 16:
                formatted_value = _truncate_id(value)
            else:
                formatted_value = str(value)
            
            formatted_key = key.replace("_", " ").title()
            detail_parts.append(f"{formatted_key}: {formatted_value}")
        
        logger.error("%s\n    └─ %s", msg, " | ".join(detail_parts))
        
        if exception:
            logger.error("    └─ Exception: %s", str(exception))
    else:
        logger.error(msg)
        if exception:
            logger.error("    └─ Exception: %s", str(exception))


def log_performance(
    logger: logging.Logger,
    operation: str,
    duration_ms: float,
    breakdown: Mapping[str, float] | None = None,
    slo_warn_ms: float | None = None,
) -> None:
    """
    Log performance metrics with SLO warnings.
    
    Args:
        logger: Logger instance to use
        operation: Operation name
        duration_ms: Total duration in milliseconds
        breakdown: Optional breakdown of time by component
        slo_warn_ms: Optional SLO threshold for warnings
    
    Example:
        log_performance(logger, "SEARCH", 7338,
                        {"vector": 43, "rerank": 5463},
                        slo_warn_ms=1200)
        
        Output:
        ⏱️ SEARCH SLOW Query completed in 7.3s (SLO warning: >1.2s)
            └─ Vector: 43ms | Rerank: 5.5s ⚠️
    """
    duration_formatted = _format_duration(duration_ms)
    
    # Determine if this is slow
    is_slow = slo_warn_ms and duration_ms > slo_warn_ms
    emoji = LogEmoji.SLOW if is_slow else LogEmoji.TIME
    
    if is_slow:
        slo_formatted = _format_duration(slo_warn_ms)
        msg = f"{emoji} {operation.upper()} SLOW Query completed in {duration_formatted} (SLO warning: >{slo_formatted})"
    else:
        msg = f"{emoji} {operation.upper()} Query completed in {duration_formatted}"
    
    if breakdown:
        breakdown_parts = []
        for key, value in breakdown.items():
            formatted_key = key.replace("_", " ").title()
            formatted_value = _format_duration(value)
            
            # Add warning emoji for slow components
            if slo_warn_ms and value > slo_warn_ms * 0.5:
                formatted_value += f" {LogEmoji.WARNING}"
            
            breakdown_parts.append(f"{formatted_key}: {formatted_value}")
        
        if is_slow:
            logger.warning("%s\n    └─ %s", msg, " | ".join(breakdown_parts))
        else:
            logger.info("%s\n    └─ %s", msg, " | ".join(breakdown_parts))
    else:
        if is_slow:
            logger.warning(msg)
        else:
            logger.info(msg)


# Backward compatibility: keep structured_log working
def structured_log(
    namespace: str,
    stage: str,
    detail: Any | None = None,
    *,
    indent: int = 0,
    context: Mapping[str, Any] | None = None,
    level: int = logging.INFO,
    logger_obj: logging.Logger | None = None,
) -> None:
    """
    Legacy structured logging function - maintained for backward compatibility.
    Now uses the enhanced console_logger formatting.
    """
    from apps.rag.rag_logging import structured_log as enhanced_structured_log
    enhanced_structured_log(namespace, stage, detail, indent=indent, context=context, level=level, logger_obj=logger_obj)


# Re-export new console_logger utilities for convenience
from apps.core.console_logger import (
    Box,
    ConsoleFormatter,
    FileFormatter,
    LogEntry,
    StructuredLogger,
    Verbosity,
    get_structured_logger,
    get_verbosity,
    slog,
)

__all__ = [
    # Legacy utilities
    "LogEmoji",
    "log_start",
    "log_success", 
    "log_progress",
    "log_warning",
    "log_error",
    "log_performance",
    "structured_log",
    # New console_logger utilities
    "Box",
    "ConsoleFormatter",
    "FileFormatter",
    "LogEntry",
    "StructuredLogger",
    "Verbosity",
    "get_structured_logger",
    "get_verbosity",
    "slog",
]

