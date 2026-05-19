from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Callable, Mapping

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from apps.conversations.models import (
    Conversation,
    ConversationToolApproval,
    ConversationToolApprovalStatus,
    PortalTurn,
)

from core.cache_resilience import CacheUnavailableError, reserve_counter
from core.tenancy import tenant_context

from ..text.redaction import redact_tool_input_payload
from ..types import (
    CharacterBudgetExceeded,
    ChunkPageBudgetExceeded,
    ChunkReadBudgetExceeded,
    ToolConstraintError,
    ToolExecutionContext,
    ToolRateLimitExceeded,
)


logger = logging.getLogger(__name__)


class McpRuntimeControlsMixin:


    def _context_governor_enabled_for_business(self, business_profile) -> bool:
        enabled = bool(getattr(settings, "MCP_CONTEXT_GOVERNOR_ENABLED", True))
        override = self._business_override(business_profile, "mcp_context_governor_enabled", 1 if enabled else 0)
        try:
            return bool(int(override))
        except (TypeError, ValueError):
            return enabled

    def _preplan_enabled_for_business(self, business_profile) -> bool:
        enabled = bool(getattr(settings, "MCP_PREPLAN_ENABLED", False))
        override = self._business_override(business_profile, "mcp_preplan_enabled", 1 if enabled else 0)
        try:
            return bool(int(override))
        except (TypeError, ValueError):
            return enabled

    def _verification_enabled_for_business(self, business_profile) -> bool:
        enabled = bool(getattr(settings, "MCP_VERIFICATION_ENABLED", False))
        override = self._business_override(business_profile, "mcp_verification_enabled", 1 if enabled else 0)
        try:
            return bool(int(override))
        except (TypeError, ValueError):
            return enabled

    def _verification_blocks_streaming_for_business(self, business_profile) -> bool:
        enabled = bool(getattr(settings, "MCP_VERIFICATION_BLOCK_STREAMING", False))
        override = self._business_override(business_profile, "mcp_verification_block_streaming", 1 if enabled else 0)
        try:
            return bool(int(override))
        except (TypeError, ValueError):
            return enabled

    def _max_input_tokens_for_business(self, business_profile) -> int:
        default = int(getattr(settings, "MCP_MAX_INPUT_TOKENS", 7000))
        override = self._business_override(business_profile, "mcp_max_input_tokens", default)
        try:
            limit = int(override)
        except (TypeError, ValueError):
            limit = default
        return max(1000, limit)


    def _business_override(self, business_profile, key: str, default: int | float) -> int | float:
        metadata = getattr(business_profile, "metadata", None)
        overrides = metadata.get(self.business_override_key) if isinstance(metadata, dict) else None
        if not isinstance(overrides, Mapping):
            return default
        value = overrides.get(key)
        if isinstance(value, (int, float)):
            try:
                return type(default)(value)
            except (TypeError, ValueError):
                return default
        return default

    def _char_budget_per_turn(self, business_profile) -> int | None:
        limit = int(self._business_override(business_profile, "char_budget_per_turn", self.default_char_budget_per_turn))
        return limit if limit > 0 else None

    def _char_budget_per_minute(self, business_profile) -> int | None:
        limit = int(
            self._business_override(business_profile, "char_budget_per_minute", self.default_char_budget_per_minute)
        )
        return limit if limit > 0 else None

    def _build_minute_budget_reserver(self, business_profile, limit: int | None):
        if not limit:
            return None
        window = self.char_budget_window_seconds
        cache_key = f"rag:char_minute:{business_profile.id}"

        def _reserve(count: int) -> None:
            if count <= 0:
                return
            try:
                new_total = reserve_counter(
                    key=cache_key,
                    window_seconds=window,
                    amount=int(count),
                    operation="char_budget_per_minute",
                )
            except CacheUnavailableError as exc:
                raise CharacterBudgetExceeded(
                    "Per-minute character budget is temporarily unavailable. Please retry in a moment."
                ) from exc
            if new_total > limit:
                raise CharacterBudgetExceeded(
                    f"Per-minute character budget exceeded (requested {new_total}, max {limit})."
                )

        return _reserve

    @staticmethod
    def _constraint_error_payload(tool_name: str, exc: ToolConstraintError) -> Mapping[str, object]:
        if isinstance(exc, CharacterBudgetExceeded):
            code = "char_budget_exceeded"
            hint = "Character budget exhausted; continue with existing excerpts or respond."
        elif isinstance(exc, ToolRateLimitExceeded):
            code = "rate_limited"
            hint = "Tool rate limit reached; wait briefly or narrow the request."
        elif isinstance(exc, ChunkPageBudgetExceeded):
            code = "page_budget_exceeded"
            hint = "Page window budget exhausted; summarize what you already have."
        elif isinstance(exc, ChunkReadBudgetExceeded):
            code = "chunk_read_budget_exceeded"
            hint = "Chunk read budget exhausted; proceed without additional reads."
        else:
            code = "constraint_violation"
            hint = "Constraint violated."
        return {
            "tool": tool_name,
            "status": "constraint_error",
            "error": str(exc),
            "error_code": code,
            "hint": hint,
            "llm_hint": hint,
            "snippets": [],
        }

    def _tool_approval_timeout_seconds(self) -> int:
        raw_value = getattr(settings, "MCP_TOOL_APPROVAL_TIMEOUT_SECONDS", 120)
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = 120
        return max(5, value)

    def _tool_approval_poll_interval(self) -> float:
        raw_value = getattr(settings, "MCP_TOOL_APPROVAL_POLL_INTERVAL_SECONDS", 0.5)
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            value = 0.5
        return max(0.2, min(value, 5.0))

    @staticmethod
    def _phone_tool_approval_reuse_enabled() -> bool:
        """
        Phone-call approvals are one-shot by default to avoid accidental replays
        across later turns. Re-enable reuse explicitly for legacy behavior.
        """
        return bool(getattr(settings, "MCP_PHONE_TOOL_APPROVAL_REUSE_ENABLED", False))

    @staticmethod
    def _duplicate_phone_call_payload(tool_name: str) -> Mapping[str, object]:
        hint = (
            "An identical phone call was already requested in this turn. "
            "Do not enqueue the same call twice; continue with a single call."
        )
        return {
            "tool": tool_name,
            "status": "blocked",
            "error_code": "duplicate_phone_call",
            "error": "Duplicate phone call in the same turn was skipped.",
            "hint": hint,
            "llm_hint": hint,
        }

    @staticmethod
    def _approval_blocked_payload(tool_name: str, status: str) -> Mapping[str, object]:
        normalized = str(status or "").strip().lower()
        if normalized == ConversationToolApprovalStatus.DENIED:
            hint = "Inform the user the action was not approved and ask how to proceed."
            return {
                "tool": tool_name,
                "status": "blocked",
                "error_code": "approval_denied",
                "error": "Tool call was denied.",
                "hint": hint,
                "llm_hint": hint,
            }
        if normalized == ConversationToolApprovalStatus.EXPIRED:
            hint = (
                "The approval request expired because there was no response. "
                "Acknowledge the expiry briefly and ask how the user wants to proceed "
                "(retry, change details, or cancel). Avoid repeating the full request unless asked."
            )
            return {
                "tool": tool_name,
                "status": "blocked",
                "error_code": "approval_timeout",
                "error": "Tool approval timed out.",
                "hint": hint,
                "llm_hint": hint,
            }
        hint = "Ask the user to approve the tool call before retrying."
        return {
            "tool": tool_name,
            "status": "blocked",
            "error_code": "approval_unavailable",
            "error": "Tool approval was not granted.",
            "hint": hint,
            "llm_hint": hint,
        }

    def _get_or_create_tool_approval(
        self,
        *,
        conversation: Conversation,
        connection: object,
        tool_name: str,
        remote_tool_name: str,
        tool_call_id: str,
        tool_event_id: str,
        arguments: Mapping[str, object],
        approval_requirement: Mapping[str, object],
    ) -> ConversationToolApproval:
        business_id = getattr(conversation, "business_profile_id", None)
        expires_at = timezone.now() + timedelta(seconds=self._tool_approval_timeout_seconds())
        with tenant_context(business_id):
            existing = None
            if tool_call_id:
                existing = ConversationToolApproval.objects.filter(
                    conversation=conversation,
                    tool_call_id=tool_call_id,
                    status=ConversationToolApprovalStatus.PENDING,
                ).first()
            if existing:
                return existing
            metadata = {
                "approval_mode": approval_requirement.get("approval_mode"),
                "operation_type": approval_requirement.get("operation_type"),
                "reason": approval_requirement.get("reason"),
            }
            return ConversationToolApproval.objects.create(
                conversation=conversation,
                connection=connection if hasattr(connection, "id") else None,
                tool_name=tool_name,
                remote_tool_name=remote_tool_name or "",
                tool_call_id=tool_call_id or "",
                event_id=tool_event_id or "",
                status=ConversationToolApprovalStatus.PENDING,
                expires_at=expires_at,
                input_payload=dict(
                    redact_tool_input_payload(
                        dict(arguments) if isinstance(arguments, Mapping) else {},
                        sensitive_keys=self._mcp_setup_fields_for_connection(connection).keys(),
                    )
                ),
                metadata=metadata,
            )

    def _wait_for_tool_approval(
        self,
        *,
        approval: ConversationToolApproval,
        conversation: Conversation,
    ) -> ConversationToolApproval | None:
        timeout_seconds = self._tool_approval_timeout_seconds()
        poll_interval = self._tool_approval_poll_interval()
        deadline = time.monotonic() + timeout_seconds
        business_id = getattr(conversation, "business_profile_id", None)
        last_lease_refresh = 0.0
        lease_refresh_every = float(getattr(settings, "PORTAL_TURN_WORKER_LEASE_REFRESH_SECONDS", 15.0) or 15.0)
        lease_seconds = int(getattr(settings, "PORTAL_TURN_WORKER_LEASE_SECONDS", 60) or 60)
        lease_refresh_every = max(1.0, lease_refresh_every)
        lease_seconds = max(10, lease_seconds)

        while True:
            close_old_connections()
            with tenant_context(business_id):
                refreshed = ConversationToolApproval.objects.filter(
                    id=approval.id,
                    conversation=conversation,
                ).first()
            if not refreshed:
                return None
            approval = refreshed
            if approval.status != ConversationToolApprovalStatus.PENDING:
                return approval

            # Portal turns may hold a DB lease while waiting for approval. Refresh it
            # occasionally so another worker does not double-run the same turn.
            turn_id = getattr(approval, "turn_id", None)
            if turn_id and (time.monotonic() - last_lease_refresh) >= lease_refresh_every:
                last_lease_refresh = time.monotonic()
                lease_until = timezone.now() + timedelta(seconds=lease_seconds)
                try:
                    with tenant_context(business_id):
                        PortalTurn.objects.filter(id=turn_id).update(
                            lease_expires_at=lease_until,
                            updated_at=timezone.now(),
                        )
                except Exception:  # pragma: no cover - best effort
                    logger.debug("portal turn lease refresh failed turn=%s approval=%s", turn_id, approval.id)

            now = timezone.now()
            if approval.expires_at and now >= approval.expires_at:
                approval.status = ConversationToolApprovalStatus.EXPIRED
                approval.resolved_at = now
                approval.save(update_fields=["status", "resolved_at", "updated_at"])
                return approval
            if time.monotonic() >= deadline:
                approval.status = ConversationToolApprovalStatus.EXPIRED
                approval.resolved_at = now
                approval.save(update_fields=["status", "resolved_at", "updated_at"])
                return approval
            time.sleep(poll_interval)

    def _maybe_request_tool_approval(
        self,
        *,
        conversation: Conversation,
        connection: object,
        tool_name: str,
        remote_tool_name: str,
        tool_call_id: str,
        tool_event_id: str,
        arguments: Mapping[str, object],
        approval_requirement: Mapping[str, object],
        on_tool_event: Callable[[Mapping[str, object]], None] | None,
        wait_for_approval: bool = True,
    ) -> tuple[bool, ConversationToolApproval | None, Mapping[str, object] | None]:
        if not approval_requirement.get("requires_approval"):
            return True, None, None

        sensitive_keys = self._mcp_setup_fields_for_connection(connection).keys()
        redacted_input = redact_tool_input_payload(arguments, sensitive_keys=sensitive_keys)
        if not isinstance(redacted_input, Mapping):
            redacted_input = {}
        redacted_input_dict = dict(redacted_input)

        approval = self._get_or_create_tool_approval(
            conversation=conversation,
            connection=connection,
            tool_name=tool_name,
            remote_tool_name=remote_tool_name,
            tool_call_id=tool_call_id,
            tool_event_id=tool_event_id,
            arguments=arguments,
            approval_requirement=approval_requirement,
        )
        approval_payload = {
            "id": str(approval.id),
            "status": approval.status,
            "mode": approval_requirement.get("approval_mode"),
            "operation_type": approval_requirement.get("operation_type"),
            "reason": approval_requirement.get("reason"),
            "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        }
        pending_tool_call_data = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": dict(arguments) if isinstance(arguments, Mapping) else {},
            "approval_id": str(approval.id),
            "connection_id": str(getattr(connection, "id", "") or "") if connection else None,
            "remote_tool_name": remote_tool_name,
            "event_id": tool_event_id,
        }
        request_event = {
            "event_id": tool_event_id,
            "phase": "approval_requested",
            "status": "pending_approval",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "kind": "mcp_remote",
            "remote": {
                "connection_id": str(getattr(connection, "id", "") or ""),
                "connection_name": str(getattr(connection, "name", "") or ""),
                "endpoint_url": str(getattr(connection, "server_url", "") or ""),
                "remote_tool": remote_tool_name,
            },
            "input": redacted_input_dict,
            "approval": approval_payload,
            "output": {"pending_tool_call": pending_tool_call_data},
        }
        if on_tool_event:
            try:
                on_tool_event(request_event)
            except Exception:  # pragma: no cover - UI callback must not break tools
                logger.exception("mcp portal tool approval request callback failed")

        if not wait_for_approval:
            tool_result = {
                "tool": tool_name,
                "status": "pending_approval",
                "error_code": "pending_approval",
                "error": "Awaiting user approval.",
                "hint": "Ask the user to approve or deny this action, then retry the tool call.",
                "approval": approval_payload,
                "remote": dict(request_event.get("remote") or {}) if isinstance(request_event.get("remote"), Mapping) else {},
                "input": redacted_input_dict,
                "pending_tool_call": pending_tool_call_data,
            }
            return False, approval, tool_result

        approval = self._wait_for_tool_approval(approval=approval, conversation=conversation)
        status_value = approval.status if approval else ConversationToolApprovalStatus.DENIED
        approval_payload["status"] = status_value
        resolve_event = {
            "event_id": tool_event_id,
            "phase": "approval_resolved",
            "status": status_value,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "kind": "mcp_remote",
            "remote": {
                "connection_id": str(getattr(connection, "id", "") or ""),
                "connection_name": str(getattr(connection, "name", "") or ""),
                "endpoint_url": str(getattr(connection, "server_url", "") or ""),
                "remote_tool": remote_tool_name,
            },
            "approval": approval_payload,
        }

        if status_value != ConversationToolApprovalStatus.APPROVED:
            tool_result = self._approval_blocked_payload(tool_name, status_value)
            resolve_event["output"] = dict(tool_result)
            if on_tool_event:
                try:
                    on_tool_event(resolve_event)
                except Exception:  # pragma: no cover - UI callback must not break tools
                    logger.exception("mcp portal tool approval resolve callback failed")
            return False, approval, tool_result

        if on_tool_event:
            try:
                on_tool_event(resolve_event)
            except Exception:  # pragma: no cover - UI callback must not break tools
                logger.exception("mcp portal tool approval resolve callback failed")
        return True, approval, None
