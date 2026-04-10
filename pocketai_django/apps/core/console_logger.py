"""
Enhanced Console Logging with Box-Drawing UI.

This module provides structured logging with:
- Fancy box-drawing terminal output (┌, │, └, ├)
- Structured key=value file output
- Three verbosity levels: minimal, standard, verbose
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from django.conf import settings

from core.otel import current_log_record_otel_fields


class Verbosity(Enum):
    """Logging verbosity levels."""
    MINIMAL = "minimal"    # Headers only, no details
    STANDARD = "standard"  # Headers + summary details
    VERBOSE = "verbose"    # Full nested data, all fields


# Box-drawing characters for terminal UI
class Box:
    """Unicode box-drawing characters for terminal UI."""
    # Corners and lines
    TOP_LEFT = "┌"
    TOP_RIGHT = "┐"
    BOTTOM_LEFT = "└"
    BOTTOM_RIGHT = "┘"
    HORIZONTAL = "─"
    VERTICAL = "│"
    
    # Connectors
    T_RIGHT = "├"
    T_LEFT = "┤"
    T_DOWN = "┬"
    T_UP = "┴"
    CROSS = "┼"
    
    # Tree structure
    BRANCH = "├"
    LAST_BRANCH = "└"
    PIPE = "│"
    
    # Separators
    THIN_HORIZONTAL = "─"
    THICK_HORIZONTAL = "━"
    DOUBLE_HORIZONTAL = "═"


def get_verbosity(for_console: bool = True) -> Verbosity:
    """Get current verbosity level from settings."""
    if for_console:
        level = getattr(settings, "LOG_VERBOSITY_CONSOLE", None)
    else:
        level = getattr(settings, "LOG_VERBOSITY_FILE", None)
    
    if not level:
        level = getattr(settings, "LOG_VERBOSITY", "standard")
    
    # Also check environment directly
    if for_console:
        level = os.environ.get("LOG_VERBOSITY_CONSOLE", level)
    else:
        level = os.environ.get("LOG_VERBOSITY_FILE", level)
    
    level = os.environ.get("LOG_VERBOSITY", level)
    
    try:
        return Verbosity(str(level).lower())
    except ValueError:
        return Verbosity.STANDARD


def _get_timezone() -> ZoneInfo:
    """Get configured timezone."""
    tz_name = getattr(settings, "PORTAL_TRACE_TIMEZONE", "Africa/Cairo")
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def _timestamp() -> str:
    """Get formatted timestamp for logs."""
    return datetime.now(_get_timezone()).strftime("%H:%M:%S")


def _full_timestamp() -> str:
    """Get full timestamp for file logs."""
    return datetime.now(_get_timezone()).strftime("%Y-%m-%d %H:%M:%S")


def _truncate(text: str, max_len: int = 60) -> str:
    """Truncate text with ellipsis."""
    if not text or len(text) <= max_len:
        return text or ""
    return text[:max_len - 3] + "..."


def _format_value(value: Any, max_len: int | None = None) -> str:
    """Format a value for display."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        result = value
    else:
        try:
            import json
            result = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            result = str(value)
    
    if max_len:
        return _truncate(result, max_len)
    return result


def _format_duration(ms: float) -> str:
    """Format duration in human-readable way."""
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
        return "?"
    if bytes_count < 1024:
        return f"{bytes_count}B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count/1024:.1f}KB"
    elif bytes_count < 1024 * 1024 * 1024:
        return f"{bytes_count/(1024*1024):.1f}MB"
    else:
        return f"{bytes_count/(1024*1024*1024):.2f}GB"


@dataclass
class LogEntry:
    """Structured log entry for formatting."""
    category: str              # e.g., "PORTAL", "TOOL", "LLM"
    event: str                 # e.g., "REQUEST", "SEARCH_KNOWLEDGE"
    fields: dict[str, Any] = field(default_factory=dict)
    children: list["LogEntry"] = field(default_factory=list)
    level: int = logging.INFO
    timestamp: str = field(default_factory=_timestamp)


