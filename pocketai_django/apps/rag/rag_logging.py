"""
RAG-specific structured logging with enhanced console output.

This module provides the main logging interface for RAG operations,
now enhanced with box-drawing terminal UI and comprehensive file logging.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from django.conf import settings

from apps.core.console_logger import (
    Box,
    ConsoleFormatter,
    FileFormatter,
    LogEntry,
    StructuredLogger,
    Verbosity,
    get_structured_logger,
    get_verbosity,
    _timestamp,
    _full_timestamp,
    _format_duration,
    _format_value,
)

logger = logging.getLogger(__name__)

# Namespace loggers
NAMESPACE_LOGGERS = {
    "rag": logger,
    "mcp": logging.getLogger("apps.mcp.orchestrator"),
    "llm": logging.getLogger("apps.llm.llm_provider"),
    "portal": logging.getLogger("apps.api.chat_portal"),
}

# Structured loggers for each namespace
_structured_loggers: dict[str, StructuredLogger] = {}


def _get_structured_logger(namespace: str) -> StructuredLogger:
    """Get or create structured logger for namespace."""
    if namespace not in _structured_loggers:
        base_logger = NAMESPACE_LOGGERS.get(namespace) or logging.getLogger(f"apps.{namespace}")
        _structured_loggers[namespace] = StructuredLogger(namespace, base_logger)
    return _structured_loggers[namespace]


def _compact_detail(detail: Any | None, verbosity: Verbosity = Verbosity.STANDARD) -> dict[str, Any]:
    """Convert detail to structured dict based on verbosity."""
    if not detail:
        return {}
    
    if isinstance(detail, dict):
        if verbosity == Verbosity.MINIMAL:
            # Only keep essential keys
            priority = ["model", "tokens", "elapsed_ms", "count", "status"]
            return {k: v for k, v in detail.items() if k in priority and v not in (None, "", 0)}
        elif verbosity == Verbosity.STANDARD:
            # Keep important keys, skip internal ones
            skip = {"raw_response", "full_content", "embeddings", "vector"}
            return {k: v for k, v in detail.items() if k not in skip and v not in (None, "", 0)}
        else:
            # Verbose: keep everything
            return {k: v for k, v in detail.items() if v is not None}
    
    # Try to parse string as key=value pairs
    if isinstance(detail, str):
        return {"message": detail}
    
    return {"value": str(detail)}


def structured_log(
    namespace: str,
    stage: str,
    detail: Any | None = None,
    *,
    indent: int = 0,
    context: Mapping[str, Any] | None = None,
    level: int = logging.INFO,
    logger_obj: logging.Logger | None = None,
    children: list[dict[str, Any]] | None = None,
) -> None:
    """
    Enhanced structured logging with box-drawing console UI.
    
    NO MORE SUPPRESSION - all logs are captured, verbosity controls display.
    
    Args:
        namespace: Log category (e.g., "rag", "mcp", "llm")
        stage: Event stage (e.g., "search.start", "tool.complete")
        detail: Optional dict or value with event details
        indent: Indentation level (for compatibility, largely ignored now)
        context: Optional context mapping (merged into detail)
        level: Log level (default INFO)
        logger_obj: Optional logger override
        children: Optional list of child entries (for results, etc.)
    """
    verbosity = get_verbosity(for_console=True)
    
    # Parse stage into category.event
    parts = stage.split(".", 1) if "." in stage else [stage, ""]
    category = parts[0].upper()
    event = parts[1].upper() if len(parts) > 1 else ""
    
    # Build fields from detail and context
    fields = _compact_detail(detail, verbosity)
    if context:
        fields.update({k: v for k, v in context.items() if v is not None})
    
    # Get target logger
    target_logger = logger_obj or NAMESPACE_LOGGERS.get(namespace) or logger
    
    # Create structured entry
    entry = LogEntry(
        category=category,
        event=event,
        fields=fields,
        level=level,
    )
    
    # Add children
    if children:
        for child in children:
            child_entry = LogEntry(
                category=child.get("label", child.get("name", "item")),
                event="",
                fields={k: v for k, v in child.items() if k not in ("label", "name")},
            )
            entry.children.append(child_entry)
    
    # Format based on verbosity and output type
    console_fmt = ConsoleFormatter(verbosity)
    file_fmt = FileFormatter(get_verbosity(for_console=False))
    level_name = logging.getLevelName(level)
    
    # Check if we have handlers
    effective_logger = target_logger
    while not effective_logger.handlers and effective_logger.parent:
        effective_logger = effective_logger.parent
    
    if effective_logger.handlers:
        for handler in effective_logger.handlers:
            import sys
            is_console = isinstance(handler, logging.StreamHandler) and handler.stream in (sys.stdout, sys.stderr)
            
            if is_console:
                msg = console_fmt.format(entry)
            else:
                msg = file_fmt.format(entry, level_name)
            
            record = logging.LogRecord(
                name=target_logger.name,
                level=level,
                pathname="",
                lineno=0,
                msg=msg,
                args=(),
                exc_info=None,
            )
            handler.emit(record)
    else:
        # Fallback: use file format
        target_logger.log(level, file_fmt.format(entry, level_name))


def rag_log(
    stage: str,
    detail: Any | None = None,
    *,
    indent: int = 0,
    context: Mapping[str, Any] | None = None,
    children: list[dict[str, Any]] | None = None,
) -> None:
    """RAG-specific structured log shorthand."""
    structured_log("rag", stage, detail, indent=indent, context=context, logger_obj=logger, children=children)


def mcp_log(
    stage: str,
    detail: Any | None = None,
    *,
    context: Mapping[str, Any] | None = None,
    children: list[dict[str, Any]] | None = None,
    level: int = logging.INFO,
) -> None:
    """MCP-specific structured log shorthand."""
    structured_log("mcp", stage, detail, context=context, level=level, children=children)


def tool_log(
    tool_name: str,
    event: str,
    fields: dict[str, Any] | None = None,
    *,
    results: list[dict[str, Any]] | None = None,
    level: int = logging.INFO,
) -> None:
    """
    Log a tool execution with optional results.
    
    Args:
        tool_name: Name of the tool (e.g., "search_knowledge")
        event: Event type (e.g., "start", "complete", "error")
        fields: Tool execution details
        results: Optional list of result items
        level: Log level
    
    Example:
        tool_log("search_knowledge", "complete", 
            {"query": "credit card", "elapsed_ms": 2700},
            results=[
                {"label": "fees.pdf", "score": 0.89, "is_table": True},
                {"label": "terms.pdf", "score": 0.76},
            ]
        )
    """
    structured_log(
        "mcp",
        f"tool.{tool_name}.{event}",
        fields,
        children=results,
        level=level,
    )


def llm_log(
    event: str,
    fields: dict[str, Any] | None = None,
    *,
    level: int = logging.INFO,
) -> None:
    """LLM-specific structured log."""
    structured_log("llm", f"llm.{event}", fields, level=level)


# =============================================================================
# Quick formatting helpers for inline use
# =============================================================================

def format_search_result(
    idx: int,
    label: str,
    score: float,
    is_table: bool = False,
    chunk_id: str | None = None,
    content_preview: str | None = None,
) -> dict[str, Any]:
    """Format a search result for logging."""
    result = {
        "label": label,
        "score": round(score, 3),
        "is_table": is_table,
    }
    if chunk_id:
        result["chunk_id"] = chunk_id[:8] + "..."
    if content_preview:
        result["preview"] = content_preview[:100] + "..." if len(content_preview) > 100 else content_preview
    return result


def format_performance(
    elapsed_ms: float,
    breakdown: dict[str, float] | None = None,
    slo_warn_ms: float | None = None,
) -> dict[str, Any]:
    """Format performance metrics for logging."""
    result = {"elapsed": _format_duration(elapsed_ms)}
    
    if slo_warn_ms and elapsed_ms > slo_warn_ms:
        result["slo_warning"] = f">{_format_duration(slo_warn_ms)}"
    
    if breakdown:
        for key, value in breakdown.items():
            result[key] = _format_duration(value)
    
    return result
