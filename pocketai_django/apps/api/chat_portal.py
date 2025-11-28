from __future__ import annotations

import copy
import json
import logging
import threading
import time
import uuid
from datetime import datetime
from queue import Empty, Queue
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import close_old_connections
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.models import BusinessProfile
from apps.conversations.models import ConversationSender
from apps.services.ai_orchestrator import (
    ActionDispatcher,
    AiOrchestratorPlan,
    AiOrchestratorService,
    StreamingTurnContext,
)
from apps.services.mcp.sanitizer import sanitize_text, sanitize_with_diagnostics
from apps.services.llm_provider import load_default_provider
from apps.services.chat_portal import (
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

"""
Status codes that trigger both status and context_progress SSE events.

These codes are special because they represent workflow phases that the portal
should surface to users as contextual progress indicators (e.g., "Searching knowledge...").

Why dual events:
    - context_progress: Portal-specific UX events (for frontend badges)
    - status: Generic status events (for logging, other consumers)
    - Both are emitted for these codes to maintain backward compatibility
      and support multiple event consumers

Codes:
    - searching_knowledge: Knowledge search is in progress
    - reading_document: Document/page read is in progress
    - planning_actions: Planner pass is running (extracting actions/extractions)
    - responding: LLM is generating final answer text

Other status codes (e.g., "complete", "stream_complete") only emit status events.
"""
CONTEXT_STATUS_CODES = {
    "searching_knowledge",
    "reading_document",
    "planning_actions",
    "responding",
}


def _queue_put(queue, item):
    """
    Duck-typed queue.put to support list-like objects in tests.
    
    Why:
        In production, we use Queue objects for thread-safe communication.
        In tests, we may use simple lists for easier inspection. This helper
        abstracts the difference so the same code works in both contexts.
    """
    put = getattr(queue, "put", None)
    if callable(put):
        put(item)
    else:
        queue.append(item)


def _enqueue_status_events(queue, *, code: str, label: str | None = None, meta: dict | None = None) -> None:
    """
    Enqueue status + context_progress events in one shot.

    Context_progress is a portal UX concept; status is a simpler generic state.
    
    Why dual events:
        - `context_progress` is used by the frontend to show contextual badges
          (e.g., "Searching knowledge...", "Reading document...")
        - `status` is a generic state event for logging and other consumers
        - Both are emitted for codes in CONTEXT_STATUS_CODES to maintain
          backward compatibility and support multiple event consumers
    """
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


class PortalTraceLogger:
    """Structured trace logger for portal LLM turns.

    Provides lightweight, human-readable tracing that mirrors the public
    doc in docs/llm_conversation_backend_flow.md so engineers can follow
    the same phases (persist → orchestrate → plan → dispatch) in logs.

    Args:
        conversation: Conversation instance tied to the portal session.
        agent: AgentProfile that owns the conversation.
        session_token: Token provided by the portal client.
        orchestrator_mode: Label for which orchestrator was chosen (mcp/legacy).
    """

    def __init__(
        self,
        *,
        conversation,
        agent,
        session_token: str,
        orchestrator_mode: str,
    ) -> None:
        self.conversation_id = getattr(conversation, "id", None)
        self.business_id = getattr(getattr(conversation, "business_profile", None), "id", None)
        self.business_slug = getattr(getattr(conversation, "business_profile", None), "slug", None)
        self.agent_slug = getattr(agent, "slug", None)
        self.session_token = session_token
        self.orchestrator_mode = orchestrator_mode
        tz_name = getattr(settings, "PORTAL_TRACE_TIMEZONE", "Africa/Cairo")
        try:
            self._timezone = ZoneInfo(tz_name)
        except Exception:  # pragma: no cover - fallback for missing tz database
            self._timezone = ZoneInfo("UTC")
            tz_name = "UTC"
        self._timezone_label = tz_name

    def _timestamp(self) -> str:
        """Render a timezone-aware timestamp for log headers."""
        return datetime.now(self._timezone).strftime("%Y-%m-%d %H:%M:%S %Z")

    def _stringify(self, value: Any) -> str:
        """Serialize arbitrary values for logging without raising."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)

    def format_data(self, value: Any) -> str:
        """Public-friendly stringify helper for upstream callers."""
        return self._stringify(value)

    def log(self, title: str, detail: str | dict | None = None, *, indent: int = 0, extra: Any | None = None) -> None:
        """
        Emit a structured trace line (multi-line payloads supported).
        
        Why structured logging:
            - Mirrors the flow phases in docs/llm_conversation_backend_flow.md
            - Makes it easy to trace a single turn through logs
            - Indentation shows call hierarchy (request → orchestrator → tools)
            - Consistent format enables log aggregation and debugging
        """
        base_parts = [
            f"[{self._timestamp()}]",
            f"conversation={self.conversation_id}",
            f"business={self.business_id}",
            f"agent={self.agent_slug}",
            f"orchestrator={self.orchestrator_mode}",
        ]
        header = "portal.trace " + " ".join(part for part in base_parts if part)
        indent_prefix = "    " * max(indent, 0)
        lines: list[str] = []
        lines.append(f"{indent_prefix}• {title}")
        if detail:
            detail_text = self._stringify(detail)
            for payload_line in detail_text.splitlines():
                lines.append(f"{indent_prefix}    {payload_line}")
        if extra:
            extra_text = self._stringify(extra)
            for payload_line in extra_text.splitlines():
                lines.append(f"{indent_prefix}    extra: {payload_line}")
        logger.info("%s\n%s", header, "\n".join(lines))

    def log_status(self, code: str, *, label: str | None = None, meta: dict | None = None, indent: int = 2) -> None:
        """
        Helper to log status codes in a consistent shape.
        
        Used to log orchestrator status updates (searching_knowledge, reading_document, etc.)
        in a structured format that matches SSE event format.
        
        Why helper:
            - Ensures consistent status logging format
            - Matches SSE event structure (code, label, meta)
            - Simplifies caller code (one method call vs building payload)
        
        Args:
            code: Status code (e.g., "searching_knowledge", "reading_document")
            label: Optional human-readable label (e.g., "Searching: fees")
            meta: Optional metadata dict (e.g., {"query": "...", "limit": 5})
            indent: Log indentation level (default 2 for status updates)
        """
        payload: dict[str, Any] = {"code": code}
        if label:
            payload["label"] = label
        if meta:
            payload["meta"] = meta
        self.log("status", payload, indent=indent)

    def log_error(self, title: str, error: Exception | str, *, indent: int = 1) -> None:
        """
        Standardized error logger so caller code stays lean.
        
        Logs errors in a consistent format with error prefix for easy filtering.
        
        Why helper:
            - Consistent error logging format (all errors prefixed with "error.")
            - Handles both Exception objects and string messages
            - Simplifies error logging (one method call)
            - Enables log filtering (grep for "error.*")
        
        Args:
            title: Error category/title (e.g., "orchestrator.turn", "finalize")
            error: Exception object or error message string
            indent: Log indentation level (default 1 for errors)
        """
        self.log(f"error.{title}", str(error), indent=indent)


def _service() -> ChatPortalService:
    """
    Factory wrapper to simplify injection/mocking in tests.
    
    Why factory pattern:
        - Allows test code to mock ChatPortalService without patching imports
        - Centralizes service instantiation (single place to change defaults)
        - Simplifies dependency injection in view functions
    """
    return ChatPortalService()


def _json_error(code: str, message: str, *, status: int = 400, extra: dict | None = None) -> JsonResponse:
    """
    Return a standardized JSON error payload for non-streaming endpoints.
    
    Used by all non-streaming endpoints (bootstrap_session, messages_endpoint, etc.)
    to ensure consistent error response format across the API.
    
    Why standardized format:
        - Consistent error structure (frontend can parse reliably)
        - Error codes enable programmatic handling (e.g., "not_found" → show 404 page)
        - Extra fields allow context-specific error details (e.g., validation field names)
        - Status code maps to HTTP semantics (400=client error, 404=not found, etc.)
    
    Error payload structure:
        {
            "error": {
                "code": "error_code_string",  # e.g., "validation_error", "not_found"
                "message": "Human-readable error message",
                ...extra fields...  # Optional context (e.g., "field": "session_token")
            }
        }
    
    Args:
        code: Error code identifier (e.g., "validation_error", "not_found")
        message: Human-readable error message
        status: HTTP status code (default 400)
        extra: Optional dict of additional error fields (merged into error object)
    
    Returns:
        JsonResponse with standardized error payload
    """
    payload: dict[str, object] = {"error": {"code": code, "message": message}}
    if extra:
        payload["error"].update(extra)
    return JsonResponse(payload, status=status)


def _parse_json_body(request: HttpRequest) -> dict:
    """
    Safely parse request body into JSON.

    Used by all POST endpoints to extract request payload. Handles encoding
    and JSON parsing errors gracefully by raising PortalValidationError
    (which maps to 400 status codes).

    Why custom parser:
        - Centralizes error handling (consistent 400 responses)
        - Validates UTF-8 encoding (prevents encoding errors)
        - Returns empty dict for empty bodies (safe default)

    Args:
        request: Django HttpRequest with JSON body

    Returns:
        Parsed JSON dict (empty dict if body is empty)

    Raises:
        PortalValidationError: If payload is not valid JSON/UTF-8.
    """
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PortalValidationError("Invalid JSON payload") from exc


def _business_prefers_mcp(business: BusinessProfile | None) -> bool:
    """
    Evaluate whether a business should use the MCP orchestrator.

    Preference can be set per-business (metadata.mcp_orchestrator_enabled)
    and falls back to the global RAG_USE_MCP_ORCHESTRATOR flag. This
    mirrors the selection step described in docs/llm_conversation_backend_flow.md.
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
    """
    Public API serializer for business summaries.
    
    Exposes minimal business information for portal header rendering.
    Only includes public-facing fields (id, name, slug) - no sensitive data.
    
    API Contract:
        - id: UUID string (for API references)
        - name: Display name (shown in portal header)
        - slug: URL-friendly identifier (for shareable links)
    
    Why minimal:
        - Portal is public-facing (no internal business data)
        - Reduces payload size (faster bootstrap)
        - Security: Prevents data leakage
    """
    return {"id": str(summary.id), "name": summary.name, "slug": summary.slug}


def _agent_to_dict(summary: PortalAgentSummary) -> dict:
    """
    Public API serializer for agent summaries.
    
    Exposes agent configuration for portal rendering and shareable links.
    
    API Contract:
        - id: UUID string (for API references)
        - name: Display name (shown in portal header, e.g., "Pocket AI")
        - role: Agent role/description (for context)
        - slug: URL-friendly identifier (for shareable links)
        - shareable_path: Full shareable URL path (e.g., "/my-store/pocket-agent")
    
    Why include shareable_path:
        - Allows frontend to generate shareable links
        - Consistent URL format across deployments
        - Supports multi-tenant routing
    """
    return {
        "id": str(summary.id),
        "name": summary.name,
        "role": summary.role,
        "slug": summary.slug,
        "shareable_path": summary.shareable_path,
    }


def _session_to_dict(session: PortalSessionState) -> dict:
    """
    Public API serializer for session snapshots.
    
    Exposes conversation state for session management and UI updates.
    
    API Contract:
        - conversation_id: UUID string (for API references)
        - session_token: Session identifier (for subsequent requests)
        - status: Conversation status (new, live, resolved, etc.)
        - started_at: ISO timestamp (when conversation started)
        - expires_at: ISO timestamp or null (when session expires)
    
    Why include timestamps:
        - Frontend can show conversation age
        - Session expiry handling (refresh token if needed)
        - Analytics: Track conversation duration
    """
    return {
        "conversation_id": str(session.conversation_id),
        "session_token": session.session_token,
        "status": session.status,
        "started_at": session.started_at.isoformat(),
        "expires_at": session.expires_at.isoformat() if session.expires_at else None,
    }


def _message_to_dict(message: PortalMessage) -> dict:
    """
    Public API serializer for messages (customer or AI).
    
    Exposes message content for transcript rendering and persistence.
    
    API Contract:
        - id: UUID string (for API references, feedback, CSAT)
        - sender: Message sender ("customer" | "ai" | "system")
        - body: Message text (may contain markdown)
        - sent_at: ISO timestamp (when message was sent)
        - metadata: Optional dict (citations, actions, diagnostics, etc.)
    
    Why include metadata:
        - Citations: Knowledge sources referenced in answer
        - Actions: Planned/executed actions (case creation, etc.)
        - Diagnostics: Tool traces, identifier gates, ingestion warnings
        - Frontend can render citations, show action status, etc.
    
    Security:
        - Metadata is filtered by service layer (no sensitive data)
        - Only includes public-facing information
    """
    return {
        "id": str(message.id),
        "sender": message.sender,
        "body": message.body,
        "sent_at": message.sent_at.isoformat(),
        "metadata": message.metadata,
    }


def _bootstrap_to_dict(result: PortalSessionBootstrap) -> dict:
    """
    Serialize bootstrap response (business/agent/session + recent messages).
    
    Complete bootstrap payload returned to frontend on session initialization.
    Matches step 1.2 in docs/llm_conversation_backend_flow.md.
    
    API Contract:
        - business: Business summary (id, name, slug)
        - agent: Agent summary (id, name, role, slug, shareable_path)
        - session: Session state (conversation_id, session_token, status, timestamps)
        - messages: Array of message summaries (recent conversation history)
    
    Why include messages:
        - Frontend can restore conversation after page refresh
        - User sees conversation history immediately
        - No separate API call needed for transcript
    
    Message filtering:
        - Placeholder messages are filtered by service layer
        - Only final persisted messages are included
        - Recent messages only (not full history, for performance)
    """
    return {
        "business": _business_to_dict(result.business),
        "agent": _agent_to_dict(result.agent),
        "session": _session_to_dict(result.session),
        "messages": [_message_to_dict(msg) for msg in result.messages],
    }


@require_GET
def resolve_portal_handle(request: HttpRequest, business_slug: str, agent_slug: str) -> JsonResponse:
    """
    Resolve friendly business/agent slugs for the public portal header.
    
    Matches step 1.1 in docs/llm_conversation_backend_flow.md.
    
    Used by the portal widget to validate the chat URL before bootstrapping.
    Returns basic business/agent information needed to render the portal header.
    
    Why separate endpoint:
        - Widget needs to validate URL before attempting bootstrap
        - Allows frontend to show error immediately if slugs are invalid
        - Reduces bootstrap payload (header info fetched separately)
        - Supports shareable link validation
    
    URL pattern:
        GET /api/chat/portal/resolve/<business_slug>/<agent_slug>/
    
    Response:
        {
            "business": {"id": "...", "name": "...", "slug": "..."},
            "agent": {"id": "...", "name": "...", "role": "...", "slug": "...", "shareable_path": "..."}
        }
    
    Raises:
        404 JSON error if business or agent not found, or agent slug mismatch.
    """
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
    """
    Create or resume a portal session.
    
    Matches step 1.2 in docs/llm_conversation_backend_flow.md.
    
    This is the entry point for the portal widget. It either:
    - Creates a new conversation (if no session_token provided)
    - Resumes an existing conversation (if valid session_token provided)
    
    Why bootstrap:
        - Establishes conversation context (business, agent, session)
        - Returns message history (for page refresh/restore)
        - Provides session_token for subsequent requests
        - Adds welcome message if new conversation
    
    Request payload:
        {
            "business_slug": "my-store",
            "agent_slug": "pocket-agent",
            "session_token": "optional-existing-token",
            "metadata": {}  # Optional per-session metadata
        }
    
    Response:
        {
            "business": {...},
            "agent": {...},
            "session": {
                "conversation_id": "...",
                "session_token": "...",
                "status": "...",
                "started_at": "...",
                "expires_at": "..."
            },
            "messages": [...]  # Recent conversation history
        }
    
    Session lifecycle:
        - New session: Creates Conversation, generates session_token, adds welcome message
        - Existing session: Resumes active conversation, returns recent messages
        - Expired session: Creates new conversation (old one archived)
    
    Raises:
        400: Invalid JSON or missing required fields
        404: Business or agent not found
    """
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
    """
    Fetch or append messages (non-streaming endpoint).
    
    Dual-purpose endpoint for message management:
    - GET: Fetch message transcript (for portal reload/restore)
    - POST: Append customer message (legacy path, streaming uses stream_send)
    
    When to use vs stream_send:
    - GET: Portal reload, transcript restoration, pagination
    - POST: Legacy support (pre-streaming), simple message append
    - stream_send: Primary path for new messages (includes streaming, planning, actions)
    
    GET Behavior:
    - Returns recent messages (paginated, max 200)
    - Includes session state snapshot
    - Used by frontend to restore conversation after refresh
    
    POST Behavior:
    - Appends customer message (no streaming, no planning)
    - Returns created message
    - Legacy path: New messages should use stream_send instead
    
    Why keep POST:
    - Backward compatibility (older widget versions)
    - Simple use cases (no LLM response needed)
    - Testing/debugging (easier than SSE)
    
    Matches step 1.2 (GET) and legacy step 2 (POST) in docs/llm_conversation_backend_flow.md.
    """
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
    """
    Record a CSAT (Customer Satisfaction) score/comment for the session.
    
    Used by the portal widget to collect post-conversation feedback.
    Mirrors the post-turn feedback phase in the flow document.
    
    Why CSAT:
        - Measures customer satisfaction with the conversation
        - Helps identify quality issues (low scores trigger alerts)
        - Provides feedback loop for improving agent responses
        - Optional comment field allows detailed feedback
    
    Request payload:
        {
            "session_token": "...",
            "score": 1-5,  # Required: 1=very dissatisfied, 5=very satisfied
            "comment": "..."  # Optional: Free-text feedback
        }
    
    Response:
        {
            "session": {...}  # Updated session state (includes CSAT record)
        }
    
    CSAT storage:
        - Stored as ConversationFeedback record
        - Linked to conversation (not specific message)
        - Used for analytics and quality monitoring
        - May trigger alerts if score is low
    
    Raises:
        400: Invalid JSON, missing session_token, or invalid score (not 1-5)
        404: Session not found
    """
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
    """
    Capture qualitative feedback on a specific AI message.
    
    Used by the portal widget to collect detailed feedback on individual responses.
    Supports structured feedback (thumbs up/down) and detailed annotations.
    
    Why feedback:
        - Helps identify specific response quality issues
        - Expected entities/aliases aid in retraining
        - Auto-promote flag can trigger case creation for negative feedback
        - Provides data for improving knowledge base and prompts
    
    Request payload:
        {
            "session_token": "...",
            "feedback_type": "positive" | "negative" | "neutral",  # Required
            "message_id": "...",  # Optional: UUID of specific AI message
            "query_text": "...",  # Optional: What user asked
            "expected_behavior": "...",  # Optional: What should have happened
            "expected_entities": [...],  # Optional: Expected extracted entities
            "expected_aliases": [...],  # Optional: Expected identifier aliases
            "notes": "...",  # Optional: Free-text notes
            "auto_promote": true  # Optional: Auto-create case for negative feedback
        }
    
    Response:
        {
            "feedback": {
                "id": "...",
                "feedback_type": "...",
                "created_at": "..."
            }
        }
    
    Feedback processing:
        - Stored as ConversationFeedback record
        - Linked to specific message (if message_id provided)
        - Expected entities/aliases stored for retraining analysis
        - Auto-promote: Negative feedback may trigger case creation
    
    Raises:
        400: Invalid JSON, missing required fields, or invalid message_id UUID
        404: Session or message not found
    """
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


@csrf_exempt
@require_POST
def stream_send(request: HttpRequest) -> StreamingHttpResponse:
    """
    Streaming entry point for portal chat turns.

    Flow (aligned with docs/llm_conversation_backend_flow.md):
    1) Persist customer message and load conversation/session.
    2) Choose orchestrator (MCP vs legacy) based on business flag.
    3) Kick off streaming LLM turn on a worker thread (tool loop + final answer).
    4) In parallel, emit SSE deltas/statuses to the browser.
    5) Run planner/persistence in a background thread; emit turnPersisted + actions events.

    Returns:
        StreamingHttpResponse emitting SSE events (`delta`, `status`, `context_progress`,
        `final`, `turnPersisted`, `actionsComplete`, `actionsError`).

    Error handling:
        Returns 400/404/500 StreamingHttpResponse immediately when validation
        fails so the browser can retry without opening a stream.
    """
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError:
        return StreamingHttpResponse(status=400)

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    body = (payload.get("body") or "").strip()
    metadata = payload.get("metadata") or {}

    customer_message: PortalMessage | None = None
    try:
        customer_message = service.append_message(
            session_token=session_token,
            sender=ConversationSender.CUSTOMER,
            body=body,
            metadata=metadata,
        )
    except PortalValidationError:
        return StreamingHttpResponse(status=400)
    except PortalNotFoundError:
        return StreamingHttpResponse(status=404)

    # After persist, re-load conversation to ensure we have agent/business context.
    try:
        conversation = service.get_conversation(session_token=session_token)
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
        """
        Best-effort provider label for trace readability (avoids None/blank).
        
        Used in trace logging to show which LLM provider is being used
        (e.g., "OpenAI", "DeepSeek", "gpt-4", etc.).
        
        Why best-effort:
            - Different provider classes have different attribute names
            - Some providers may not expose a name/label
            - Falls back to class name if no label found
            - Always returns a non-empty string (never None/blank)
            
        Args:
            provider_obj: LLM provider instance (BaseLLMProvider or BaseMcpProvider)
            
        Returns:
            Human-readable provider label (e.g., "OpenAI", "DeepSeek", "OpenAIToolsProvider")
        """
        if provider_obj is None:
            return "unknown"
        for attr in ("name", "label", "model_name"):
            value = getattr(provider_obj, attr, None)
            if isinstance(value, str) and value:
                return value
        return provider_obj.__class__.__name__

    if use_mcp:
        from apps.services.llm_provider import load_mcp_provider
        from apps.services.mcp import McpOrchestratorService

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
        """
        Serialize executed actions for SSE events and persisted metadata.
        
        Called after ActionDispatcher.execute() completes to format results
        for SSE events (actionsComplete) and message metadata updates.
        
        Why serialize:
            - ActionExecutionResult objects aren't JSON-serializable
            - Portal needs simple dict format for SSE events
            - Metadata (e.g., case_id) is used to update message metadata
        """
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
        """
        Serialize queued actions before background execution mutates them.
        
        Called before actions are executed to store "queued" state in message
        metadata. After execution, message metadata is updated with actual results.
        
        Why serialize before execution:
            - Actions are queued in message metadata immediately (for UI display)
            - Execution happens later in background thread
            - Portal can show "queued" status while actions run
            - Final results replace queued status when actions complete
        """
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

    def _response_chunks(text: str, chunk_size: int = 64) -> Iterable[str]:
        """
        Chunk non-streamed responses into SSE-sized tokens.

        Used when upstream streaming failed and we need to replay the whole
        provider answer without a single huge `delta` event.
        
        Why chunking:
            - Some providers may not support streaming (returns full response at once)
            - Large single delta events can cause browser buffering issues
            - Chunking simulates streaming for consistent UX
            - Word-by-word chunking (not character-by-character) for natural flow
            
        Args:
            text: Full response text to chunk
            chunk_size: Target chunk size in characters (default 64)
            
        Returns:
            Iterable of text chunks (word boundaries preserved)
        """
        clean = (text or "").strip()
        if not clean:
            return
        words = clean.split()
        if not words:
            return
        current: list[str] = []
        current_len = 0
        for word in words:
            if not current:
                current.append(word)
                current_len = len(word)
                continue
            projected = current_len + 1 + len(word)
            if projected <= chunk_size:
                current.append(word)
                current_len = projected
            else:
                yield " ".join(current)
                current = [word]
                current_len = len(word)
        if current:
            yield " ".join(current)

    # Queue orchestration mirrors the phases in the flow doc:
    # stream_queue → live tokens/statuses, finalize_queue → planner done, actions_queue → post-actions.
    # 
    # Threading architecture:
    #   - Main thread: SSE event loop (event_stream generator)
    #   - Worker thread: Orchestrator streaming (orchestrate function)
    #   - Finalize thread: Planner + persistence (finalize_stream_context function)
    #   - Actions thread: Background actions (run_post_actions function)
    #
    # Queue communication pattern:
    #   1. Worker thread streams deltas/status → stream_queue → SSE loop
    #   2. Worker thread signals completion → stream_sentinel → SSE loop advances
    #   3. Finalize thread completes → finalize_sentinel → SSE loop emits turnPersisted
    #   4. Actions thread completes → actions_sentinel → SSE loop emits actionsComplete/Error
    #
    # Why separate queues:
    #   - stream_queue: Real-time SSE events (deltas, status) that must be delivered immediately
    #   - finalize_queue: Signals when planner/persistence is done so we can emit turnPersisted
    #   - actions_queue: Background action results (case creation, extractions) that complete later
    #   - Sentinels mark queue end so the SSE loop knows when to advance to the next phase
    #
    # Thread safety:
    #   - Queues are thread-safe (Queue.put/get operations)
    #   - plan_holder dict is shared but only written by one thread per phase
    #   - stream_complete Event is thread-safe (set/is_set operations)
    #   - No locks needed: queues handle synchronization
    stream_queue: Queue = Queue()
    stream_sentinel = object()  # Marks end of streaming phase (all deltas/statuses sent)
    finalize_queue: Queue = Queue()
    finalize_sentinel = object()  # Marks end of planner/persistence phase
    actions_queue: Queue = Queue()
    actions_sentinel = object()  # Marks end of background actions phase
    stream_complete = threading.Event()  # Marks when provider streaming is done (separate from planner)
    plan_holder: dict[str, Any] = {}  # Shared state bag between worker threads (plan, context, errors)
    streamed_text_chunks: list[str] = []  # Accumulates all streamed text for fallback scenarios

    def on_response_text_delta(chunk: str) -> None:
        """
        Push every streamed token chunk into the SSE queue.
        
        Called by the orchestrator as the LLM streams response text.
        Each chunk becomes a `delta` SSE event for real-time UI updates.
        """
        if chunk:
            stream_queue.put(chunk)

    def on_status_change(state) -> None:
        """
        Normalize orchestrator status updates and fan them out to:
        - portal.trace logs (for debugging)
        - SSE status/context_progress events (for UX badges)
        
        Why normalization:
            Orchestrators may emit status as a string or dict. This callback
            standardizes the format and routes it to both logging (for ops)
            and SSE (for user-facing progress indicators).
        """
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

    def signal_stream_complete() -> None:
        """
        Mark provider streaming as finished and unblock the SSE loop.

        This ensures we always send a `final` event even if planner/actions
        are still running on other threads.
        
        Why separate from planner:
            The user sees the answer immediately after streaming completes.
            Planner/actions run in parallel but don't block the UX. This
            separation keeps the portal responsive while backend work continues.
        """
        if stream_complete.is_set():
            return
        stream_complete.set()
        trace_logger.log("stream.completed", indent=1)
        stream_queue.put({"type": "status", "state": "complete", "label": ""})
        logger.debug("Stream completion signaled for conversation %s", conversation.id)
        stream_queue.put(stream_sentinel)

    def on_placeholder_response(text: str) -> None:
        """
        Ignore provider placeholders; portal surfaces progress via status events.
        
        Why suppress placeholders:
            MCP orchestrator may emit early placeholder text like "Let me check..."
            The portal uses structured status events (searching_knowledge, reading_document)
            for UX instead, which are more informative and consistent.
        """
        return

    def finalize_stream_context(stream_context: StreamingTurnContext) -> None:
        """
        Planner + persistence phase.

        Runs after streaming to:
        - call run_planner_only (non-streaming) to derive actions/extractions
        - persist the AI message with citations/diagnostics
        - spawn background actions/extraction storage so SSE can continue
        
        Why separate from streaming:
            - Streaming must be fast (user sees answer immediately)
            - Planner needs the full answer + tool context to plan actions/extractions
            - Persistence happens here so we have a stable message_id for feedback/CSAT
            - Actions run in yet another thread so they don't delay the turnPersisted event
        """
        close_old_connections()
        trace_logger.log("finalize.started", indent=1)
        try:
            # Planner now runs asynchronously using the streamed answer/context.
            plan = orchestrator.run_planner_only(
                conversation=conversation,
                user_message=body,
                answer_text=stream_context.response_text,
                tool_context=getattr(stream_context, "tool_context", None),
            )
            if plan is None:
                # Fallback to the streamed response without actions/extractions.
                plan = orchestrator.finalize_turn(stream_context)
            plan_holder["plan"] = plan
            trace_logger.log(
                "planner.completed",
                detail=f"planned_actions={len(plan.planned_actions)} extractions={len(plan.extractions)}",
                indent=1,
            )
            # Persist safest possible text: prefer planner output, fall back to streamed, then placeholder.
            # Why this order:
            #   - Planner may refine the answer (remove filler, align with citations)
            #   - Streamed chunks are what the user actually saw
            #   - Placeholder ensures we never store empty messages (breaks transcripts)
            persist_text = plan.response_text or ""
            if not persist_text:
                streamed_text = "".join(stream_context.streamed_chunks).strip() if stream_context.streamed_chunks else ""
                if streamed_text:
                    persist_text = streamed_text
            if not persist_text:
                persist_text = "(no content)"
            response_text, dropped = sanitize_with_diagnostics(
                persist_text,
                conversation=conversation,
                stage="persisted_message",
            )
            answer_confidence = None
            if plan.diagnostics:
                answer_confidence = plan.diagnostics.get("answer_confidence")
            pending_actions = serialize_planned_actions(plan.planned_actions)
            message_metadata = {
                "citations": [snippet.title for snippet in plan.citations],
                "actions": pending_actions,
                "diagnostics": plan.diagnostics,
            }
            if answer_confidence is not None:
                message_metadata["answer_confidence"] = answer_confidence
            if plan.ingestion_warnings:
                message_metadata["ingestion_warnings"] = [dict(item) for item in plan.ingestion_warnings]
            ai_message = service.append_message(
                session_token=session_token,
                sender=ConversationSender.AI,
                body=response_text,
                metadata=message_metadata,
            )

            session_state = service.get_session_state(session_token=session_token)
            final_payload = {
                "text": response_text,
                "message_id": str(ai_message.id),
                "session_status": session_state.status,
            }
            if answer_confidence is not None:
                final_payload["answer_confidence"] = answer_confidence
            if plan.ingestion_warnings:
                final_payload["ingestion_warnings"] = [dict(item) for item in plan.ingestion_warnings]
            logger.info(
                "portal response finalized conversation=%s message_id=%s status=%s",
                conversation.id,
                ai_message.id,
                session_state.status,
            )
            extra_payload: dict[str, Any] = {
                "citations": [snippet.title for snippet in plan.citations],
                "pending_actions": len(plan.planned_actions),
            }
            if answer_confidence is not None:
                extra_payload["answer_confidence"] = answer_confidence
            trace_logger.log(
                "response.persisted",
                detail=f"message_id={ai_message.id}",
                indent=1,
                extra=extra_payload,
            )
            plan_holder["message_metadata"] = message_metadata
            plan_holder["final_payload"] = final_payload
            plan_holder["ai_message_id"] = ai_message.id

            def run_post_actions() -> None:
                """
                Execute planned actions and store extractions in the background.

                Keeps the customer-facing stream snappy while backend side effects
                (CRM/lead creation) settle and get reflected via actionsComplete SSE.
                
                Why background:
                    - Actions (case creation, customer updates) can be slow (DB writes, external APIs)
                    - User already has their answer; actions are "nice to have" follow-ups
                    - SSE events (actionsComplete/actionsError) notify the portal when done
                    - If actions fail, we still show the answer (graceful degradation)
                """
                close_old_connections()
                trace_logger.log("post_actions.started", indent=2)
                try:
                    action_results = []
                    if plan.planned_actions:
                        action_results = dispatcher.execute(conversation=conversation, planned_actions=plan.planned_actions)
                        logger.info(
                            "portal action results conversation=%s results=%s",
                            conversation.id,
                            [
                                {
                                    "action": result.action.value,
                                    "status": result.status,
                                    "error": result.error,
                                }
                                for result in action_results
                            ],
                        )
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
                        service.update_message(
                            session_token=session_token,
                            message_id=ai_message.id,
                            metadata=updated_metadata,
                        )
                        actions_queue.put(
                            {
                                "type": "actionsComplete",
                                "message_id": str(ai_message.id),
                                "actions": serialized_actions,
                            }
                        )
                    elif plan.extractions:
                        actions_queue.put(
                            {
                                "type": "actionsComplete",
                                "message_id": str(ai_message.id),
                                "actions": [],
                            }
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.exception("portal post-processing failed: %s", exc)
                    trace_logger.log_error("post_actions", exc, indent=2)
                    actions_queue.put(
                        {
                            "type": "actionsError",
                            "message_id": str(ai_message.id),
                            "error": str(exc),
                        }
                    )
                finally:
                    close_old_connections()
                    actions_queue.put(actions_sentinel)

            if plan.planned_actions or plan.extractions:
                threading.Thread(target=run_post_actions, daemon=True).start()
            else:
                actions_queue.put(actions_sentinel)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Orchestrator finalize failed: %s", exc)
            trace_logger.log_error("finalize", exc, indent=1)
            plan_holder["final_error"] = str(exc)
            actions_queue.put(actions_sentinel)
        finally:
            close_old_connections()
            finalize_queue.put(finalize_sentinel)

    def orchestrate() -> None:
        """
        Worker thread that runs the live LLM turn.

        Kicks off orchestrator.stream_turn with callbacks, then starts planner
        thread and signals stream completion so the SSE loop can advance.
        
        Why separate thread:
            - LLM calls can take 5-30 seconds (tool loops, streaming)
            - Main request thread must stay responsive for SSE delivery
            - Threading allows parallel work: streaming + planner + actions
            - Daemon thread ensures cleanup if request is cancelled
        """
        close_old_connections()
        try:
            trace_logger.log("orchestrator.turn.start", indent=1)
            context = orchestrator.stream_turn(
                conversation=conversation,
                user_message=body,
                on_response_text_delta=on_response_text_delta,
                on_status_change=on_status_change,
                on_placeholder_response=on_placeholder_response,
                on_stream_complete=signal_stream_complete,
            )
            plan_holder["context"] = context
            threading.Thread(target=finalize_stream_context, args=(context,), daemon=True).start()
            # Streaming is complete; signal immediately so SSE can finish without waiting for planner/actions.
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
            close_old_connections()

    worker = threading.Thread(target=orchestrate, daemon=True)
    worker.start()

    def event_stream() -> Iterable[str]:
        """
        SSE generator producing the multi-phase UX.
        
        Emits events in phases (aligned with docs/llm_conversation_backend_flow.md):
        1. Streaming phase: delta, status, context_progress events
        2. Provisional final: Best-guess answer before persistence
        3. turnPersisted: Final persisted message with message_id
        4. actionsComplete/actionsError: Background action results
        
        Phase transitions:
        - Phase 1 → 2: When stream_sentinel is received (streaming complete)
        - Phase 2 → 3: When finalize_sentinel is received (planner/persistence done)
        - Phase 3 → 4: When actions_sentinel is received (actions complete)
        
        Why multi-phase:
        - User sees answer immediately (streaming phase)
        - Provisional final allows UI to show answer while backend persists
        - turnPersisted provides stable message_id for feedback/CSAT
        - actionsComplete shows follow-up tasks (case creation, etc.)
        
        Error handling:
        - Network errors: Stream closes, frontend handles reconnection
        - Planner errors: Falls back to streamed text, still emits turnPersisted
        - Action errors: Emits actionsError event, answer still shown
        
        Thread coordination:
        - Reads from queues (thread-safe operations)
        - Blocks on finalize_queue for planner completion
        - Blocks on actions_queue for action completion
        - Timeout-based polling for stream_queue (non-blocking)
        """
        # Phase 1: Streaming phase (deltas, status, context_progress)
        # Poll stream_queue with timeout to allow non-blocking checks
        streamed_from_provider = False
        while True:
            try:
                # Timeout allows checking if worker thread is still alive
                chunk = stream_queue.get(timeout=0.1)
            except Empty:
                # If worker still alive, continue polling (streaming may be slow)
                # If worker dead, continue to check for sentinel (error handling)
                if worker.is_alive():
                    continue
                else:
                    continue
            # Sentinel marks end of streaming phase (all deltas/statuses sent)
            if chunk is stream_sentinel:
                break
            # Handle structured events (status, context_progress)
            if isinstance(chunk, dict):
                if chunk.get("type") == "context_progress":
                    # context_progress: Portal-specific UX events (searching, reading, etc.)
                    # Used by frontend to show contextual badges
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
                    # status: Generic status events (for logging, other consumers)
                    # Emitted alongside context_progress for backward compatibility
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
            # Handle text chunks (delta events)
            # These are the actual streaming response tokens
            streamed_from_provider = True
            chunk_text = str(chunk)
            streamed_text_chunks.append(chunk_text)
            yield "event: delta\n"
            yield f"data: {json.dumps({'text': chunk_text})}\n\n"
        # Phase 2: Build provisional final event
        # Accumulate all streamed chunks into final text
        streamed_text = "".join(streamed_text_chunks)
        normalized_streamed = streamed_text.strip()

        # Get current session status for provisional final payload
        session_status: str | None = None
        try:
            session_state = service.get_session_state(session_token=session_token)
            session_status = session_state.status
        except PortalNotFoundError:
            # Session may have expired, continue with null status
            session_status = None

        # Phase 2.1: Handle fallback scenarios (non-streaming providers, errors)
        context: StreamingTurnContext | None = None
        # If no provider stream was emitted, fall back to stored StreamingTurnContext to build a response.
        # Why this fallback:
        #   - Some providers may not support streaming (returns full response at once)
        #   - Edge cases where streaming callback fails but we have the answer
        #   - Ensures portal always gets a response even if streaming path fails
        need_context_for_final = (not streamed_from_provider) or not normalized_streamed
        if need_context_for_final:
            # Wait for worker thread to complete (ensures context is available)
            worker.join()
            context = plan_holder.get("context")
            if not context:
                # No context available (orchestrator failed completely)
                error_message = plan_holder.get("error", "AI orchestration failed")
                yield "event: error\n"
                yield f"data: {json.dumps(error_message)}\n\n"
                return
            # If provider didn't stream, chunk the full response and emit as deltas
            # This simulates streaming for non-streaming providers (consistent UX)
            if not streamed_from_provider:
                stream_text = "".join(context.streamed_chunks).strip() or context.response_text or ""
                for chunk in _response_chunks(stream_text):
                    streamed_text_chunks.append(chunk)
                    yield "event: delta\n"
                    yield f"data: {json.dumps({'text': chunk})}\n\n"
                normalized_streamed = "".join(streamed_text_chunks).strip()
        # Build provisional text (prefer streamed, fallback to context)
        provisional_text = normalized_streamed
        if need_context_for_final and context:
            fallback_text = context.response_text or ""
            if not provisional_text:
                provisional_text = fallback_text
        # Phase 2.2: Emit provisional final event
        provisional_payload = {
            "text": provisional_text,
            "message_id": None,  # Not persisted yet (will be in turnPersisted)
            "session_status": session_status,
            "pending": True,  # Flag: indicates this is provisional, not final
        }
        # Provisional final mirrors what was streamed; real persistence follows after planner/actions.
        # Why provisional:
        #   - User sees answer immediately (good UX)
        #   - But message_id is null (not persisted yet)
        #   - turnPersisted event will follow with stable message_id for feedback/CSAT
        yield "event: final\n"
        yield f"data: {json.dumps(provisional_payload)}\n\n"

        # Phase 3: Wait for planner/persistence to complete
        # If we didn't need context earlier, wait for worker now (ensures context is ready)
        if not need_context_for_final:
            worker.join()
            context = plan_holder.get("context")

        # Block on finalize_queue until planner/persistence thread completes
        # This ensures we have the persisted message_id before emitting turnPersisted
        finalize_queue.get()
        plan: AiOrchestratorPlan | None = plan_holder.get("plan")
        if not plan:
            # Planner failed (fallback to streamed text, but no actions/extractions)
            error_message = plan_holder.get("final_error") or plan_holder.get("error", "AI orchestration failed")
            yield "event: error\n"
            yield f"data: {json.dumps(error_message)}\n\n"
            return
        logger.info(
            "portal plan ready conversation=%s actions=%s extractions=%s",
            conversation.id,
            [action.action.value for action in plan.planned_actions],
            [extraction.extraction_type.value for extraction in plan.extractions],
        )
        trace_logger.log(
            "plan.ready",
            detail=f"actions={len(plan.planned_actions)} extractions={len(plan.extractions)}",
            indent=1,
        )

        final_payload = plan_holder.get("final_payload")
        if not final_payload:
            error_message = plan_holder.get("final_error", "AI finalization failed")
            yield "event: error\n"
            yield f"data: {json.dumps(error_message)}\n\n"
            return

        # Phase 3.1: Emit turnPersisted event (final persisted message)
        final_payload = dict(final_payload)
        persisted_text = final_payload.get("text", "")
        # Prefer streamed text (what user actually saw) over persisted text (may be sanitized)
        effective_text = normalized_streamed or persisted_text
        message_id_value = final_payload.get("message_id")
        # If streamed text diverged from persisted text, update DB so history matches what user saw.
        # Why sync:
        #   - User saw the streamed text (what was actually delivered)
        #   - Planner may have sanitized/refined it before persistence
        #   - We prefer the streamed version (what user actually saw) for consistency
        #   - This ensures transcripts match the live experience
        if effective_text and effective_text != persisted_text and message_id_value:
            try:
                message_uuid = uuid.UUID(str(message_id_value))
            except (TypeError, ValueError):
                message_uuid = None
            if message_uuid:
                # Update persisted message to match what was streamed (consistency)
                service.update_message(
                    session_token=session_token,
                    message_id=message_uuid,
                    body=effective_text,
                )
                final_payload["text"] = effective_text

        # Mark as final (not pending) - this is the authoritative persisted message
        final_payload["pending"] = False
        trace_logger.log(
            "response.dispatched",
            detail=f"message_id={final_payload.get('message_id')}",
            indent=1,
        )
        # Phase 3.2: Emit turnPersisted event
        # This is the stable point for feedback, CSAT, and transcript storage
        yield "event: turnPersisted\n"
        yield f"data: {json.dumps(final_payload)}\n\n"

        # Phase 4: Background actions (actionsComplete/actionsError events)
        # Block on actions_queue until all actions complete (or error occurs)
        while True:
            post_event = actions_queue.get()
            if post_event is actions_sentinel:
                # Sentinel marks end of actions phase (all actions complete or failed)
                break
            if post_event.get("type") == "actionsComplete":
                # Actions complete: surface executed action metadata back to portal.
                # Why separate event:
                #   - Actions run in background (can take seconds)
                #   - Portal can show "Follow-up tasks completed" notification
                #   - Allows UI to update action status (e.g., "Case created ✓")
                #   - User already has their answer; actions are follow-up tasks
                payload = {
                    "message_id": post_event.get("message_id"),
                    "actions": post_event.get("actions", []),  # Serialized action results
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
                # Actions failed: notify portal but don't block answer (graceful degradation)
                # Why non-blocking:
                #   - Answer is already shown to user
                #   - Actions are secondary (case creation, etc.)
                #   - User can retry or contact support if needed
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

    return StreamingHttpResponse(event_stream(), content_type="text/event-stream")


@require_GET
def events(request: HttpRequest) -> StreamingHttpResponse:
    """
    Lightweight SSE heartbeat endpoint (statusChanged + heartbeat).
    
    Separate long-lived SSE stream for conversation status updates and liveness.
    Matches step 6 in docs/llm_conversation_backend_flow.md.
    
    Why separate from stream_send:
    - stream_send: Short-lived (per message turn), includes LLM response
    - events: Long-lived (entire session), only status/heartbeat
    - Allows multiple tabs to stay in sync (status changes propagate)
    - Keeps connection alive for status updates outside of message turns
    
    Events emitted:
    - statusChanged: Initial event with current conversation status
    - heartbeat: Empty payload every 15 seconds (proves connection alive)
    
    Use cases:
    - Status synchronization (conversation resolved by admin)
    - Multi-tab coordination (status changes visible across tabs)
    - Connection health monitoring (heartbeat proves stream alive)
    - Reconnection detection (frontend can detect disconnects)
    
    Reconnection behavior:
    - Frontend should reconnect on disconnect
    - Initial statusChanged event restores current state
    - Heartbeat resumes after reconnection
    
    Headers:
    - Cache-Control: no-cache (prevents proxy caching)
    - X-Accel-Buffering: no (disables nginx buffering for real-time events)
    """
    session_token = request.GET.get("session_token") or request.GET.get("sessionToken")
    if not session_token:
        return StreamingHttpResponse(status=400)
    service = _service()
    try:
        session = service.get_session_state(session_token=session_token)
    except PortalNotFoundError:
        return StreamingHttpResponse(status=404)

    def heartbeat_stream() -> Iterable[str]:
        """
        Emit initial statusChanged then heartbeat every 15s.
        
        Stream lifecycle:
        1. Emit statusChanged immediately (frontend knows current state)
        2. Emit heartbeat every 15s (proves connection alive)
        3. Continue indefinitely (until client disconnects or session expires)
        
        Why 15s heartbeat:
        - Frequent enough to detect disconnects quickly
        - Infrequent enough to avoid unnecessary load
        - Standard SSE heartbeat interval
        """
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
