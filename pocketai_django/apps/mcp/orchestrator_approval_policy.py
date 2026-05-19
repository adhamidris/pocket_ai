from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from email.utils import getaddresses
from typing import Callable, Mapping

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import (
    BusinessProfile,
    EmailAccountAuditAction,
    EmailAccountProvider,
    EmailAccountStatus,
    EmailSendMode,
    McpConnectionApprovalMode,
    McpToolOperationType,
)
from apps.conversations.models import (
    Conversation,
    ConversationToolApproval,
    ConversationToolApprovalStatus,
)
from apps.integrations.email_accounts import ensure_fresh_email_credentials
from apps.integrations.email_policy import evaluate_email_send_policy
from apps.integrations.gmail import GmailApiError, gmail_get_draft_headers
from apps.integrations.microsoft_graph import GraphApiError, graph_get_draft_headers
from apps.integrations.models import (
    AgentEmailAccountPolicyOverride,
    EmailAccount,
    EmailAccountAuditEvent,
)

from core.tenancy import tenant_context

from . import tools as mcp_tools
from .redaction import redact_tool_input_payload


logger = logging.getLogger(__name__)


class McpApprovalPolicyMixin:


    @staticmethod
    def _is_email_tool(tool_name: str) -> bool:
        return str(tool_name or "").strip().lower().startswith("email_")

    @staticmethod
    def _conversation_actor_user_uuid(conversation: Conversation) -> uuid.UUID | None:
        meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
        actor_user_id = None
        if isinstance(meta, Mapping):
            actor_user_id = meta.get("actor_user_id") or meta.get("actorUserId") or meta.get("user_id") or meta.get("userId")
        if not actor_user_id:
            return None
        try:
            return uuid.UUID(str(actor_user_id))
        except (TypeError, ValueError):
            return None

    def _connected_native_integration_types(self, *, conversation: Conversation) -> set[str]:
        try:
            return mcp_tools.list_connected_native_integration_types(conversation=conversation)
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "native_integration_connected_types_failed conversation=%s business=%s",
                getattr(conversation, "id", None),
                getattr(conversation, "business_profile_id", None),
            )
            return set()

    def _available_native_integration_tool_names(
        self,
        *,
        conversation: Conversation,
        registry: Mapping[str, Mapping[str, object]],
    ) -> set[str]:
        try:
            return mcp_tools.list_enabled_native_integration_tool_names(
                conversation=conversation,
                registry=registry,
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "native_integration_enabled_tools_failed conversation=%s business=%s",
                getattr(conversation, "id", None),
                getattr(conversation, "business_profile_id", None),
            )
            return set()

    def _available_email_integration_tool_names(
        self,
        *,
        conversation: Conversation,
        registry: Mapping[str, Mapping[str, object]],
    ) -> set[str]:
        try:
            return mcp_tools.list_enabled_email_tool_names(
                conversation=conversation,
                registry=registry,
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "email_integration_enabled_tools_failed conversation=%s business=%s",
                getattr(conversation, "id", None),
                getattr(conversation, "business_profile_id", None),
            )
            return set()

    def _effective_tool_approval_mode(self, *, conversation: Conversation) -> str:
        agent = getattr(conversation, "agent_profile", None)
        mode = str(getattr(agent, "mcp_default_approval_mode", "") or "").strip()
        if mode in {
            McpConnectionApprovalMode.AUTO,
            McpConnectionApprovalMode.APPROVE_WRITES,
            McpConnectionApprovalMode.APPROVE_ALL,
        }:
            return mode
        return McpConnectionApprovalMode.AUTO

    def _tool_approval_overrides_for_business(self, *, conversation: Conversation) -> dict[str, str]:
        business_id = str(getattr(conversation, "business_profile_id", "") or "").strip()
        if not business_id:
            return {}

        cached = self._tool_approval_overrides_cache.get(business_id)
        if cached is not None:
            return cached

        business = getattr(conversation, "business_profile", None)
        if business is None:
            with tenant_context(business_id):
                business = BusinessProfile.objects.filter(id=business_id).only("id", "metadata").first()

        metadata = business.metadata if isinstance(getattr(business, "metadata", None), Mapping) else {}
        raw = metadata.get("tool_approval_overrides")
        if raw is None:
            raw = metadata.get("toolApprovalOverrides")
        if not isinstance(raw, Mapping):
            self._tool_approval_overrides_cache[business_id] = {}
            return {}

        cleaned: dict[str, str] = {}
        for tool_name, value in raw.items():
            normalized_tool_name = str(tool_name or "").strip()
            normalized_value = str(value or "").strip().lower()
            if not normalized_tool_name or normalized_value not in {"auto", "confirm"}:
                continue
            cleaned[normalized_tool_name] = normalized_value

        self._tool_approval_overrides_cache[business_id] = cleaned
        return cleaned

    def _tool_approval_override_for_tool(self, *, conversation: Conversation, tool_name: str) -> str | None:
        normalized_tool_name = str(tool_name or "").strip()
        if not normalized_tool_name:
            return None
        overrides = self._tool_approval_overrides_for_business(conversation=conversation)
        mode = str(overrides.get(normalized_tool_name) or "").strip().lower()
        if mode in {"auto", "confirm"}:
            return mode
        return None

    def _resolve_native_integration_policy(
        self,
        *,
        conversation: Conversation,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> dict[str, object]:
        metadata = mcp_tools.get_native_integration_tool_metadata(tool_name)
        if not isinstance(metadata, Mapping):
            return {"decision": "allow", "reason": "non_native_tool"}

        account, account_error = mcp_tools.resolve_native_integration_account_for_tool(
            tool_name=tool_name,
            arguments=arguments,
            conversation=conversation,
        )
        if account_error:
            error_payload = dict(account_error) if isinstance(account_error, Mapping) else {}
            reason_code = str(error_payload.get("error_code") or error_payload.get("error") or "not_connected").strip()
            if reason_code not in {"not_connected", "account_mismatch", "token_expired", "approval_required"}:
                reason_code = "not_connected"
            error_payload["error"] = reason_code
            error_payload["error_code"] = reason_code
            return {
                "decision": "deny",
                "reason": "native_integration_precondition_failed",
                "reason_code": reason_code,
                "error_payload": error_payload,
                "operation_type": str(metadata.get("operation_type") or McpToolOperationType.UNKNOWN),
                "integration_type": str(metadata.get("integration_type") or ""),
            }

        operation_type = str(metadata.get("operation_type") or McpToolOperationType.UNKNOWN)
        override_mode = self._tool_approval_override_for_tool(conversation=conversation, tool_name=tool_name)
        if override_mode == "confirm":
            return {
                "decision": "allow_with_confirmation",
                "reason": "controls_override_confirm",
                "reason_code": "approval_required",
                "operation_type": operation_type,
                "integration_type": str(metadata.get("integration_type") or ""),
                "approval_mode": McpConnectionApprovalMode.APPROVE_ALL,
                "resolved_integration_account_id": str(getattr(account, "id", "") or "") if account else "",
            }
        if override_mode == "auto":
            return {
                "decision": "allow",
                "reason": "controls_override_auto",
                "reason_code": "allowed",
                "operation_type": operation_type,
                "integration_type": str(metadata.get("integration_type") or ""),
                "approval_mode": McpConnectionApprovalMode.AUTO,
                "resolved_integration_account_id": str(getattr(account, "id", "") or "") if account else "",
            }

        approval_mode = self._effective_tool_approval_mode(conversation=conversation)
        if approval_mode == McpConnectionApprovalMode.APPROVE_ALL:
            decision = "allow_with_confirmation"
            reason = "approval_mode_approve_all"
        elif approval_mode == McpConnectionApprovalMode.APPROVE_WRITES and operation_type != McpToolOperationType.READ:
            decision = "allow_with_confirmation"
            reason = "approval_mode_approve_writes"
        else:
            decision = "allow"
            reason = "policy_auto_allowed"

        return {
            "decision": decision,
            "reason": reason,
            "reason_code": "approval_required" if decision == "allow_with_confirmation" else "allowed",
            "operation_type": operation_type,
            "integration_type": str(metadata.get("integration_type") or ""),
            "approval_mode": approval_mode,
            "resolved_integration_account_id": str(getattr(account, "id", "") or "") if account else "",
        }

    _EMAIL_PENDING_DRAFT_META_KEY = "email_pending_draft"

    @staticmethod
    def _try_parse_uuid(value: str) -> uuid.UUID | None:
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError):
            return None

    def _sanitize_email_tool_arguments(self, tool_name: str, arguments: Mapping[str, object]) -> dict[str, object]:
        """
        Email tools support an optional `email_account_id`, but LLMs sometimes
        hallucinate placeholder IDs (e.g. "email-1") which would otherwise
        short-circuit execution with a validation error.
        """

        effective: dict[str, object] = dict(arguments) if isinstance(arguments, Mapping) else {}
        if not self._is_email_tool(tool_name):
            return effective

        raw_account_id = str(effective.get("email_account_id") or effective.get("emailAccountId") or "").strip()
        if raw_account_id and self._try_parse_uuid(raw_account_id) is None:
            effective.pop("email_account_id", None)
            effective.pop("emailAccountId", None)

        return effective

    @staticmethod
    def _looks_like_placeholder_draft_id(value: str) -> bool:
        lowered = (value or "").strip().lower()
        if not lowered:
            return False
        if lowered in {"draft", "draft_id", "draftid"}:
            return True
        if lowered.startswith(("draft-", "draft_")) and lowered[6:].isdigit():
            return True
        return False

    def _pending_email_draft_for_conversation(
        self,
        *,
        conversation: Conversation,
        email_account_id: uuid.UUID | None,
    ) -> dict[str, str] | None:
        meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
        pending = meta.get(self._EMAIL_PENDING_DRAFT_META_KEY)
        if not isinstance(pending, Mapping):
            return None

        draft_id = str(pending.get("draft_id") or "").strip()
        if not draft_id:
            return None

        account_snapshot = self._try_parse_uuid(str(pending.get("email_account_id") or "").strip())
        if email_account_id and account_snapshot and account_snapshot != email_account_id:
            return None

        return {
            "draft_id": draft_id,
            "email_account_id": str(account_snapshot) if account_snapshot else "",
        }

    def _set_pending_email_draft(
        self,
        *,
        conversation: Conversation,
        email_account_id: uuid.UUID,
        provider: str,
        draft_id: str,
        message_id: str,
        thread_id: str,
        preview: Mapping[str, object] | None = None,
    ) -> None:
        business_id = getattr(conversation, "business_profile_id", None)
        with tenant_context(business_id):
            meta = dict(conversation.metadata) if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
            payload: dict[str, object] = {
                "email_account_id": str(email_account_id),
                "provider": str(provider or ""),
                "draft_id": str(draft_id or ""),
                "message_id": str(message_id or ""),
                "thread_id": str(thread_id or ""),
                "created_at": timezone.now().isoformat(),
            }
            if isinstance(preview, Mapping):
                safe_preview: dict[str, object] = {}
                for key in ("to", "cc", "bcc", "subject", "body_text"):
                    if key not in preview:
                        continue
                    value = preview.get(key)
                    if value is None:
                        continue
                    if isinstance(value, list):
                        out: list[str] = []
                        for item in value[:64]:
                            text = str(item or "").strip()
                            if text:
                                out.append(self._clip_text(text, 240))
                        if out:
                            safe_preview[key] = out
                        continue
                    text_value = str(value or "").strip()
                    if not text_value:
                        continue
                    limit = 5000 if key == "body_text" else 240
                    safe_preview[key] = self._clip_text(text_value, limit)
                if safe_preview:
                    payload["preview"] = safe_preview
            meta[self._EMAIL_PENDING_DRAFT_META_KEY] = payload
            conversation.metadata = meta
            conversation.save(update_fields=["metadata", "last_activity_at"])

    def _clear_pending_email_draft(
        self,
        *,
        conversation: Conversation,
        email_account_id: uuid.UUID | None,
        draft_id: str | None,
    ) -> None:
        if not draft_id:
            return
        business_id = getattr(conversation, "business_profile_id", None)
        with tenant_context(business_id):
            meta = dict(conversation.metadata) if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
            pending = meta.get(self._EMAIL_PENDING_DRAFT_META_KEY)
            if not isinstance(pending, Mapping):
                return
            pending_draft_id = str(pending.get("draft_id") or "").strip()
            if pending_draft_id and pending_draft_id != str(draft_id):
                return
            pending_account_id = self._try_parse_uuid(str(pending.get("email_account_id") or "").strip())
            if email_account_id and pending_account_id and pending_account_id != email_account_id:
                return
            meta.pop(self._EMAIL_PENDING_DRAFT_META_KEY, None)
            conversation.metadata = meta
            conversation.save(update_fields=["metadata", "last_activity_at"])

    def _email_tool_event_input(self, tool_name: str, arguments: Mapping[str, object]) -> dict[str, object] | None:
        normalized = str(tool_name or "").strip().lower()
        if normalized == "email_search":
            query = str(arguments.get("query") or "").strip()
            if not query:
                return None
            try:
                limit = int(arguments.get("limit") or 0)
            except (TypeError, ValueError):
                limit = 0
            payload: dict[str, object] = {"query": self._clip_text(query, 240)}
            if limit:
                payload["limit"] = max(1, min(25, limit))
            return payload
        if normalized == "email_get_message":
            message_id = str(arguments.get("message_id") or arguments.get("messageId") or "").strip()
            return {"message_id": message_id} if message_id else None
        if normalized == "email_get_thread":
            thread_id = str(arguments.get("thread_id") or arguments.get("threadId") or "").strip()
            return {"thread_id": thread_id} if thread_id else None
        if normalized == "email_create_draft":
            # Email drafts are user-facing (approval UX). Include the full preview payload so
            # the portal can render + stream the message as it is drafted.
            def _coerce_list(value: object) -> list[str]:
                if not isinstance(value, list):
                    return []
                out: list[str] = []
                for item in value[:64]:
                    text = str(item or "").strip()
                    if text:
                        out.append(text)
                return out

            subject = str(arguments.get("subject") or "").strip()
            body_text = str(arguments.get("body_text") or arguments.get("bodyText") or "").strip()
            if len(body_text) > 12_000:
                body_text = body_text[:12_000].rstrip()

            payload: dict[str, object] = {}
            to_list = _coerce_list(arguments.get("to"))
            cc_list = _coerce_list(arguments.get("cc"))
            bcc_list = _coerce_list(arguments.get("bcc"))
            if to_list:
                payload["to"] = to_list
            if cc_list:
                payload["cc"] = cc_list
            if bcc_list:
                payload["bcc"] = bcc_list
            if subject:
                payload["subject"] = self._clip_text(subject, 240)
            if body_text:
                payload["body_text"] = body_text
            return payload or None
        if normalized == "email_send_draft":
            draft_id = str(arguments.get("draft_id") or arguments.get("draftId") or "").strip()
            return {"draft_id": draft_id} if draft_id else None
        return None

    def _email_tool_trace_arguments(self, tool_name: str, arguments: Mapping[str, object]) -> dict[str, object]:
        # Keep trace payloads privacy-safe; do not store full email bodies/recipients.
        payload = self._email_tool_event_input(tool_name, arguments) or {}
        account_id = str(arguments.get("email_account_id") or arguments.get("emailAccountId") or "").strip()
        if account_id:
            payload["email_account_id"] = account_id
        return payload

    def _email_tool_event_output(self, tool_name: str, tool_result: Mapping[str, object]) -> dict[str, object]:
        normalized = str(tool_name or "").strip().lower()
        status = str(tool_result.get("status") or "").strip() or "ok"
        output: dict[str, object] = {"status": status}
        if status != "ok":
            error_code = str(tool_result.get("error_code") or tool_result.get("error") or "").strip()
            hint = str(tool_result.get("hint") or "").strip()
            if error_code:
                output["error_code"] = error_code
            if hint:
                output["hint"] = self._clip_text(hint, 240)
            return output

        if normalized == "email_search":
            results = tool_result.get("results")
            if isinstance(results, list):
                output["result_count"] = len(results)
                message_ids: list[str] = []
                thread_ids: list[str] = []
                for item in results[:5]:
                    if not isinstance(item, Mapping):
                        continue
                    mid = str(item.get("message_id") or item.get("messageId") or "").strip()
                    tid = str(item.get("thread_id") or item.get("threadId") or "").strip()
                    if mid:
                        message_ids.append(mid)
                    if tid:
                        thread_ids.append(tid)
                if message_ids:
                    output["message_ids"] = message_ids
                if thread_ids:
                    output["thread_ids"] = thread_ids
            return output

        if normalized == "email_get_message":
            output["message_id"] = str(tool_result.get("message_id") or tool_result.get("messageId") or "").strip()
            output["thread_id"] = str(tool_result.get("thread_id") or tool_result.get("threadId") or "").strip()
            output["body_truncated"] = bool(tool_result.get("body_truncated") or tool_result.get("bodyTruncated"))
            return output

        if normalized == "email_get_thread":
            output["thread_id"] = str(tool_result.get("thread_id") or tool_result.get("threadId") or "").strip()
            output["message_count"] = int(tool_result.get("message_count") or tool_result.get("messageCount") or 0)
            messages = tool_result.get("messages")
            if isinstance(messages, list):
                output["returned_messages"] = len(messages)
            output["truncated"] = bool(tool_result.get("truncated"))
            return output

        if normalized == "email_create_draft":
            output["draft_id"] = str(tool_result.get("draft_id") or tool_result.get("draftId") or "").strip()
            output["message_id"] = str(tool_result.get("message_id") or tool_result.get("messageId") or "").strip()
            output["thread_id"] = str(tool_result.get("thread_id") or tool_result.get("threadId") or "").strip()
            output["body_truncated"] = bool(tool_result.get("body_truncated") or tool_result.get("bodyTruncated"))
            return output

        if normalized == "email_send_draft":
            output["draft_id"] = str(tool_result.get("draft_id") or tool_result.get("draftId") or "").strip()
            output["message_id"] = str(tool_result.get("message_id") or tool_result.get("messageId") or "").strip()
            output["thread_id"] = str(tool_result.get("thread_id") or tool_result.get("threadId") or "").strip()
            return output

        # Fallback: do not dump arbitrary tool outputs.
        return output

    def _tool_trace_output_summary(self, tool_name: str, tool_result: Mapping[str, object]) -> dict[str, object] | None:
        """
        Build a compact, privacy-safe summary of the tool output for the portal debug panel.

        This intentionally avoids returning any large free-text payloads (e.g., read_knowledge
        excerpts) while still exposing enough metadata to debug budgets/cursors/artifacts.
        """

        normalized = str(tool_name or "").strip().lower()
        status = str(tool_result.get("status") or "").strip() or "ok"

        if self._is_email_tool(tool_name):
            return self._email_tool_event_output(tool_name, tool_result)

        def _cursor_fingerprint(value: object) -> dict[str, object] | None:
            if not isinstance(value, str):
                return None
            raw = value.strip()
            if not raw:
                return None
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            return {"len": len(raw), "sha256_10": digest[:10]}

        if normalized == "search_knowledge":
            results = tool_result.get("refs")
            if not isinstance(results, list):
                results = tool_result.get("results")
            out: dict[str, object] = {"status": status}
            for key in ("error", "error_code", "hint"):
                value = tool_result.get(key)
                if isinstance(value, str) and value.strip():
                    out[key] = self._clip_text(value.strip(), 240)
            if isinstance(results, list):
                out["results_count"] = len(results)
                preview_list: list[dict[str, object]] = []
                for item in results[:8]:
                    if not isinstance(item, Mapping):
                        continue
                    entry: dict[str, object] = {}
                    item_id = str(item.get("id") or "").strip()
                    if item_id:
                        entry["id"] = item_id
                    label = item.get("label") or item.get("title")
                    if isinstance(label, str) and label.strip():
                        entry["title"] = self._clip_text(label.strip(), 140)
                    kind = item.get("kind")
                    if isinstance(kind, str) and kind.strip():
                        entry["kind"] = kind.strip()
                    item_type = item.get("type")
                    if isinstance(item_type, str) and item_type.strip():
                        entry["type"] = item_type.strip()
                    for key in ("char_estimate", "chars", "row_count", "column_count"):
                        if key in item:
                            try:
                                entry[key] = int(item.get(key) or 0)
                            except (TypeError, ValueError):
                                pass
                    try:
                        suggested = int(item.get("read_chars") or 0)
                    except (TypeError, ValueError):
                        suggested = 0
                    if not suggested:
                        hint = item.get("read_hint")
                        if isinstance(hint, Mapping):
                            try:
                                suggested = int(hint.get("suggested_max_chars") or 0)
                            except (TypeError, ValueError):
                                suggested = 0
                    if suggested:
                        entry["read_chars"] = suggested
                    why = item.get("why")
                    if isinstance(why, list) and why:
                        why_out = [self._clip_text(str(token), 80) for token in why[:2] if str(token).strip()]
                        if why_out:
                            entry["why"] = why_out
                    preview_text = item.get("preview")
                    if isinstance(preview_text, str) and preview_text.strip():
                        entry["preview_chars"] = len(preview_text.strip())
                        if item.get("preview_truncated") is True:
                            entry["preview_truncated"] = True
                    if entry:
                        preview_list.append(entry)
                if preview_list:
                    out["results_preview"] = preview_list
            pagination = tool_result.get("pagination")
            pagination_map = pagination if isinstance(pagination, Mapping) else {}
            total_found = tool_result.get("total_found")
            if total_found is None:
                total_found = pagination_map.get("total")
            if isinstance(total_found, (int, float)) or (isinstance(total_found, str) and total_found.strip().isdigit()):
                try:
                    out["total_found"] = int(total_found)
                except (TypeError, ValueError):
                    pass
            budget = tool_result.get("budget")
            if isinstance(budget, Mapping):
                out["budget"] = dict(budget)
            budget_guidance = tool_result.get("budget_guidance")
            if isinstance(budget_guidance, Mapping) and budget_guidance:
                guidance_out: dict[str, object] = {}
                for key in ("reason", "available_refs_count", "available_read_evidence_count"):
                    if key in budget_guidance:
                        guidance_out[key] = budget_guidance.get(key)
                actions = budget_guidance.get("next_actions")
                if isinstance(actions, list):
                    action_names: list[str] = []
                    for action in actions[:4]:
                        if not isinstance(action, Mapping):
                            continue
                        action_name = str(action.get("action") or "").strip()
                        if action_name:
                            action_names.append(action_name)
                    if action_names:
                        guidance_out["next_actions"] = action_names
                if guidance_out:
                    out["budget_guidance"] = guidance_out
            repeat_guidance = tool_result.get("search_repeat_guidance")
            if isinstance(repeat_guidance, Mapping) and repeat_guidance:
                repeat_out: dict[str, object] = {}
                for key in ("reason", "available_refs_count", "similarity"):
                    if key in repeat_guidance:
                        repeat_out[key] = repeat_guidance.get(key)
                if repeat_out:
                    out["search_repeat_guidance"] = repeat_out
            retrieval_observability = compact_retrieval_observability(
                tool_result.get("retrieval_observability")
            )
            if retrieval_observability:
                out["retrieval_observability"] = retrieval_observability
            completeness = tool_result.get("completeness")
            if isinstance(completeness, Mapping) and completeness:
                # Keep this compact: surfaced fields help debug pagination/dedupe without leaking content.
                completeness_out: dict[str, object] = {}
                for key in (
                    "shown",
                    "legacy_shown",
                    "planned_unique_snippets",
                    "raw_snippet_count",
                    "already_seen",
                    "legacy_already_seen",
                    "total_found",
                    "snippets_total_found",
                    "refs_total_found",
                    "paging_mode",
                    "ref_offset",
                    "has_more",
                    "excluded_seen",
                    "all_previously_shown",
                ):
                    if key in completeness and completeness.get(key) not in (None, ""):
                        completeness_out[key] = completeness.get(key)
                if completeness_out:
                    out["completeness"] = completeness_out
            has_more = tool_result.get("has_more")
            if has_more is None:
                has_more = pagination_map.get("has_more")
            if isinstance(has_more, bool):
                out["has_more"] = has_more
            next_cursor_value = tool_result.get("next_cursor") or pagination_map.get("next_cursor")
            next_cursor_fp = _cursor_fingerprint(next_cursor_value)
            if next_cursor_fp:
                out["next_cursor"] = next_cursor_fp

            requested_query = str(tool_result.get("query") or "").strip()
            if requested_query:
                out["requested_query"] = self._clip_text(requested_query, 220)
            effective_query = requested_query
            if effective_query:
                out["effective_query"] = self._clip_text(effective_query, 260)
            return out

        if normalized == "read_knowledge":
            out: dict[str, object] = {"status": status}
            for key in ("total_chars", "max_chars", "max_chars_allowed"):
                if key in tool_result:
                    try:
                        out[key] = int(tool_result.get(key) or 0)
                    except (TypeError, ValueError):
                        pass
            mode = tool_result.get("mode")
            if isinstance(mode, str) and mode.strip():
                out["mode"] = mode.strip()
            for key in ("error_code", "error"):
                value = tool_result.get(key)
                if isinstance(value, str) and value.strip():
                    out[key] = self._clip_text(value.strip(), 120)

            evidence = tool_result.get("evidence")
            if not isinstance(evidence, list):
                evidence = tool_result.get("contents")
            if isinstance(evidence, list):
                out["evidence_count"] = len(evidence)
                preview: list[dict[str, object]] = []
                for item in evidence[:6]:
                    if not isinstance(item, Mapping):
                        continue
                    entry: dict[str, object] = {}
                    item_id = str(item.get("id") or "").strip()
                    if item_id:
                        entry["id"] = item_id
                    title = item.get("title")
                    if isinstance(title, str) and title.strip():
                        entry["title"] = self._clip_text(title.strip(), 140)
                    item_type = item.get("type")
                    if isinstance(item_type, str) and item_type.strip():
                        entry["type"] = item_type.strip()
                    kind = item.get("kind")
                    if isinstance(kind, str) and kind.strip():
                        entry["kind"] = kind.strip()
                    try:
                        entry["chars"] = int(item.get("chars") or 0)
                    except (TypeError, ValueError):
                        pass
                    entry["complete"] = bool(item.get("complete"))
                    entry["truncated"] = bool(item.get("truncated"))
                    artifact_id = item.get("artifact_id")
                    if isinstance(artifact_id, str) and artifact_id.strip():
                        entry["artifact_id"] = artifact_id.strip()
                    cursor_used_fp = _cursor_fingerprint(item.get("cursor_used"))
                    if cursor_used_fp:
                        entry["cursor_used"] = cursor_used_fp
                    next_cursor_fp = _cursor_fingerprint(item.get("next_cursor"))
                    if next_cursor_fp:
                        entry["next_cursor"] = next_cursor_fp
                    if entry:
                        preview.append(entry)
                if preview:
                    out["evidence_preview"] = preview

            read = tool_result.get("read")
            if isinstance(read, list):
                out["read_count"] = len(read)
                read_preview: list[dict[str, object]] = []
                for item in read[:8]:
                    if not isinstance(item, Mapping):
                        continue
                    entry: dict[str, object] = {}
                    item_id = str(item.get("id") or "").strip()
                    if item_id:
                        entry["id"] = item_id
                    status_value = item.get("status")
                    if isinstance(status_value, str) and status_value.strip():
                        entry["status"] = status_value.strip()
                    try:
                        entry["chars"] = int(item.get("chars") or 0)
                    except (TypeError, ValueError):
                        pass
                    artifact_id = item.get("artifact_id")
                    if isinstance(artifact_id, str) and artifact_id.strip():
                        entry["artifact_id"] = artifact_id.strip()
                    if entry:
                        read_preview.append(entry)
                if read_preview:
                    out["read_preview"] = read_preview
            deferred = tool_result.get("deferred")
            if isinstance(deferred, list):
                out["deferred_count"] = len(deferred)
            errors = tool_result.get("errors")
            if isinstance(errors, list):
                out["errors_count"] = len(errors)
            throttle_notice = tool_result.get("throttle_notice")
            if isinstance(throttle_notice, Mapping):
                out["throttle_notice"] = dict(throttle_notice)
            budget = tool_result.get("budget")
            if isinstance(budget, Mapping):
                out["budget"] = dict(budget)
            return out

        # Default: status + a small set of safe fields (avoid dumping arbitrary payloads).
        out: dict[str, object] = {"status": status}
        for key in (
            "id",
            "document_id",
            "draft_id",
            "message_id",
            "thread_id",
            "artifact_id",
            "count",
            "total",
        ):
            value = tool_result.get(key)
            if value is None or value == "":
                continue
            if isinstance(value, (int, float, bool)):
                out[key] = value
            else:
                out[key] = self._clip_text(value, 160)
        budget = tool_result.get("budget")
        if isinstance(budget, Mapping):
            out["budget"] = dict(budget)
        return out or None

    def _resolve_email_account_for_tool_call(
        self,
        *,
        conversation: Conversation,
        arguments: Mapping[str, object],
    ) -> EmailAccount | None:
        business_id = getattr(conversation, "business_profile_id", None)
        raw_account_id = str(arguments.get("email_account_id") or arguments.get("emailAccountId") or "").strip()
        with tenant_context(business_id):
            if raw_account_id:
                try:
                    account_uuid = uuid.UUID(raw_account_id)
                except (TypeError, ValueError):
                    return None
                return (
                    EmailAccount.objects.filter(
                        id=account_uuid,
                        business_profile_id=business_id,
                        status=EmailAccountStatus.CONNECTED,
                    )
                    .select_related("business_profile", "user")
                    .first()
                )

            meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
            actor_user_id = None
            if isinstance(meta, Mapping):
                actor_user_id = meta.get("actor_user_id") or meta.get("actorUserId") or meta.get("user_id") or meta.get("userId")
            
            if actor_user_id:
                try:
                    user_uuid = uuid.UUID(str(actor_user_id))
                except (TypeError, ValueError):
                    user_uuid = None
                
                if user_uuid:
                    return (
                        EmailAccount.objects.filter(
                            business_profile_id=business_id,
                            user_id=user_uuid,
                            status=EmailAccountStatus.CONNECTED,
                        )
                        .select_related("business_profile", "user")
                        .first()
                    )

            # Fallback: if no actor_user_id is present (e.g. anonymous test session),
            # check if the business has exactly one connected email account.
            # This handles the common "Owner testing their own agent" case without
            # risking data leakage in multi-user environments.
            candidates = list(
                EmailAccount.objects.filter(
                    business_profile_id=business_id,
                    status=EmailAccountStatus.CONNECTED,
                )
                .select_related("business_profile", "user")
                [:2]
            )
            if len(candidates) == 1:
                return candidates[0]
            
            return None

    def _effective_email_send_mode(
        self,
        *,
        conversation: Conversation,
        email_account: EmailAccount,
    ) -> tuple[str, dict[str, object]]:
        send_mode = str(getattr(email_account, "send_mode", "") or "").strip() or str(
            getattr(settings, "EMAIL_SEND_DEFAULT_MODE", EmailSendMode.DRAFT_APPROVAL)
        )
        config: dict[str, object] = dict(email_account.policy_config or {}) if isinstance(email_account.policy_config, Mapping) else {}
        config.setdefault(
            "step_up_external_domain",
            bool(getattr(settings, "EMAIL_AUTOSEND_STEP_UP_EXTERNAL_DOMAIN", True)),
        )

        agent_id = getattr(conversation, "agent_profile_id", None)
        if not agent_id:
            return send_mode, config

        business_id = getattr(conversation, "business_profile_id", None)
        with tenant_context(business_id):
            override = AgentEmailAccountPolicyOverride.objects.filter(
                agent_profile_id=agent_id,
                email_account_id=getattr(email_account, "id", None),
            ).first()
        if override:
            override_mode = str(getattr(override, "send_mode", "") or "").strip()
            if override_mode:
                send_mode = override_mode
            if isinstance(getattr(override, "policy_config", None), Mapping):
                config.update(dict(override.policy_config))
        return send_mode, config

    def _email_send_requires_approval(
        self,
        *,
        conversation: Conversation,
        email_account: EmailAccount,
        draft_id: str,
    ) -> tuple[bool, str]:
        send_mode, config = self._effective_email_send_mode(conversation=conversation, email_account=email_account)
        auto_send_enabled = str(send_mode).strip().lower() == EmailSendMode.AUTO_SEND
        if not auto_send_enabled:
            return True, "draft_plus_approval_default"

        try:
            email_account = ensure_fresh_email_credentials(email_account)
            access_token = str((email_account.credentials or {}).get("access_token") or "").strip()
            if not access_token:
                return True, "missing_access_token"

            if email_account.provider == EmailAccountProvider.GOOGLE:
                headers = gmail_get_draft_headers(access_token=access_token, draft_id=draft_id)
            elif email_account.provider == EmailAccountProvider.MICROSOFT:
                headers = graph_get_draft_headers(access_token=access_token, draft_id=draft_id)
            else:
                return True, "provider_not_supported"
        except (GmailApiError, GraphApiError, Exception):
            logger.exception("email.autosend_policy_check_failed account=%s", getattr(email_account, "id", None))
            return True, "policy_check_failed"

        recipients: list[str] = []
        pairs = getaddresses([headers.get("to", ""), headers.get("cc", ""), headers.get("bcc", "")])
        for _name, address in pairs:
            addr = str(address or "").strip()
            if addr:
                recipients.append(addr)

        decision = evaluate_email_send_policy(
            auto_send_enabled=True,
            recipients=recipients,
            sender_email=str(getattr(email_account, "email_address", "") or ""),
            config=config,
        )
        return decision.requires_approval, decision.reason

    def _phone_tool_input_payload(self, arguments: Mapping[str, object]) -> dict[str, object]:
        phone_number = str(arguments.get("phone_number") or arguments.get("phoneNumber") or "").strip()
        objective = str(arguments.get("objective") or "").strip()
        call_type = str(arguments.get("call_type") or arguments.get("callType") or "").strip()
        language = str(arguments.get("language") or "").strip()
        max_duration = arguments.get("max_duration_minutes") or arguments.get("maxDurationMinutes")
        try:
            max_duration_value = int(max_duration) if max_duration is not None else None
        except (TypeError, ValueError):
            max_duration_value = None

        payload: dict[str, object] = {}
        if phone_number:
            payload["phone_number"] = phone_number
        if objective:
            payload["objective"] = self._clip_text(objective, 600)
        if call_type:
            payload["call_type"] = self._clip_text(call_type, 80)
        if language:
            payload["language"] = self._clip_text(language, 40)
        if max_duration_value:
            payload["max_duration_minutes"] = max_duration_value

        context_items = arguments.get("context_items") or arguments.get("contextItems") or []
        context_lines: list[str] = []
        if isinstance(context_items, list):
            for item in context_items[:8]:
                line = ""
                if isinstance(item, Mapping):
                    title = str(item.get("title") or item.get("label") or item.get("name") or "").strip()
                    value = item.get("value") or item.get("content") or item.get("text") or item.get("summary") or item.get("note")
                    value_text = str(value).strip() if value is not None else ""

                    if title and value_text:
                        line = f"{title}: {value_text}"
                    elif value_text:
                        line = value_text
                    elif title:
                        line = title
                    else:
                        parts: list[str] = []
                        for key, val in list(item.items())[:3]:
                            key_text = str(key).strip()
                            val_text = str(val).strip() if val is not None else ""
                            if key_text and val_text:
                                parts.append(f"{key_text}: {val_text}")
                        line = "; ".join(parts).strip()
                        if not line:
                            try:
                                line = json.dumps(item, ensure_ascii=False)
                            except Exception:
                                line = str(item)
                elif isinstance(item, str):
                    line = item.strip()
                elif item is not None:
                    line = str(item).strip()
                if line:
                    context_lines.append(self._clip_text(line, 220))
        if context_lines:
            payload["context_items"] = context_lines
        return payload

    def _phone_tool_approval_preview(
        self,
        *,
        arguments: Mapping[str, object],
        conversation: Conversation,
    ) -> dict[str, object] | None:
        payload = self._phone_tool_input_payload(arguments)
        if not payload:
            return None

        fields: list[dict[str, str]] = []
        phone_number = str(payload.get("phone_number") or "").strip()
        if phone_number:
            fields.append({"label": "To", "value": self._clip_text(phone_number, 80)})
        objective = str(payload.get("objective") or "").strip()
        if objective:
            fields.append({"label": "Objective", "value": self._clip_text(objective, 360)})
        call_type = str(payload.get("call_type") or "").strip()
        if call_type:
            fields.append({"label": "Type", "value": self._clip_text(call_type, 80)})
        language = str(payload.get("language") or "").strip()
        if language:
            fields.append({"label": "Language", "value": self._clip_text(language, 40)})
        max_duration = payload.get("max_duration_minutes")
        if isinstance(max_duration, int) and max_duration:
            fields.append({"label": "Max duration", "value": f"{max_duration} min"})

        context_lines: list[str] = []
        context_items = payload.get("context_items")
        if isinstance(context_items, list):
            for item in context_items[:8]:
                line = str(item or "").strip()
                if line:
                    context_lines.append(self._clip_text(line, 220))

        summary = str(getattr(conversation, "summary", "") or "").strip()
        if summary:
            context_lines.append(f"Summary: {self._clip_text(summary, 600)}")
        elif not context_lines:
            try:
                business_id = getattr(conversation, "business_profile_id", None)
                with tenant_context(business_id):
                    messages = list(
                        ConversationMessage.objects.filter(conversation_id=conversation.id)
                        .order_by("-sent_at", "-created_at")
                        .only("sender", "body")[:4]
                    )
                for msg in reversed(messages):
                    body = str(getattr(msg, "body", "") or "").strip()
                    if not body:
                        continue
                    sender = "Customer" if msg.sender == ConversationSender.CUSTOMER else "Agent"
                    context_lines.append(f"{sender}: {self._clip_text(body, 160)}")
            except Exception:
                context_lines = context_lines or []

        preview: dict[str, object] = {"type": "phone_call", "title": "Phone call", "fields": fields}
        if context_lines:
            preview["body"] = self._clip_text("\n".join(context_lines), 1400)
        if not fields and not context_lines:
            return None
        return preview

    def _maybe_request_phone_tool_approval(
        self,
        *,
        conversation: Conversation,
        tool_name: str,
        tool_call_id: str,
        tool_event_id: str,
        arguments: Mapping[str, object],
        on_tool_event: Callable[[Mapping[str, object]], None] | None,
        wait_for_approval: bool = True,
    ) -> tuple[bool, ConversationToolApproval | None, Mapping[str, object] | None]:
        expires_at = timezone.now() + timedelta(seconds=self._tool_approval_timeout_seconds())
        business_id = getattr(conversation, "business_profile_id", None)

        redacted_input = self._phone_tool_input_payload(arguments)
        redacted_input_dict = dict(redacted_input) if isinstance(redacted_input, Mapping) else {}

        existing_approved: ConversationToolApproval | None = None
        if self._phone_tool_approval_reuse_enabled():
            with tenant_context(business_id):
                approved_candidates = list(
                    ConversationToolApproval.objects.filter(
                        conversation=conversation,
                        tool_name=tool_name,
                        remote_tool_name="",
                        status=ConversationToolApprovalStatus.APPROVED,
                    )
                    .order_by("-resolved_at")[:10]
                )
            for candidate in approved_candidates:
                candidate_input = getattr(candidate, "input_payload", None)
                if isinstance(candidate_input, Mapping) and dict(candidate_input) == redacted_input_dict:
                    existing_approved = candidate
                    break

        if existing_approved:
            approval_payload = {
                "id": str(existing_approved.id),
                "status": ConversationToolApprovalStatus.APPROVED,
                "operation_type": "write",
                "reason": "phone_call",
                "expires_at": existing_approved.expires_at.isoformat() if existing_approved.expires_at else None,
            }
            resolve_event = {
                "event_id": tool_event_id,
                "phase": "approval_resolved",
                "status": ConversationToolApprovalStatus.APPROVED,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "kind": "phone",
                "approval": approval_payload,
                "input": redacted_input_dict,
            }
            if on_tool_event:
                try:
                    on_tool_event(resolve_event)
                except Exception:  # pragma: no cover - UI callback must not break tools
                    logger.exception("mcp portal phone approval resolve callback failed")
            return True, existing_approved, None

        with tenant_context(business_id):
            existing = None
            if tool_call_id:
                existing = ConversationToolApproval.objects.filter(
                    conversation=conversation,
                    tool_call_id=tool_call_id,
                    status=ConversationToolApprovalStatus.PENDING,
                ).first()
            approval = existing or ConversationToolApproval.objects.create(
                conversation=conversation,
                connection=None,
                tool_name=tool_name,
                remote_tool_name="",
                tool_call_id=tool_call_id or "",
                event_id=tool_event_id or "",
                status=ConversationToolApprovalStatus.PENDING,
                expires_at=expires_at,
                input_payload=redacted_input_dict,
                metadata={
                    "approval_mode": "phone_call",
                    "operation_type": "write",
                    "reason": "phone_call",
                },
            )

        preview_payload = self._phone_tool_approval_preview(arguments=arguments, conversation=conversation)
        if preview_payload and isinstance(getattr(approval, "metadata", None), Mapping):
            try:
                updated_meta = dict(approval.metadata or {})
                updated_meta["preview"] = preview_payload
                with tenant_context(business_id):
                    ConversationToolApproval.objects.filter(id=approval.id).update(
                        metadata=updated_meta,
                        updated_at=timezone.now(),
                    )
                approval.metadata = updated_meta
            except Exception:  # pragma: no cover - best effort only
                logger.exception("mcp phone approval preview persist failed approval=%s", getattr(approval, "id", None))

        approval_payload = {
            "id": str(approval.id),
            "status": approval.status,
            "operation_type": "write",
            "reason": "phone_call",
            "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        }
        if preview_payload:
            approval_payload["preview"] = preview_payload

        pending_tool_call_data = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": dict(arguments) if isinstance(arguments, Mapping) else {},
            "approval_id": str(approval.id),
            "connection_id": None,
            "remote_tool_name": "",
            "event_id": tool_event_id,
        }
        request_event = {
            "event_id": tool_event_id,
            "phase": "approval_requested",
            "status": "pending_approval",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "kind": "phone",
            "input": redacted_input_dict,
            "approval": approval_payload,
            "output": {"pending_tool_call": pending_tool_call_data},
        }
        if on_tool_event:
            try:
                on_tool_event(request_event)
            except Exception:  # pragma: no cover - UI callback must not break tools
                logger.exception("mcp portal phone approval request callback failed")

        if not wait_for_approval:
            tool_result = {
                "tool": tool_name,
                "status": "pending_approval",
                "error_code": "pending_approval",
                "error": "Awaiting user approval.",
                "hint": "Ask the user to approve or deny the phone call, then retry.",
                "approval": approval_payload,
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
            "kind": "phone",
            "approval": approval_payload,
        }
        if status_value != ConversationToolApprovalStatus.APPROVED:
            tool_result = self._approval_blocked_payload(tool_name, status_value)
            resolve_event["output"] = dict(tool_result)
            if on_tool_event:
                try:
                    on_tool_event(resolve_event)
                except Exception:  # pragma: no cover - UI callback must not break tools
                    logger.exception("mcp portal phone approval resolve callback failed")
            return False, approval, dict(tool_result)

        if on_tool_event:
            try:
                on_tool_event(resolve_event)
            except Exception:  # pragma: no cover - UI callback must not break tools
                logger.exception("mcp portal phone approval resolve callback failed")
        return True, approval, None

    def _maybe_request_email_tool_approval(
        self,
        *,
        conversation: Conversation,
        tool_name: str,
        tool_call_id: str,
        tool_event_id: str,
        arguments: Mapping[str, object],
        reason: str,
        on_tool_event: Callable[[Mapping[str, object]], None] | None,
        wait_for_approval: bool = True,
    ) -> tuple[bool, ConversationToolApproval | None, Mapping[str, object] | None]:
        expires_at = timezone.now() + timedelta(seconds=self._tool_approval_timeout_seconds())
        business_id = getattr(conversation, "business_profile_id", None)

        redacted_input = redact_tool_input_payload(arguments, sensitive_keys={"body_text", "bodyText"})
        redacted_input_dict = dict(redacted_input) if isinstance(redacted_input, Mapping) else {}

        with tenant_context(business_id):
            existing = None
            if tool_call_id:
                existing = ConversationToolApproval.objects.filter(
                    conversation=conversation,
                    tool_call_id=tool_call_id,
                    status=ConversationToolApprovalStatus.PENDING,
                ).first()
            approval = existing or ConversationToolApproval.objects.create(
                conversation=conversation,
                connection=None,
                tool_name=tool_name,
                remote_tool_name="",
                tool_call_id=tool_call_id or "",
                event_id=tool_event_id or "",
                status=ConversationToolApprovalStatus.PENDING,
                expires_at=expires_at,
                input_payload=dict(
                    redact_tool_input_payload(
                        dict(arguments) if isinstance(arguments, Mapping) else {},
                        sensitive_keys={"body_text", "bodyText"},
                    )
                ),
                metadata={
                    "approval_mode": "email_send",
                    "operation_type": "write",
                    "reason": reason,
                },
            )

        preview_payload: dict[str, object] | None = None
        try:
            draft_id = str(arguments.get("draft_id") or arguments.get("draftId") or "").strip()
            meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
            pending = meta.get(self._EMAIL_PENDING_DRAFT_META_KEY)
            if isinstance(pending, Mapping):
                pending_draft_id = str(pending.get("draft_id") or "").strip()
                preview = pending.get("preview")
                if draft_id and pending_draft_id and pending_draft_id == draft_id and isinstance(preview, Mapping):
                    preview_payload = dict(preview)
        except Exception:  # pragma: no cover - best effort only
            preview_payload = None

        if preview_payload and isinstance(getattr(approval, "metadata", None), Mapping):
            try:
                updated_meta = dict(approval.metadata or {})
                updated_meta["preview"] = preview_payload
                with tenant_context(business_id):
                    ConversationToolApproval.objects.filter(id=approval.id).update(
                        metadata=updated_meta,
                        updated_at=timezone.now(),
                    )
                approval.metadata = updated_meta
            except Exception:  # pragma: no cover - best effort only
                logger.exception("mcp email approval preview persist failed approval=%s", getattr(approval, "id", None))

        approval_payload = {
            "id": str(approval.id),
            "status": approval.status,
            "operation_type": "write",
            "reason": reason,
            "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        }
        if preview_payload:
            approval_payload["preview"] = preview_payload
        pending_tool_call_data = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": dict(arguments) if isinstance(arguments, Mapping) else {},
            "approval_id": str(approval.id),
            "connection_id": None,
            "remote_tool_name": "",
            "event_id": tool_event_id,
        }
        request_event = {
            "event_id": tool_event_id,
            "phase": "approval_requested",
            "status": "pending_approval",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "kind": "email",
            "input": redacted_input_dict,
            "approval": approval_payload,
            "output": {"pending_tool_call": pending_tool_call_data},
        }
        if on_tool_event:
            try:
                on_tool_event(request_event)
            except Exception:  # pragma: no cover - UI callback must not break tools
                logger.exception("mcp portal email approval request callback failed")

        if not wait_for_approval:
            tool_result = {
                "tool": tool_name,
                "status": "pending_approval",
                "error_code": "pending_approval",
                "error": "Awaiting user approval.",
                "hint": "Ask the user to approve or deny sending, then retry.",
                "approval": approval_payload,
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
            "kind": "email",
            "approval": approval_payload,
        }
        # Include the (redacted) input so the portal UI can correlate this approval
        # resolution with the existing draft card (prevents duplicate "Sending email…"
        # cards that never transition to a finished state).
        resolve_event["input"] = redacted_input_dict

        if status_value != ConversationToolApprovalStatus.APPROVED:
            tool_result = self._approval_blocked_payload(tool_name, status_value)
            resolve_event["output"] = dict(tool_result)
            if on_tool_event:
                try:
                    on_tool_event(resolve_event)
                except Exception:  # pragma: no cover - UI callback must not break tools
                    logger.exception("mcp portal email approval resolve callback failed")
            return False, approval, dict(tool_result)

        if on_tool_event:
            try:
                on_tool_event(resolve_event)
            except Exception:  # pragma: no cover - UI callback must not break tools
                logger.exception("mcp portal email approval resolve callback failed")
        return True, approval, None

    def _record_email_send_audit(
        self,
        *,
        conversation: Conversation,
        email_account: EmailAccount,
        tool_result: Mapping[str, object],
    ) -> None:
        status = str(tool_result.get("status") or "").strip().lower()
        if status != "ok":
            return
        business_id = getattr(conversation, "business_profile_id", None)
        actor_user = None
        meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
        actor_user_id = None
        if isinstance(meta, Mapping):
            actor_user_id = meta.get("actor_user_id") or meta.get("actorUserId") or meta.get("user_id") or meta.get("userId")
        if actor_user_id:
            try:
                from django.contrib.auth import get_user_model

                user_model = get_user_model()
                candidate = user_model.objects.filter(id=uuid.UUID(str(actor_user_id))).first()
                if candidate and business_id and hasattr(candidate, "business_profiles"):
                    if not candidate.business_profiles.filter(id=business_id).exists():
                        candidate = None
                actor_user = candidate
            except Exception:
                actor_user = None

        with tenant_context(business_id):
            EmailAccountAuditEvent.objects.create(
                business_profile=conversation.business_profile,
                email_account=email_account,
                email_account_id_snapshot=getattr(email_account, "id", None),
                actor_user=actor_user,
                actor_agent=getattr(conversation, "agent_profile", None),
                action=EmailAccountAuditAction.UPDATED,
                description="Email draft sent via chat.",
                metadata={
                    "provider": str(getattr(email_account, "provider", "") or ""),
                    "draft_id": str(tool_result.get("draft_id") or tool_result.get("draftId") or ""),
                    "message_id": str(tool_result.get("message_id") or tool_result.get("messageId") or ""),
                    "thread_id": str(tool_result.get("thread_id") or tool_result.get("threadId") or ""),
                    "conversation_id": str(getattr(conversation, "id", "") or ""),
                },
            )
