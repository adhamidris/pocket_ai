from __future__ import annotations

import logging
from typing import Mapping

from apps.accounts.models import BusinessProfile, McpConnectionApprovalMode, McpToolOperationType
from apps.conversations.models import Conversation

from core.tenancy import tenant_context

from ... import tools as mcp_tools


logger = logging.getLogger(__name__)


class McpNativeApprovalMixin:

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