class ConsoleFormatter:
    """
    Formats log entries with clean hierarchical output for terminal.
    
    Uses tree characters (├, └) only for showing actual nested structure,
    not decorative box frames.
    """
    
    def __init__(self, verbosity: Verbosity = Verbosity.STANDARD):
        self.verbosity = verbosity
    
    def format_header(self, category: str, event: str, timestamp: str | None = None) -> str:
        """Format a header line - clean, no box."""
        ts = timestamp or _timestamp()
        title = f"{category}.{event}" if event else category
        return f"[{ts}] {title}"
    
    def format_separator(self) -> str:
        """Format a visual separator."""
        return "─" * 60
    
    def format_fields_inline(self, fields: dict[str, Any], sep: str = " | ") -> str:
        """Format multiple fields on one line."""
        parts = []
        for key, value in fields.items():
            if value is not None and value != "":
                formatted = _format_value(value, max_len=40)
                parts.append(f"{key}={formatted}")
        return sep.join(parts)
    
    def format_entry(self, entry: LogEntry) -> list[str]:
        """Format a complete log entry as list of lines."""
        lines = []
        
        # Header line
        header = self.format_header(entry.category, entry.event, entry.timestamp)
        
        # Main fields - inline on same line or below
        if entry.fields:
            if self.verbosity == Verbosity.MINIMAL:
                lines.append(header)
            elif self.verbosity == Verbosity.STANDARD:
                # Inline with header
                fields_str = self.format_fields_inline(entry.fields)
                lines.append(f"{header} {fields_str}")
            else:
                # Verbose: header, then fields on separate lines
                lines.append(header)
                for key, value in entry.fields.items():
                    lines.append(f"  {key}: {_format_value(value)}")
        else:
            lines.append(header)
        
        # Children - show with tree indentation
        if entry.children and self.verbosity != Verbosity.MINIMAL:
            for i, child in enumerate(entry.children):
                is_last = (i == len(entry.children) - 1)
                branch = "└" if is_last else "├"
                
                # Child header
                child_title = f"{child.category}"
                if child.event:
                    child_title += f".{child.event}"
                
                # Child fields inline
                if child.fields:
                    if self.verbosity == Verbosity.VERBOSE:
                        # Multi-line for verbose
                        lines.append(f"  {branch}─ [{i+1}] {child_title}")
                        pipe = " " if is_last else "│"
                        for key, value in child.fields.items():
                            lines.append(f"  {pipe}    {key}={_format_value(value)}")
                    else:
                        # Compact inline
                        compact = " ".join(f"{k}={_format_value(v, 25)}" for k, v in list(child.fields.items())[:4])
                        lines.append(f"  {branch}─ [{i+1}] {child_title}: {compact}")
                else:
                    lines.append(f"  {branch}─ [{i+1}] {child_title}")
        
        return lines
    
    def format(self, entry: LogEntry) -> str:
        """Format entry as single string."""
        return "\n".join(self.format_entry(entry))


class FileFormatter:
    """Formats log entries as structured key=value lines for file output."""
    
    def __init__(self, verbosity: Verbosity = Verbosity.VERBOSE):
        self.verbosity = verbosity
    
    def format_entry(self, entry: LogEntry, level_name: str = "INFO") -> list[str]:
        """Format entry as structured log lines."""
        lines = []
        timestamp = _full_timestamp()
        
        # Main event line
        prefix = f"{timestamp} [{level_name}] {entry.category}.{entry.event}"
        
        if entry.fields:
            if self.verbosity == Verbosity.MINIMAL:
                # Just the event, no fields
                lines.append(prefix)
            else:
                # All fields as key=value
                parts = []
                for key, value in entry.fields.items():
                    formatted = _format_value(value, max_len=None if self.verbosity == Verbosity.VERBOSE else 100)
                    # Quote strings with spaces
                    if " " in formatted and not formatted.startswith('"'):
                        formatted = f'"{formatted}"'
                    parts.append(f"{key}={formatted}")
                lines.append(f"{prefix} {' '.join(parts)}")
        else:
            lines.append(prefix)
        
        # Children as separate lines
        if entry.children and self.verbosity != Verbosity.MINIMAL:
            for i, child in enumerate(entry.children):
                child_prefix = f"{timestamp} [{level_name}] {entry.category}.{entry.event}.ITEM"
                parts = [f"idx={i+1}"]
                for key, value in child.fields.items():
                    formatted = _format_value(value, max_len=None if self.verbosity == Verbosity.VERBOSE else 200)
                    if " " in formatted and not formatted.startswith('"'):
                        formatted = f'"{formatted}"'
                    parts.append(f"{key}={formatted}")
                lines.append(f"{child_prefix} {' '.join(parts)}")
        
        return lines
    
    def format(self, entry: LogEntry, level_name: str = "INFO") -> str:
        """Format entry as single string."""
        return "\n".join(self.format_entry(entry, level_name))


