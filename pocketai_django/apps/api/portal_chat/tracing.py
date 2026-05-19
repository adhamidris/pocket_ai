from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings


logger = logging.getLogger(__name__)


class PortalTraceLogger:
    """
    Structured trace logger for portal LLM turns.
    
    Clean hierarchical output - uses tree characters (├, └) only for
    showing actual nested structure, not decorative frames.
    """
    
    def __init__(
        self,
        *,
        conversation,
        agent,
        session_token: str,
        orchestrator_mode: str,
    ) -> None:
        from apps.core.console_logger import Verbosity, get_verbosity
        
        self.conversation_id = getattr(conversation, "id", None)
        self.business_id = getattr(getattr(conversation, "business_profile", None), "id", None)
        self.business_slug = getattr(getattr(conversation, "business_profile", None), "slug", None)
        self.agent_slug = getattr(agent, "slug", None)
        self.session_token = session_token
        self.orchestrator_mode = orchestrator_mode
        self._logged_header = False
        
        tz_name = getattr(settings, "PORTAL_TRACE_TIMEZONE", "Africa/Cairo")
        try:
            self._timezone = ZoneInfo(tz_name)
        except Exception:
            self._timezone = ZoneInfo("UTC")
        
        self._console_verbosity = get_verbosity(for_console=True)

    def _timestamp(self) -> str:
        return datetime.now(self._timezone).strftime("%H:%M:%S")

    def _stringify(self, value: Any, max_len: int | None = None) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            result = value
        else:
            try:
                result = json.dumps(value, ensure_ascii=False)
            except Exception:
                result = str(value)
        
        if max_len and len(result) > max_len:
            return result[:max_len - 3] + "..."
        return result

    def format_data(self, value: Any) -> str:
        return self._stringify(value)
    
    def _log_header(self) -> None:
        """Log the portal request header - clean format."""
        if self._logged_header:
            return
        
        self._logged_header = True
        ts = self._timestamp()
        
        # Clean separator and header
        logger.info("─" * 60)
        logger.info(f"[{ts}] PORTAL.REQUEST conv={str(self.conversation_id)[:8]}... agent={self.agent_slug}")

    def log(self, title: str, detail: str | dict | None = None, *, indent: int = 0, extra: Any | None = None) -> None:
        """Log an event with clean hierarchical formatting."""
        from apps.core.console_logger import Verbosity
        
        # Log header on first event
        self._log_header()
        
        indent_prefix = "  " * max(indent, 0)
        
        # Format based on verbosity
        if self._console_verbosity == Verbosity.MINIMAL:
            logger.info(f"{indent_prefix}├─ {title}")
        elif self._console_verbosity == Verbosity.STANDARD:
            if detail:
                detail_text = self._stringify(detail, max_len=80)
                logger.info(f"{indent_prefix}├─ {title}: {detail_text}")
            else:
                logger.info(f"{indent_prefix}├─ {title}")
        else:
            # Verbose: full details
            logger.info(f"{indent_prefix}├─ {title}")
            if detail:
                detail_text = self._stringify(detail)
                if len(detail_text) > 100:
                    for i in range(0, len(detail_text), 100):
                        logger.info(f"{indent_prefix}│   {detail_text[i:i+100]}")
                else:
                    logger.info(f"{indent_prefix}│   {detail_text}")

    def log_status(self, code: str, *, label: str | None = None, meta: dict | None = None, indent: int = 1) -> None:
        """Log status events - respects verbosity."""
        from apps.core.console_logger import Verbosity
        
        if self._console_verbosity == Verbosity.MINIMAL:
            return
        
        # Standard: only important status codes
        important_codes = {"searching_complete", "reading_complete", "answer_finalized", "stream_complete"}
        if self._console_verbosity == Verbosity.STANDARD and code not in important_codes:
            return
        
        detail = {"code": code}
        if label:
            detail["label"] = label
        if meta:
            detail.update(meta)
        
        self.log(f"status.{code}", detail, indent=indent)

    def log_spinner(
        self,
        text: str,
        *,
        pending: bool,
        prev_text: str | None = None,
        reason: str | None = None,
        indent: int = 1,
    ) -> None:
        """Log spinner status updates (portal state-machine only)."""
        from apps.core.console_logger import Verbosity

        if self._console_verbosity == Verbosity.MINIMAL:
            return

        detail: dict[str, object] = {"text": text, "pending": pending}
        if prev_text is not None:
            detail["prev_text"] = prev_text
        if reason:
            detail["reason"] = reason

        self.log("spinner.update", detail, indent=indent)

    def log_error(self, title: str, error: Exception | str, *, indent: int = 1) -> None:
        """Log an error."""
        self._log_header()
        indent_prefix = "  " * indent
        logger.error(f"{indent_prefix}├─ ERROR: {title}")
        logger.error(f"{indent_prefix}│   {str(error)}")

