from __future__ import annotations

import copy
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta
from queue import Empty, Queue
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace

from apps.accounts.models import BusinessProfile
from apps.conversations.models import ConversationSender
from apps.core.logging_utils import LogEmoji
from apps.rag.ai_orchestrator import (
    ActionDispatcher,
    AiOrchestratorService,
    StreamingTurnContext,
)
from apps.mcp.sanitizer import sanitize_placeholder_thinking, sanitize_text, sanitize_with_diagnostics
from apps.llm.llm_provider import _emit_stream_chunks, load_default_provider
from apps.conversations.portal import (
    ChatPortalService,
    PortalAgentSummary,
    PortalBusinessSummary,
    PortalMessage,
    PortalNotFoundError,
    PortalSessionBootstrap,
    PortalSessionState,
    PortalValidationError,
)

logger = logging.getLogger(__name__)
TRACER = otel_trace.get_tracer(__name__)

CONTEXT_STATUS_CODES = {
    "searching_knowledge",
    "searching_start",
    "searching_complete",
    "reading_document",
    "reading_start",
    "reading_complete",
    "planning_actions",
    "responding",
    "answer_started",
    "answer_finalized",
    "clarifying",
}


def _queue_put(queue, item):
    put = getattr(queue, "put", None)
    if callable(put):
        put(item)
    else:
        queue.append(item)


def _enqueue_status_events(queue, *, code: str, label: str | None = None, meta: dict | None = None) -> None:
    code_value = (code or "").strip()
    if not code_value:
        return
    label_value = label or code_value.replace("_", " ").title()
    payload: dict[str, object] = {"type": "status", "state": code_value, "label": label_value}
    if meta:
        payload["meta"] = meta
    if code_value in CONTEXT_STATUS_CODES:
        ctx_payload = {"type": "context_progress", "state": code_value, "label": label_value}
        if meta:
            ctx_payload["meta"] = meta
        _queue_put(queue, ctx_payload)
    _queue_put(queue, payload)


LOW_INTENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(hi|hello|hey|hola|hallo|مرحبا|السلام عليكم|as-salamu alaykum)\b", re.IGNORECASE),
    re.compile(r"^(good\s+(morning|evening|afternoon|day|night))\b", re.IGNORECASE),
    re.compile(r"^(thanks|thank you|gracias|gracias|shukran|شكرا)\b", re.IGNORECASE),
    re.compile(r"^(test|testing)\b", re.IGNORECASE),
)
LOW_INTENT_SIMPLE = {
    "hi",
    "hello",
    "hey",
    "hola",
    "مرحبا",
    "salam",
    "salaam",
    "as-salamu alaykum",
    "thanks",
    "thank you",
    "gracias",
    "شكرا",
    "test",
    "testing",
}
STRUCTURED_KEYWORDS = {
    "account",
    "action",
    "appointment",
    "apply",
    "balance",
    "book",
    "case",
    "cancel",
    "card",
    "complaint",
    "contact",
    "contract",
    "escalate",
    "fee",
    "help",
    "issue",
    "lead",
    "meeting",
    "order",
    "payment",
    "phone",
    "price",
    "problem",
    "refund",
    "schedule",
    "status",
    "support",
    "ticket",
    "update",
}


def _is_low_intent_message(text: str) -> bool:
    """Detect short greetings/acks that do not require planner metadata."""

    normalized = (text or "").strip()
    if not normalized:
        return False
    if len(normalized) > 80:
        return False
    lowered = normalized.lower()
    if any(keyword in lowered for keyword in STRUCTURED_KEYWORDS):
        return False
    if any(ch.isdigit() for ch in lowered):
        return False
    letters = sum(ch.isalpha() for ch in lowered)
    if letters == 0:
        return True
    for pattern in LOW_INTENT_PATTERNS:
        if pattern.match(lowered):
            return True
    if lowered in LOW_INTENT_SIMPLE:
        return True
    return False


def _tool_activity_present(stream_context: StreamingTurnContext | None) -> bool:
    if not stream_context:
        return False
    tool_context = getattr(stream_context, "tool_context", None)
    if not tool_context:
        return False
    signal_attrs = (
        "tool_trace",
        "knowledge_results",
        "knowledge_reads",
        "identifier_filters",
        "coverage_ledger",
        "table_aggregate_rows",
    )
    for attr in signal_attrs:
        values = getattr(tool_context, attr, None)
        if values:
            return True
    if getattr(stream_context, "knowledge_payload", None):
        return True
    return False


# Tools that purely retrieve knowledge (no CRM side effects)
_KNOWLEDGE_ONLY_TOOLS = frozenset({
    "search_knowledge",
    "read_document",
    "read_knowledge",
    "table_aggregate",
    "dataset_query",
    "query_dataset",
    "list_tables",
})

# CRM tools that need planner for action extraction
_CRM_TOOLS = frozenset({
    "create_case",
    "update_case_details",
    "add_case_history",
    "create_lead",
    "create_customer",
    "update_customer",
    "get_customer",
})


def _has_crm_signals(stream_context: StreamingTurnContext | None, user_message: str) -> bool:
    """
    Detect if this turn has CRM-related signals that warrant running the planner.

    Returns True if:
    - CRM tools were called (create_case, create_lead, etc.)
    - Non-knowledge tools were called
    - User message contains identifiers (email, phone, digits)
    - User message contains complaint/escalation signals
    """
    # Check tool trace for CRM or non-knowledge tools
    if stream_context:
        tool_context = getattr(stream_context, "tool_context", None)
        if tool_context:
            tool_trace = getattr(tool_context, "tool_trace", [])
            if isinstance(tool_trace, list):
                for entry in tool_trace:
                    if not isinstance(entry, dict):
                        continue
                    tool_name = entry.get("tool", "")
                    # If any CRM tool was called, definitely need planner
                    if tool_name in _CRM_TOOLS:
                        return True
                    # If tool is not knowledge-only, might have side effects
                    if tool_name and tool_name not in _KNOWLEDGE_ONLY_TOOLS:
                        return True
            # Check for identifier filters (indicates customer data was involved)
            identifier_filters = getattr(tool_context, "identifier_filters", None)
            if identifier_filters:
                return True

    # Check message for CRM signals
    lowered = (user_message or "").lower()
    # Identifiers (email, phone, digits) suggest action requests
    if any(ch.isdigit() for ch in lowered):
        return True
    if "@" in lowered:  # Email pattern
        return True
    # Complaint/escalation keywords
    crm_keywords = {
        "complaint", "complain", "angry", "frustrated", "escalate",
        "manager", "supervisor", "refund", "cancel", "urgent",
        "problem", "issue", "broken", "not working", "help me",
    }
    if any(kw in lowered for kw in crm_keywords):
        return True

    return False


def _business_planner_override(business: BusinessProfile | None) -> bool | None:
    if not business:
        return None
    metadata = business.metadata if isinstance(business.metadata, dict) else {}
    if not metadata:
        return None
    enabled = metadata.get("portal_planner_enabled")
    if isinstance(enabled, bool):
        return enabled
    disabled = metadata.get("portal_disable_planner")
    if isinstance(disabled, bool):
        return not disabled
    return None