class StructuredLogger:
    """
    High-level structured logger that outputs to both console and file.
    
    Usage:
        slog = StructuredLogger("rag")
        slog.log("SEARCH", "START", {"query": "test", "chunks": 5})
        
        # With children (results)
        slog.log("SEARCH", "COMPLETE", 
            fields={"elapsed_ms": 2700},
            children=[
                {"label": "doc1.pdf", "score": 0.89},
                {"label": "doc2.pdf", "score": 0.76},
            ]
        )
    """
    
    def __init__(self, namespace: str, logger: logging.Logger | None = None):
        self.namespace = namespace.upper()
        self._logger = logger or logging.getLogger(f"apps.{namespace.lower()}")
        self._console_fmt = ConsoleFormatter(get_verbosity(for_console=True))
        self._file_fmt = FileFormatter(get_verbosity(for_console=False))
    
    def _is_console_handler(self, handler: logging.Handler) -> bool:
        """Check if handler outputs to console/terminal."""
        if isinstance(handler, logging.StreamHandler):
            return handler.stream in (sys.stdout, sys.stderr)
        return False
    
    def log(
        self,
        category: str,
        event: str,
        fields: dict[str, Any] | None = None,
        children: list[dict[str, Any]] | None = None,
        level: int = logging.INFO,
    ) -> None:
        """Log a structured event."""
        # Build entry
        entry = LogEntry(
            category=category,
            event=event,
            fields=fields or {},
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
        
        # Get level name
        level_name = logging.getLevelName(level)
        
        # Format and log to each handler appropriately
        logger = self._logger
        if not logger.handlers and logger.parent:
            logger = logger.parent
        
        # For root logger or loggers with handlers
        for handler in logger.handlers:
            if self._is_console_handler(handler):
                # Fancy console output
                msg = self._console_fmt.format(entry)
            else:
                # Structured file output
                msg = self._file_fmt.format(entry, level_name)
            
            # Direct emit to avoid double formatting
            record = logging.LogRecord(
                name=logger.name,
                level=level,
                pathname="",
                lineno=0,
                msg=msg,
                args=(),
                exc_info=None,
            )
            record.__dict__.update(current_log_record_otel_fields())
            handler.emit(record)
        
        # If no handlers found, fall back to standard logging
        if not logger.handlers:
            # Use file format for default
            self._logger.log(level, self._file_fmt.format(entry, level_name))
    
    def log_box(
        self,
        title: str,
        fields: dict[str, Any] | None = None,
        level: int = logging.INFO,
    ) -> None:
        """Log a simple boxed message (shorthand for common case)."""
        parts = title.split(".", 1)
        category = parts[0]
        event = parts[1] if len(parts) > 1 else ""
        self.log(category, event, fields, level=level)
    
    def info(self, category: str, event: str, fields: dict[str, Any] | None = None, **kwargs) -> None:
        """Log at INFO level."""
        self.log(category, event, fields, level=logging.INFO, **kwargs)
    
    def warning(self, category: str, event: str, fields: dict[str, Any] | None = None, **kwargs) -> None:
        """Log at WARNING level."""
        self.log(category, event, fields, level=logging.WARNING, **kwargs)
    
    def error(self, category: str, event: str, fields: dict[str, Any] | None = None, **kwargs) -> None:
        """Log at ERROR level."""
        self.log(category, event, fields, level=logging.ERROR, **kwargs)
    
    def debug(self, category: str, event: str, fields: dict[str, Any] | None = None, **kwargs) -> None:
        """Log at DEBUG level."""
        self.log(category, event, fields, level=logging.DEBUG, **kwargs)


# Singleton loggers for common namespaces
_loggers: dict[str, StructuredLogger] = {}


def get_structured_logger(namespace: str) -> StructuredLogger:
    """Get or create a structured logger for namespace."""
    if namespace not in _loggers:
        _loggers[namespace] = StructuredLogger(namespace)
    return _loggers[namespace]


# Convenience function for quick logging
def slog(
    namespace: str,
    category: str,
    event: str,
    fields: dict[str, Any] | None = None,
    **kwargs
) -> None:
    """Quick structured log."""
    get_structured_logger(namespace).log(category, event, fields, **kwargs)