def _planner_decision(
    *,
    conversation,
    user_message: str,
    stream_context: StreamingTurnContext | None,
) -> tuple[bool, str | None]:
    if getattr(settings, "PORTAL_FORCE_PLANNER", False):
        return True, None
    override = _business_planner_override(getattr(conversation, "business_profile", None))
    if override is not None:
        return override, "business_override" if not override else None
    # Skip planner for low intent messages (greetings, etc.)
    if _is_low_intent_message(user_message):
        return False, "low_intent"
    # Only run planner if CRM signals are present (not just any tool activity)
    # This saves ~9s latency for pure knowledge Q&A turns
    if _has_crm_signals(stream_context, user_message):
        return True, None
    # Knowledge-only turns don't need planner
    if _tool_activity_present(stream_context):
        return False, "knowledge_only"
    # No tool activity and no CRM signals - default to running planner
    # (might be a complex intent that didn't trigger tools)
    return True, None


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

    def log_error(self, title: str, error: Exception | str, *, indent: int = 1) -> None:
        """Log an error."""
        self._log_header()
        indent_prefix = "  " * indent
        logger.error(f"{indent_prefix}├─ ERROR: {title}")
        logger.error(f"{indent_prefix}│   {str(error)}")




def _service() -> ChatPortalService:
    return ChatPortalService()


def _json_error(code: str, message: str, *, status: int = 400, extra: dict | None = None) -> JsonResponse:
    payload: dict[str, object] = {"error": {"code": code, "message": message}}
    if extra:
        payload["error"].update(extra)
    return JsonResponse(payload, status=status)


def _parse_json_body(request: HttpRequest) -> dict:
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PortalValidationError("Invalid JSON payload") from exc


def _business_prefers_mcp(business: BusinessProfile | None) -> bool:
    """
    Evaluate whether a business should use the MCP orchestrator.

    Business metadata can override the global setting via the key
    `mcp_orchestrator_enabled`. When unset, the global
    RAG_USE_MCP_ORCHESTRATOR flag is used.
    """

    global_default = getattr(settings, "RAG_USE_MCP_ORCHESTRATOR", False)
    if business is None:
        return global_default
    metadata = business.metadata if isinstance(business.metadata, dict) else {}
    override = metadata.get("mcp_orchestrator_enabled")
    if override is None:
        return global_default
    return bool(override)


def _business_to_dict(summary: PortalBusinessSummary) -> dict:
    return {"id": str(summary.id), "name": summary.name, "slug": summary.slug}


def _agent_to_dict(summary: PortalAgentSummary) -> dict:
    return {
        "id": str(summary.id),
        "name": summary.name,
        "role": summary.role,
        "slug": summary.slug,
        "shareable_path": summary.shareable_path,
    }


def _session_to_dict(session: PortalSessionState) -> dict:
    return {
        "conversation_id": str(session.conversation_id),
        "session_token": session.session_token,
        "status": session.status,
        "started_at": session.started_at.isoformat(),
        "expires_at": session.expires_at.isoformat() if session.expires_at else None,
    }


def _message_to_dict(message: PortalMessage) -> dict:
    return {
        "id": str(message.id),
        "sender": message.sender,
        "body": message.body,
        "sent_at": message.sent_at.isoformat(),
        "metadata": message.metadata,
    }


def _bootstrap_to_dict(result: PortalSessionBootstrap) -> dict:
    return {
        "business": _business_to_dict(result.business),
        "agent": _agent_to_dict(result.agent),
        "session": _session_to_dict(result.session),
        "messages": [_message_to_dict(msg) for msg in result.messages],
    }


@require_GET
def resolve_portal_handle(request: HttpRequest, business_slug: str, agent_slug: str) -> JsonResponse:
    service = _service()
    try:
        business, agent = service.resolve_handle(business_slug, agent_slug)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)
    return JsonResponse(
        {
            "business": {"id": str(business.id), "name": business.name, "slug": business.slug},
            "agent": {
                "id": str(agent.id),
                "name": agent.name,
                "role": agent.role or "AI Assistant",
                "slug": agent.slug,
                "shareable_path": agent.shareable_path,
            },
        }
    )


@csrf_exempt
@require_POST
def bootstrap_session(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    business_slug = (payload.get("business_slug") or payload.get("businessSlug") or "").strip()
    agent_slug = (payload.get("agent_slug") or payload.get("agentSlug") or "").strip()
    existing_session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip() or None
    metadata = payload.get("metadata") or {}

    if not business_slug or not agent_slug:
        return _json_error("validation_error", "business_slug and agent_slug are required.")

    try:
        result = service.bootstrap_session(
            business_slug=business_slug,
            agent_slug=agent_slug,
            existing_session_token=existing_session_token,
            metadata=metadata,
        )
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    return JsonResponse(_bootstrap_to_dict(result), status=200)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def messages_endpoint(request: HttpRequest) -> JsonResponse:
    service = _service()
    if request.method == "GET":
        session_token = request.GET.get("session_token") or request.GET.get("sessionToken")
        if not session_token:
            return _json_error("validation_error", "session_token is required")
        limit_param = request.GET.get("limit")
        limit = None
        if limit_param:
            try:
                limit = max(1, min(200, int(limit_param)))
            except ValueError:
                return _json_error("validation_error", "limit must be an integer between 1 and 200")
        try:
            messages = service.list_messages(session_token=session_token, limit=limit)
            session = service.get_session_state(session_token=session_token)
        except PortalNotFoundError as exc:
            return _json_error("not_found", str(exc), status=404)
        return JsonResponse(
            {"session": _session_to_dict(session), "messages": [_message_to_dict(msg) for msg in messages]}
        )

    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    body = (payload.get("body") or "").strip()
    metadata = payload.get("metadata") or {}

    try:
        message = service.append_message(
            session_token=session_token,
            sender=ConversationSender.CUSTOMER,
            body=body,
            metadata=metadata,
        )
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    return JsonResponse({"message": _message_to_dict(message)}, status=201)


@csrf_exempt
@require_POST
def submit_csat(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    try:
        score = int(payload.get("score"))
    except (TypeError, ValueError):
        return _json_error("validation_error", "score must be an integer between 1 and 5")
    comment = (payload.get("comment") or "").strip() or None

    try:
        session = service.record_csat(session_token=session_token, score=score, comment=comment)
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    return JsonResponse({"session": _session_to_dict(session)}, status=200)


@csrf_exempt
@require_POST
def submit_feedback(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    feedback_type = (payload.get("feedback_type") or payload.get("feedbackType") or "").strip()
    if not session_token or not feedback_type:
        return _json_error("validation_error", "session_token and feedback_type are required.")
    message_id_value = payload.get("message_id") or payload.get("messageId")
    message_id: uuid.UUID | None = None
    if message_id_value:
        try:
            message_id = uuid.UUID(str(message_id_value))
        except (TypeError, ValueError):
            return _json_error("validation_error", "message_id must be a valid UUID.")
    feedback_payload = {
        "query_text": payload.get("query_text"),
        "expected_behavior": payload.get("expected_behavior"),
        "expected_entities": payload.get("expected_entities") or [],
        "expected_aliases": payload.get("expected_aliases") or [],
        "notes": payload.get("notes"),
        "auto_promote": payload.get("auto_promote", True),
    }
    try:
        feedback = service.record_feedback(
            session_token=session_token,
            feedback_type=feedback_type,
            message_id=message_id,
            payload=feedback_payload,
        )
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    return JsonResponse(
        {
            "feedback": {
                "id": str(feedback.id),
                "feedback_type": feedback.feedback_type,
                "created_at": feedback.created_at.isoformat(),
            }
        },
        status=201,
    )


def _verification_hash(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _mask_verification_destination(method: str, destination: str) -> str:
    text = (destination or "").strip()
    if not text:
        return ""
    if method == "email":
        if "@" not in text:
            return "[EMAIL]"
        local, _, domain = text.partition("@")
        local = local.strip()
        domain = domain.strip()
        if not domain:
            return "[EMAIL]"
        return f"{local[:1] or '*'}***@{domain}"
    if method == "phone":
        digits = re.sub(r"\D", "", text)
        if len(digits) < 4:
            return "***"
        return f"***{digits[-2:]}"
    return "***"


def _portal_verification_policy(business: BusinessProfile | None) -> dict[str, object]:
    enabled = bool(getattr(settings, "MCP_VERIFIED_LOOKUP_ENABLED", True))
    require_for_pii = bool(getattr(settings, "MCP_VERIFIED_LOOKUP_REQUIRE_FOR_PII", True))
    allow_customer_match = bool(getattr(settings, "MCP_VERIFIED_LOOKUP_ALLOW_CUSTOMER_MATCH", True))

    meta = getattr(business, "metadata", None) if business else None
    if isinstance(meta, Mapping):
        cfg = meta.get("verified_lookup") or meta.get("verified_lookup_policy") or {}
        if isinstance(cfg, Mapping):
            if cfg.get("enabled") is not None:
                enabled = bool(cfg.get("enabled"))
            if cfg.get("require_for_pii") is not None:
                require_for_pii = bool(cfg.get("require_for_pii"))
            if cfg.get("requireForPii") is not None:
                require_for_pii = bool(cfg.get("requireForPii"))
            if cfg.get("allow_customer_match") is not None:
                allow_customer_match = bool(cfg.get("allow_customer_match"))
            if cfg.get("allowCustomerMatch") is not None:
                allow_customer_match = bool(cfg.get("allowCustomerMatch"))
    return {
        "enabled": enabled,
        "require_for_pii": require_for_pii,
        "allow_customer_match": allow_customer_match,
    }


def _conversation_is_verified_for_lookup(conversation) -> bool:
    metadata = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    marker = metadata.get("verified_lookup") if isinstance(metadata, Mapping) else None
    if marker is True:
        return True
    if isinstance(marker, Mapping):
        status = str(marker.get("status") or marker.get("state") or "").strip().lower()
        if status in {"verified", "ok", "passed"}:
            return True
        if marker.get("verified") is True:
            return True
    return False


@csrf_exempt
@require_POST
def portal_verification_status(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    if not session_token:
        return _json_error("validation_error", "session_token is required.")
    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=False)
        session = service.get_session_state(session_token=session_token, conversation=conversation)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    policy = _portal_verification_policy(getattr(conversation, "business_profile", None))
    verified = _conversation_is_verified_for_lookup(conversation)
    return JsonResponse(
        {
            "session": _session_to_dict(session),
            "verified_lookup": {
                "enabled": bool(policy.get("enabled")),
                "verified": bool(verified),
            },
        }
    )


@csrf_exempt
@require_POST
def portal_verification_start(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    method = str(payload.get("method") or "").strip().lower()
    destination = str(payload.get("destination") or payload.get("value") or "").strip()
    if not session_token or not method or not destination:
        return _json_error("validation_error", "session_token, method, and destination are required.")
    if method not in {"email", "phone"}:
        return _json_error("validation_error", "method must be 'email' or 'phone'.")

    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=False)
        session = service.get_session_state(session_token=session_token, conversation=conversation)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    policy = _portal_verification_policy(getattr(conversation, "business_profile", None))
    if not bool(policy.get("enabled")):
        return _json_error("verification_disabled", "Verification is disabled for this business.", status=403)
    if _conversation_is_verified_for_lookup(conversation):
        return JsonResponse(
            {
                "session": _session_to_dict(session),
                "verified_lookup": {"verified": True},
            },
            status=200,
        )

    ttl_seconds = int(getattr(settings, "PORTAL_VERIFICATION_OTP_TTL_SECONDS", 600) or 600)
    ttl_seconds = max(60, min(ttl_seconds, 3600))
    cooldown_seconds = int(getattr(settings, "PORTAL_VERIFICATION_RESEND_COOLDOWN_SECONDS", 30) or 30)
    cooldown_seconds = max(5, min(cooldown_seconds, 300))
    max_attempts = int(getattr(settings, "PORTAL_VERIFICATION_MAX_ATTEMPTS", 5) or 5)
    max_attempts = max(3, min(max_attempts, 10))

    cooldown_key = f"portal:verify:cooldown:{conversation.id}:{method}"
    if cache.get(cooldown_key):
        return _json_error("rate_limited", "Please wait a moment before requesting another code.", status=429)
    cache.set(cooldown_key, True, timeout=cooldown_seconds)

    challenge_id = uuid.uuid4()
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = timezone.now() + timedelta(seconds=ttl_seconds)
    record = {
        "method": method,
        "destination_sha256": _verification_hash(destination),
        "code_sha256": _verification_hash(code),
        "attempts": 0,
        "max_attempts": max_attempts,
        "expires_at": expires_at.isoformat(),
    }
    cache_key = f"portal:verify:challenge:{conversation.id}:{challenge_id}"
    cache.set(cache_key, record, timeout=ttl_seconds)

    # NOTE: For local/dev we can return the code to simplify end-to-end testing.
    return_code = bool(getattr(settings, "PORTAL_VERIFICATION_DEBUG_RETURN_CODE", False) or getattr(settings, "DEBUG", False))
    response: dict[str, object] = {
        "session": _session_to_dict(session),
        "challenge_id": str(challenge_id),
        "method": method,
        "destination": _mask_verification_destination(method, destination),
        "expires_in": ttl_seconds,
    }
    if return_code:
        response["debug_code"] = code
    return JsonResponse(response, status=200)


@csrf_exempt
@require_POST
def portal_verification_confirm(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    challenge_id_raw = str(payload.get("challenge_id") or payload.get("challengeId") or "").strip()
    code = str(payload.get("code") or "").strip()
    if not session_token or not challenge_id_raw or not code:
        return _json_error("validation_error", "session_token, challenge_id, and code are required.")
    try:
        challenge_id = uuid.UUID(challenge_id_raw)
    except (TypeError, ValueError):
        return _json_error("validation_error", "challenge_id must be a valid UUID.")

    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=False)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    cache_key = f"portal:verify:challenge:{conversation.id}:{challenge_id}"
    record = cache.get(cache_key)
    if not isinstance(record, Mapping):
        return _json_error("verification_expired", "Verification challenge expired. Request a new code.", status=410)

    expires_at_raw = record.get("expires_at")
    expires_at: datetime | None = None
    if isinstance(expires_at_raw, str) and expires_at_raw:
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except ValueError:
            expires_at = None
    if expires_at and timezone.is_naive(expires_at):
        expires_at = timezone.make_aware(expires_at, timezone.get_current_timezone())
    if expires_at and expires_at < timezone.now():
        cache.delete(cache_key)
        return _json_error("verification_expired", "Verification challenge expired. Request a new code.", status=410)

    attempts = int(record.get("attempts") or 0)
    max_attempts = int(record.get("max_attempts") or 5)
    if attempts >= max_attempts:
        cache.delete(cache_key)
        return _json_error("too_many_attempts", "Too many attempts. Request a new code.", status=429)

    expected = str(record.get("code_sha256") or "")
    provided = _verification_hash(code)
    if not expected or not hmac.compare_digest(expected, provided):
        attempts += 1
        record_out = dict(record)
        record_out["attempts"] = attempts
        ttl_remaining = 60
        if expires_at:
            ttl_remaining = max(1, int((expires_at - timezone.now()).total_seconds()))
        cache.set(cache_key, record_out, timeout=ttl_remaining)
        return _json_error(
            "invalid_code",
            "Invalid code. Please try again.",
            status=400,
            extra={"attempts_left": max(0, max_attempts - attempts)},
        )

    convo_meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), dict) else {}
    meta = dict(convo_meta)
    meta["verified_lookup"] = {
        "status": "verified",
        "method": record.get("method"),
        "verified_at": timezone.now().isoformat(),
    }
    conversation.metadata = meta
    conversation.save(update_fields=["metadata", "last_activity_at"])
    cache.delete(cache_key)

    session = service.get_session_state(session_token=session_token, conversation=conversation)
    return JsonResponse(
        {
            "session": _session_to_dict(session),
            "verified_lookup": {"verified": True},
        },
        status=200,
    )


@csrf_exempt
@require_POST
def stream_send(request: HttpRequest) -> StreamingHttpResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError:
        return StreamingHttpResponse(status=400)

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    body = (payload.get("body") or "").strip()
    metadata = payload.get("metadata") or {}

    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=False)
    except PortalNotFoundError:
        return StreamingHttpResponse(status=404)

    customer_message: PortalMessage | None = None
    try:
        customer_message = service.append_message(
            session_token=session_token,
            sender=ConversationSender.CUSTOMER,
            body=body,
            metadata=metadata,
            conversation=conversation,
        )
    except PortalValidationError:
        return StreamingHttpResponse(status=400)
    except PortalNotFoundError:
        return StreamingHttpResponse(status=404)

    agent = conversation.agent_profile
    if not agent:
        return StreamingHttpResponse(status=500)

    use_mcp = _business_prefers_mcp(conversation.business_profile)
    trace_logger = PortalTraceLogger(
        conversation=conversation,
        agent=agent,
        session_token=session_token,
        orchestrator_mode="mcp" if use_mcp else "legacy",
    )
    trace_logger.log("request.received", detail=f"body={body}")
    if metadata:
        trace_logger.log("request.metadata", detail=trace_logger.format_data(metadata), indent=1)
    if customer_message:
        trace_logger.log("customer.message_recorded", detail=f"id={customer_message.id}", indent=1)
    def _provider_label(provider_obj: Any) -> str:
        if provider_obj is None:
            return "unknown"
        for attr in ("name", "label", "model_name"):
            value = getattr(provider_obj, attr, None)
            if isinstance(value, str) and value:
                return value
        return provider_obj.__class__.__name__

    if use_mcp:
        from apps.llm.llm_provider import load_mcp_provider
        from apps.mcp.orchestrator import McpOrchestratorService

        provider = load_mcp_provider()
        orchestrator = McpOrchestratorService(agent=agent, provider=provider)
        trace_logger.log(
            "orchestrator.selected",
            detail=f"mode=mcp provider={_provider_label(provider)}",
            indent=1,
        )
    else:
        provider = load_default_provider()
        orchestrator = AiOrchestratorService(agent=agent, provider=provider)
        trace_logger.log(
            "orchestrator.selected",
            detail=f"mode=legacy provider={_provider_label(provider)}",
            indent=1,
        )
    dispatcher = ActionDispatcher(agent=agent)

    def serialize_action_results(results):
        payloads = []
        for result in results:
            payloads.append(
                {
                    "action": result.action.value,
                    "status": result.status,
                    "metadata": result.metadata,
                    "error": result.error,
                }
            )
        return payloads

    def serialize_planned_actions(planned):
        payloads = []
        for action in planned:
            payloads.append(
                {
                    "action": action.action.value,
                    "status": "queued",
                    "metadata": action.payload,
                    "error": None,
                }
            )
        return payloads

    stream_queue: Queue = Queue()
    stream_sentinel = object()
    finalize_queue: Queue = Queue()
    finalize_sentinel = object()
    actions_queue: Queue = Queue()
    actions_sentinel = object()
    stream_complete = threading.Event()
    plan_holder: dict[str, Any] = {}
    streamed_text_chunks: list[str] = []
    state_machine_enabled = getattr(settings, "PORTAL_STREAM_STATE_MACHINE", False)
    plan_holder["metadata_version"] = 1
    plan_holder["session_status"] = conversation.status
    plan_holder["spinner_text"] = None
    reserved_message_id = uuid.uuid4() if state_machine_enabled else None
    if reserved_message_id:
        plan_holder["pending_message_id"] = reserved_message_id
    spinner_state = {"text": None, "pending": True}
    spinner_phase_state = {
        "searching": {"active": False, "index": 0, "label": None, "meta": None, "timer": None},
        "reading": {"active": False, "index": 0, "label": None, "meta": None, "timer": None},
    }
    spinner_phase_interval_setting = getattr(settings, "PORTAL_SPINNER_PHASE_INTERVAL", 5.5)
    try:
        spinner_phase_interval = float(spinner_phase_interval_setting)
    except (TypeError, ValueError):
        spinner_phase_interval = 5.5
    if spinner_phase_interval < 0:
        spinner_phase_interval = 0.0

    def _search_base_text(label: str | None, meta: dict | None) -> str:
        return label or "Searching knowledge…"

    def _search_variant_text(label: str | None, meta: dict | None) -> str:
        query_text = ""
        if isinstance(meta, dict):
            raw_query = meta.get("query")
            if isinstance(raw_query, str):
                query_text = raw_query.strip()
        if query_text:
            return f"Exploring deeper insights for {query_text[:60]}…"
        return "Exploring deeper insights…"

    def _reading_base_text(label: str | None, meta: dict | None) -> str:
        return label or "Reading document…"

    def _reading_variant_text(label: str | None, meta: dict | None) -> str:
        doc_hint = ""
        if isinstance(meta, dict):
            doc_hint = str(meta.get("document_id") or "").strip()
        if not doc_hint and isinstance(label, str):
            parts = label.split(":", 1)
            if len(parts) == 2:
                doc_hint = parts[1].strip()
        hint_suffix = f" ({doc_hint[:40]})" if doc_hint else ""
        return f"Gathering more context…{hint_suffix}"

    SPINNER_PHASE_VARIANTS: dict[str, tuple[Callable[[str | None, dict | None], str], ...]] = {
        "searching": (_search_base_text, _search_variant_text),
        "reading": (_reading_base_text, _reading_variant_text),
    }

    def _current_message_id() -> str:
        raw_id = plan_holder.get("ai_message_id") or plan_holder.get("pending_message_id")
        if raw_id:
            return str(raw_id)
        return ""

    def _current_session_status() -> str | None:
        status = plan_holder.get("session_status")
        if status:
            return status
        return conversation.status

    def _next_metadata_version() -> int:
        version = int(plan_holder.get("metadata_version") or 1) + 1
        plan_holder["metadata_version"] = version
        return version

    def _emit_spinner_status(
        raw_text: str | None,
        *,
        pending: bool = True,
        fallback: str | None = "Working...",
        allow_empty: bool = False,
    ) -> None:
        if not state_machine_enabled:
            return
        text_value = sanitize_placeholder_thinking(raw_text, fallback=fallback)
        if text_value is None and allow_empty:
            text_value = ""
        if text_value is None:
            return
        if spinner_state["text"] == text_value and spinner_state["pending"] == pending:
            return
        spinner_state["text"] = text_value
        spinner_state["pending"] = pending
        plan_holder["spinner_text"] = text_value or None
        payload = {
            "type": "spinnerStatus",
            "message_id": _current_message_id(),
            "spinner_text": text_value,
            "pending": pending,
        }
        stream_queue.put(payload)

    def _phase_variant_text(phase: str, index: int, label: str | None, meta: dict | None) -> str:
        variants = SPINNER_PHASE_VARIANTS.get(phase)
        if not variants:
            return label or "Working..."
        variant_fn = variants[index % len(variants)]
        try:
            return variant_fn(label, meta)
        except Exception:
            return label or "Working..."

    def _cancel_phase_timer(phase: str) -> None:
        state = spinner_phase_state.get(phase)
        if not state:
            return
        timer = state.get("timer")
        if timer:
            timer.cancel()
            state["timer"] = None

    def _advance_phase_spinner(phase: str) -> None:
        state = spinner_phase_state.get(phase)
        if not state or not state.get("active"):
            return
        variants = SPINNER_PHASE_VARIANTS.get(phase)
        if not variants:
            return
        if state["index"] + 1 >= len(variants):
            state["active"] = False
            return
        state["index"] += 1
        text = _phase_variant_text(phase, state["index"], state.get("label"), state.get("meta"))
        _emit_spinner_status(text)
        _schedule_phase_rotation(phase)

    def _schedule_phase_rotation(phase: str) -> None:
        state = spinner_phase_state.get(phase)
        if not state or not state.get("active"):
            return
        variants = SPINNER_PHASE_VARIANTS.get(phase)
        if not variants or len(variants) <= 1 or spinner_phase_interval <= 0:
            return
        if state["index"] >= len(variants) - 1:
            return
        _cancel_phase_timer(phase)
        timer = threading.Timer(spinner_phase_interval, lambda: _advance_phase_spinner(phase))
        timer.daemon = True
        state["timer"] = timer
        timer.start()

    def _set_phase_spinner(phase: str, label: str | None, meta: dict | None, *, reset_index: bool) -> None:
        state = spinner_phase_state.setdefault(
            phase,
            {"active": False, "index": 0, "label": None, "meta": None, "timer": None},
        )
        if reset_index or not state["active"]:
            state["index"] = 0
        state["active"] = True
        state["label"] = label or state.get("label")
        if isinstance(meta, dict) and meta:
            state["meta"] = meta
        elif not state.get("meta"):
            state["meta"] = {}
        text = _phase_variant_text(phase, state["index"], state.get("label"), state.get("meta"))
        _emit_spinner_status(text)
        _schedule_phase_rotation(phase)

    def _reset_phase_spinner(phase: str) -> None:
        state = spinner_phase_state.get(phase)
        if not state:
            return
        _cancel_phase_timer(phase)
        state["active"] = False
        state["index"] = 0
        state["label"] = None
        state["meta"] = None

    def _progressive_spinner_update(code: str | None, label: str | None, meta: dict | None) -> bool:
        if not state_machine_enabled:
            return False
        phase_map = {
            "searching_start": ("searching", True),
            "searching_knowledge": ("searching", False),
            "reading_start": ("reading", True),
            "reading_document": ("reading", False),
        }
        reset_map = {
            "searching_complete": "searching",
            "reading_complete": "reading",
        }
        phase_entry = phase_map.get(code or "")
        if phase_entry:
            phase, reset_index = phase_entry
            _set_phase_spinner(phase, label, meta, reset_index=reset_index)
            return True
        reset_phase = reset_map.get(code or "")
        if reset_phase:
            _reset_phase_spinner(reset_phase)
        return False

    def _turn_pending_payload(text: str, *, pending: bool = True) -> dict[str, object]:
        payload = {
            "message_id": _current_message_id(),
            "text": text,
            "pending": pending,
            "session_status": _current_session_status(),
            "metadata_version": plan_holder.get("metadata_version", 1),
        }
        spinner_text = plan_holder.get("spinner_text")
        if spinner_text:
            payload["spinner_text"] = spinner_text
        return payload

    def on_response_text_delta(chunk: str) -> None:
        if chunk:
            stream_queue.put(chunk)

    def on_status_change(state) -> None:
        if not state:
            return
        code: str | None = None
        label: str | None = None
        meta: dict | None = None
        if isinstance(state, str):
            code = state.strip()
        elif isinstance(state, dict):
            raw_code = state.get("code") or state.get("state")
            if isinstance(raw_code, str):
                code = raw_code.strip()
            raw_label = state.get("label")
            if isinstance(raw_label, str):
                label = raw_label.strip()
            raw_meta = state.get("meta")
            if isinstance(raw_meta, dict):
                meta = raw_meta
        if not code:
            return
        trace_logger.log_status(code, label=label, meta=meta)
        _enqueue_status_events(stream_queue, code=code, label=label, meta=meta)
        if state_machine_enabled:
            if _progressive_spinner_update(code, label, meta):
                return
            if code in {"responding", "answer_started"}:
                _emit_spinner_status(label or "Drafting answer...")
            elif code == "clarifying":
                _emit_spinner_status(label or "Clarifying request…")
            elif code == "answer_finalized":
                _emit_spinner_status(label or "Finalizing answer…")
            elif code in {"stream_complete", "complete"}:
                _emit_spinner_status("", pending=False, fallback=None, allow_empty=True)

    def signal_stream_complete() -> None:
        if stream_complete.is_set():
            return
        stream_complete.set()
        trace_logger.log("stream.completed", indent=1)
        stream_queue.put({"type": "status", "state": "complete", "label": ""})
        logger.debug("Stream completion signaled for conversation %s", conversation.id)
        stream_queue.put(stream_sentinel)

    def on_placeholder_response(text: str) -> None:
        # Placeholder thinking is no longer surfaced via the spinner.
        return

    def on_spinner_update(text: str) -> None:
        # Spinner updates now rely solely on explicit status codes.
        return

    def run_planner_async(
        stream_context: StreamingTurnContext,
        persisted_text: str,
        ai_message_id: uuid.UUID | None,
        parent_ctx,
    ) -> None:
        close_old_connections()
        token = None
        if parent_ctx is not None:
            token = otel_context.attach(parent_ctx)
        try:
            should_run_planner, skip_reason = _planner_decision(
                conversation=conversation,
                user_message=body,
                stream_context=stream_context,
            )
            if not should_run_planner:
                trace_logger.log(
                    "planner.skipped",
                    indent=1,
                    extra={"reason": skip_reason or "auto"},
                )
                plan_holder["planner_skipped"] = True
                return
            tool_context = getattr(stream_context, "tool_context", None)
            plan = orchestrator.run_planner_only(
                conversation=conversation,
                user_message=body,
                answer_text=persisted_text,
                tool_context=tool_context,
            )
            if plan is None:
                trace_logger.log("planner.skipped", indent=1)
                return
            plan_holder["plan"] = plan
            trace_logger.log(
                "planner.completed",
                detail=f"planned_actions={len(plan.planned_actions)} extractions={len(plan.extractions)}",
                indent=1,
            )
            trace_logger.log(
                "plan.ready",
                detail=f"actions={len(plan.planned_actions)} extractions={len(plan.extractions)}",
                indent=1,
            )

            pending_actions = serialize_planned_actions(plan.planned_actions)
            existing_metadata = plan_holder.get("message_metadata") if isinstance(plan_holder.get("message_metadata"), dict) else {}
            message_metadata = dict(existing_metadata or {})
            message_metadata.update(
                {
                    "citations": [snippet.title for snippet in plan.citations],
                    "actions": pending_actions,
                    "diagnostics": plan.diagnostics,
                }
            )
            answer_confidence = None
            if plan.diagnostics:
                answer_confidence = plan.diagnostics.get("answer_confidence")
            if answer_confidence is not None:
                message_metadata["answer_confidence"] = answer_confidence
            if plan.ingestion_warnings:
                message_metadata["ingestion_warnings"] = [dict(item) for item in plan.ingestion_warnings]

            if ai_message_id:
                service.update_message(
                    session_token=session_token,
                    message_id=ai_message_id,
                    metadata=message_metadata,
                    conversation=conversation,
                )
            plan_holder["message_metadata"] = message_metadata

            updated_payload = dict(plan_holder.get("final_payload") or {})
            if answer_confidence is not None:
                updated_payload["answer_confidence"] = answer_confidence
            if plan.ingestion_warnings:
                updated_payload["ingestion_warnings"] = [dict(item) for item in plan.ingestion_warnings]
            plan_holder["final_payload"] = updated_payload or None
            if updated_payload:
                version = _next_metadata_version()
                updated_payload["metadata_version"] = version
                actions_queue.put(
                    {
                        "type": "turnUpdated",
                        "payload": {
                            "message_id": str(updated_payload.get("message_id") or ai_message_id or ""),
                            "text": updated_payload.get("text"),
                            "session_status": updated_payload.get("session_status"),
                            "answer_confidence": updated_payload.get("answer_confidence"),
                            "ingestion_warnings": updated_payload.get("ingestion_warnings"),
                            "metadata_version": version,
                        },
                    }
                )

            action_results = []
            if plan.planned_actions:
                action_results = dispatcher.execute(conversation=conversation, planned_actions=plan.planned_actions)
                trace_logger.log(
                    "actions.executed",
                    detail=f"count={len(action_results)}",
                    indent=2,
                    extra=[
                        {
                            "action": result.action.value,
                            "status": result.status,
                            "error": result.error,
                        }
                        for result in action_results
                    ],
                )
            if plan.extractions:
                service.store_extractions(
                    session_token=session_token,
                    items=((extraction.extraction_type, extraction.payload) for extraction in plan.extractions),
                    conversation=conversation,
                )
                logger.info(
                    "portal extractions stored conversation=%s count=%s",
                    conversation.id,
                    len(plan.extractions),
                )
                trace_logger.log(
                    "extractions.stored",
                    detail=f"count={len(plan.extractions)}",
                    indent=2,
                )

            if plan.planned_actions:
                serialized_actions = serialize_action_results(action_results)
                updated_metadata = copy.deepcopy(message_metadata)
                updated_metadata["actions"] = serialized_actions
                if ai_message_id:
                    service.update_message(
                        session_token=session_token,
                        message_id=ai_message_id,
                        metadata=updated_metadata,
                        conversation=conversation,
                    )
                actions_queue.put(
                    {
                        "type": "actionsComplete",
                        "message_id": str(ai_message_id or ""),
                        "actions": serialized_actions,
                    }
                )
            elif plan.extractions:
                actions_queue.put(
                    {
                        "type": "actionsComplete",
                        "message_id": str(ai_message_id or ""),
                        "actions": [],
                    }
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("planner async failed: %s", exc)
            trace_logger.log_error("planner", exc, indent=1)
            actions_queue.put(
                {
                    "type": "actionsError",
                    "message_id": str(ai_message_id or ""),
                    "error": str(exc),
                }
            )
        finally:
            if token is not None:
                otel_context.detach(token)
            close_old_connections()
            actions_queue.put(actions_sentinel)

    def persist_initial_response(stream_context: StreamingTurnContext, parent_ctx) -> None:
        close_old_connections()
        token = None
        if parent_ctx is not None:
            token = otel_context.attach(parent_ctx)
        trace_logger.log("finalize.started", indent=1)
        try:
            with TRACER.start_as_current_span("portal.finalize_turn") as finalize_span:
                if finalize_span.is_recording():
                    finalize_span.set_attribute("conversation.id", str(conversation.id))
                    finalize_span.set_attribute("business.id", str(conversation.business_profile_id))
                base_plan = orchestrator.finalize_turn(stream_context)
                plan_holder["plan"] = base_plan
                persist_text = base_plan.response_text or ""
                if not persist_text:
                    streamed_text = "".join(stream_context.streamed_chunks).strip() if stream_context.streamed_chunks else ""
                    if streamed_text:
                        persist_text = streamed_text
                if not persist_text:
                    persist_text = "(no content)"
                with TRACER.start_as_current_span("portal.finalize.sanitize") as sanitize_span:
                    response_text, _ = sanitize_with_diagnostics(
                        persist_text,
                        conversation=conversation,
                        stage="persisted_message",
                    )
                    if sanitize_span.is_recording():
                        sanitize_span.set_attribute("portal.sanitize_chars", len(persist_text))
                pending_actions = serialize_planned_actions(base_plan.planned_actions)
                message_metadata = {
                    "citations": [snippet.title for snippet in base_plan.citations],
                    "actions": pending_actions,
                    "diagnostics": base_plan.diagnostics,
                }
                if base_plan.diagnostics and base_plan.diagnostics.get("answer_confidence") is not None:
                    message_metadata["answer_confidence"] = base_plan.diagnostics.get("answer_confidence")
                if base_plan.ingestion_warnings:
                    message_metadata["ingestion_warnings"] = [dict(item) for item in base_plan.ingestion_warnings]
                with TRACER.start_as_current_span("portal.finalize.persist") as persist_span:
                    ai_message = service.append_message(
                        session_token=session_token,
                        sender=ConversationSender.AI,
                        body=response_text,
                        metadata=message_metadata,
                        conversation=conversation,
                        message_id=plan_holder.get("pending_message_id"),
                    )
                    if persist_span.is_recording():
                        persist_span.set_attribute("portal.actions.pending", len(pending_actions))
                try:
                    schedule_memory = getattr(orchestrator, "schedule_memory_update", None)
                    if callable(schedule_memory):
                        schedule_memory(
                            conversation=conversation,
                            user_message=body,
                            assistant_message=response_text,
                            expected_last_message_id=ai_message.id,
                        )
                except Exception:  # pragma: no cover - best effort background task
                    logger.exception("portal memory update scheduling failed")

                session_state = service.get_session_state(session_token=session_token, conversation=conversation)
                final_payload = {
                    "text": response_text,
                    "message_id": str(ai_message.id),
                    "session_status": session_state.status,
                    "metadata_version": plan_holder.get("metadata_version", 1),
                }
                if base_plan.diagnostics:
                    answer_confidence = base_plan.diagnostics.get("answer_confidence")
                    if answer_confidence is not None:
                        final_payload["answer_confidence"] = answer_confidence
                if base_plan.ingestion_warnings:
                    final_payload["ingestion_warnings"] = [dict(item) for item in base_plan.ingestion_warnings]
                plan_holder["session_status"] = session_state.status
                extra_payload: dict[str, Any] = {
                    "citations": [snippet.title for snippet in base_plan.citations],
                    "pending_actions": len(base_plan.planned_actions),
                }
                trace_logger.log(
                    "response.persisted",
                    detail=f"message_id={ai_message.id}",
                    indent=1,
                    extra=extra_payload,
                )
                plan_holder["message_metadata"] = message_metadata
                plan_holder["final_payload"] = final_payload
                plan_holder["ai_message_id"] = ai_message.id
                plan_holder["pending_message_id"] = ai_message.id

                threading.Thread(
                    target=run_planner_async,
                    args=(
                        stream_context,
                        response_text,
                        ai_message.id,
                        otel_context.get_current(),
                    ),
                    daemon=True,
                ).start()
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Orchestrator finalize failed: %s", exc)
            trace_logger.log_error("finalize", exc, indent=1)
            plan_holder["final_error"] = str(exc)
            actions_queue.put(actions_sentinel)
        finally:
            if token is not None:
                otel_context.detach(token)
            close_old_connections()
            finalize_queue.put(finalize_sentinel)

    def orchestrate(parent_ctx) -> None:
        close_old_connections()
        token = None
        if parent_ctx is not None:
            token = otel_context.attach(parent_ctx)
        try:
            trace_logger.log("orchestrator.turn.start", indent=1)
            with TRACER.start_as_current_span("portal.orchestrator.turn"):
                stream_context = orchestrator.stream_turn(
                    conversation=conversation,
                    user_message=body,
                    on_response_text_delta=on_response_text_delta,
                    on_status_change=on_status_change,
                    on_placeholder_response=on_placeholder_response,
                    on_stream_complete=signal_stream_complete,
                    on_spinner_update=on_spinner_update,
                )
                plan_holder["context"] = stream_context
                threading.Thread(
                    target=persist_initial_response,
                    args=(stream_context, otel_context.get_current()),
                    daemon=True,
                ).start()
                signal_stream_complete()
                trace_logger.log("orchestrator.turn.complete", indent=1)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Orchestrator turn failed: %s", exc)
            plan_holder["error"] = str(exc)
            trace_logger.log_error("orchestrator.turn", exc, indent=1)
            signal_stream_complete()
            finalize_queue.put(finalize_sentinel)
            actions_queue.put(actions_sentinel)
        finally:
            if token is not None:
                otel_context.detach(token)
            close_old_connections()

    request_context = otel_context.get_current()
    worker = threading.Thread(target=orchestrate, args=(request_context,), daemon=True)
    worker.start()

    def _legacy_event_stream() -> Iterable[str]:
        streamed_from_provider = False
        while True:
            try:
                chunk = stream_queue.get(timeout=0.25)
            except Empty:
                if worker.is_alive() or not stream_complete.is_set():
                    continue
                break
            if chunk is stream_sentinel:
                break
            if isinstance(chunk, dict):
                if chunk.get("type") == "context_progress":
                    state_value = chunk.get("state")
                    label_value = chunk.get("label")
                    data: dict[str, object] = {}
                    if isinstance(state_value, str):
                        data["state"] = state_value
                    if isinstance(label_value, str):
                        data["label"] = label_value
                    meta_value = chunk.get("meta")
                    if isinstance(meta_value, dict):
                        data["meta"] = meta_value
                    yield "event: context_progress\n"
                    yield f"data: {json.dumps(data)}\n\n"
                    continue
                if chunk.get("type") == "status":
                    state_value = chunk.get("state")
                    label_value = chunk.get("label")
                    data: dict[str, object] = {}
                    if isinstance(state_value, str):
                        data["state"] = state_value
                    if isinstance(label_value, str):
                        data["label"] = label_value
                    meta_value = chunk.get("meta")
                    if isinstance(meta_value, dict):
                        data["meta"] = meta_value
                    yield "event: status\n"
                    yield f"data: {json.dumps(data)}\n\n"
                    continue
                if chunk.get("type") == "spinnerStatus":
                    data = {
                        "message_id": chunk.get("message_id"),
                        "text": chunk.get("spinner_text"),
                        "pending": chunk.get("pending"),
                    }
                    yield "event: spinnerStatus\n"
                    yield f"data: {json.dumps(data)}\n\n"
                    continue
            streamed_from_provider = True
            chunk_text = str(chunk)
            streamed_text_chunks.append(chunk_text)
            yield "event: delta\n"
            yield f"data: {json.dumps({'text': chunk_text})}\n\n"
        streamed_text = "".join(streamed_text_chunks)
        normalized_streamed = streamed_text.strip()

        session_status: str | None = None
        try:
            session_state = service.get_session_state(session_token=session_token, conversation=conversation)
            session_status = session_state.status
        except PortalNotFoundError:
            session_status = None

        context: StreamingTurnContext | None = None
        need_context_for_final = (not streamed_from_provider) or not normalized_streamed
        if need_context_for_final:
            worker.join()
            context = plan_holder.get("context")
            if not context:
                error_message = plan_holder.get("error", "AI orchestration failed")
                yield "event: error\n"
                yield f"data: {json.dumps(error_message)}\n\n"
                return
            if not streamed_from_provider:
                stream_text = "".join(context.streamed_chunks).strip() or context.response_text or ""
                reconstructed: list[str] = []
                _emit_stream_chunks(reconstructed.append, stream_text)
                for chunk in reconstructed:
                    streamed_text_chunks.append(chunk)
                    yield "event: delta\n"
                    yield f"data: {json.dumps({'text': chunk})}\n\n"
                normalized_streamed = "".join(streamed_text_chunks).strip()
        provisional_text = normalized_streamed
        if need_context_for_final and context:
            fallback_text = context.response_text or ""
            if not provisional_text:
                provisional_text = fallback_text
        provisional_payload = {
            "text": provisional_text,
            "message_id": None,
            "session_status": session_status,
            "pending": True,
        }
        yield "event: final\n"
        yield f"data: {json.dumps(provisional_payload)}\n\n"

        if not need_context_for_final:
            worker.join()
            context = plan_holder.get("context")

        finalize_queue.get()
        final_payload = plan_holder.get("final_payload")
        if not final_payload:
            error_message = plan_holder.get("final_error", "AI finalization failed")
            yield "event: error\n"
            yield f"data: {json.dumps(error_message)}\n\n"
            return

        final_payload = dict(final_payload)
        persisted_text = final_payload.get("text", "")
        effective_text = normalized_streamed or persisted_text
        message_id_value = final_payload.get("message_id")
        if effective_text and effective_text != persisted_text and message_id_value:
            try:
                message_uuid = uuid.UUID(str(message_id_value))
            except (TypeError, ValueError):
                message_uuid = None
            if message_uuid:
                service.update_message(
                    session_token=session_token,
                    message_id=message_uuid,
                    body=effective_text,
                    conversation=conversation,
                )
                final_payload["text"] = effective_text

        final_payload["pending"] = False
        trace_logger.log(
            "response.dispatched",
            detail=f"message_id={final_payload.get('message_id')}",
            indent=1,
        )
        yield "event: turnPersisted\n"
        yield f"data: {json.dumps(final_payload)}\n\n"

        while True:
            post_event = actions_queue.get()
            if post_event is actions_sentinel:
                break
            if post_event.get("type") == "turnUpdated":
                payload = post_event.get("payload") or {}
                yield "event: turnUpdated\n"
                yield f"data: {json.dumps(payload)}\n\n"
            elif post_event.get("type") == "actionsComplete":
                payload = {
                    "message_id": post_event.get("message_id"),
                    "actions": post_event.get("actions", []),
                    "label": "Follow-up tasks completed.",
                }
                trace_logger.log(
                    "actions.completed",
                    detail=f"message_id={payload['message_id']} count={len(payload['actions'])}",
                    indent=2,
                )
                yield "event: actionsComplete\n"
                yield f"data: {json.dumps(payload)}\n\n"
            elif post_event.get("type") == "actionsError":
                payload = {
                    "message_id": post_event.get("message_id"),
                    "error": post_event.get("error", "Background workflow failed."),
                }
                trace_logger.log_error(
                    "actions",
                    payload.get("error") or "actions failed",
                    indent=2,
                )
                yield "event: actionsError\n"
                yield f"data: {json.dumps(payload)}\n\n"

    def _state_machine_event_stream() -> Iterable[str]:
        streamed_from_provider = False
        pending_emitted = False
        while True:
            try:
                chunk = stream_queue.get(timeout=0.25)
            except Empty:
                if worker.is_alive() or not stream_complete.is_set():
                    continue
                break
            if chunk is stream_sentinel:
                break
            if isinstance(chunk, dict):
                if chunk.get("type") == "context_progress":
                    state_value = chunk.get("state")
                    label_value = chunk.get("label")
                    data: dict[str, object] = {}
                    if isinstance(state_value, str):
                        data["state"] = state_value
                    if isinstance(label_value, str):
                        data["label"] = label_value
                    meta_value = chunk.get("meta")
                    if isinstance(meta_value, dict):
                        data["meta"] = meta_value
                    yield "event: context_progress\n"
                    yield f"data: {json.dumps(data)}\n\n"
                    continue
                if chunk.get("type") == "status":
                    state_value = chunk.get("state")
                    label_value = chunk.get("label")
                    data: dict[str, object] = {}
                    if isinstance(state_value, str):
                        data["state"] = state_value
                    if isinstance(label_value, str):
                        data["label"] = label_value
                    meta_value = chunk.get("meta")
                    if isinstance(meta_value, dict):
                        data["meta"] = meta_value
                    yield "event: status\n"
                    yield f"data: {json.dumps(data)}\n\n"
                    continue
                if chunk.get("type") == "spinnerStatus":
                    payload = {
                        "message_id": chunk.get("message_id"),
                        "text": chunk.get("spinner_text"),
                        "pending": chunk.get("pending"),
                    }
                    yield "event: spinnerStatus\n"
                    yield f"data: {json.dumps(payload)}\n\n"
                    continue
            streamed_from_provider = True
            chunk_text = str(chunk)
            streamed_text_chunks.append(chunk_text)
            pending_payload = _turn_pending_payload("".join(streamed_text_chunks))
            pending_emitted = True
            yield "event: turnPending\n"
            yield f"data: {json.dumps(pending_payload)}\n\n"

        streamed_text = "".join(streamed_text_chunks)
        normalized_streamed = streamed_text.strip()
        context: StreamingTurnContext | None = None
        need_context_for_text = (not streamed_from_provider) or not normalized_streamed
        if need_context_for_text:
            worker.join()
            context = plan_holder.get("context")
            if not context:
                error_message = plan_holder.get("error", "AI orchestration failed")
                yield "event: error\n"
                yield f"data: {json.dumps(error_message)}\n\n"
                return
            stream_text = "".join(context.streamed_chunks).strip() or context.response_text or ""
            if stream_text:
                streamed_text_chunks.append(stream_text)
                normalized_streamed = "".join(streamed_text_chunks).strip()
        if not pending_emitted:
            pending_payload = _turn_pending_payload(normalized_streamed)
            yield "event: turnPending\n"
            yield f"data: {json.dumps(pending_payload)}\n\n"

        worker.join()
        finalize_queue.get()
        final_payload = plan_holder.get("final_payload")
        if not final_payload:
            error_message = plan_holder.get("final_error", "AI finalization failed")
            yield "event: error\n"
            yield f"data: {json.dumps(error_message)}\n\n"
            return

        final_payload = dict(final_payload)
        persisted_text = final_payload.get("text", "")
        effective_text = normalized_streamed or persisted_text
        message_id_value = final_payload.get("message_id")
        if effective_text and effective_text != persisted_text and message_id_value:
            try:
                message_uuid = uuid.UUID(str(message_id_value))
            except (TypeError, ValueError):
                message_uuid = None
            if message_uuid:
                service.update_message(
                    session_token=session_token,
                    message_id=message_uuid,
                    body=effective_text,
                    conversation=conversation,
                )
                final_payload["text"] = effective_text

        final_payload["pending"] = False
        if "metadata_version" not in final_payload:
            final_payload["metadata_version"] = plan_holder.get("metadata_version", 1)
        trace_logger.log(
            "response.dispatched",
            detail=f"message_id={final_payload.get('message_id')}",
            indent=1,
        )
        _emit_spinner_status("", pending=False, fallback=None, allow_empty=True)
        yield "event: turnPersisted\n"
        yield f"data: {json.dumps(final_payload)}\n\n"

        while True:
            post_event = actions_queue.get()
            if post_event is actions_sentinel:
                break
            if post_event.get("type") == "turnUpdated":
                payload = post_event.get("payload") or {}
                yield "event: turnUpdated\n"
                yield f"data: {json.dumps(payload)}\n\n"
            elif post_event.get("type") == "actionsComplete":
                payload = {
                    "message_id": post_event.get("message_id"),
                    "actions": post_event.get("actions", []),
                    "label": "Follow-up tasks completed.",
                }
                trace_logger.log(
                    "actions.completed",
                    detail=f"message_id={payload['message_id']} count={len(payload['actions'])}",
                    indent=2,
                )
                yield "event: actionsComplete\n"
                yield f"data: {json.dumps(payload)}\n\n"
            elif post_event.get("type") == "actionsError":
                payload = {
                    "message_id": post_event.get("message_id"),
                    "error": post_event.get("error", "Background workflow failed."),
                }
                trace_logger.log_error(
                    "actions",
                    payload.get("error") or "actions failed",
                    indent=2,
                )
                yield "event: actionsError\n"
                yield f"data: {json.dumps(payload)}\n\n"

    def event_stream() -> Iterable[str]:
        if state_machine_enabled:
            yield from _state_machine_event_stream()
        else:
            yield from _legacy_event_stream()

    return StreamingHttpResponse(event_stream(), content_type="text/event-stream")


@require_GET
def events(request: HttpRequest) -> StreamingHttpResponse:
    session_token = request.GET.get("session_token") or request.GET.get("sessionToken")
    if not session_token:
        return StreamingHttpResponse(status=400)
    service = _service()
    try:
        session = service.get_session_state(session_token=session_token)
    except PortalNotFoundError:
        return StreamingHttpResponse(status=404)

    def heartbeat_stream() -> Iterable[str]:
        yield "event: statusChanged\n"
        yield f"data: {json.dumps({'status': session.status})}\n\n"
        while True:
            yield "event: heartbeat\n"
            yield "data: {}\n\n"
            time.sleep(15)

    response = StreamingHttpResponse(heartbeat_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
