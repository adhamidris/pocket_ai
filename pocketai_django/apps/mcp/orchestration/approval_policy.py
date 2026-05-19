from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from email.utils import getaddresses
from typing import Callable, Mapping

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import (
    EmailAccountAuditAction,
    EmailAccountProvider,
    EmailAccountStatus,
    EmailSendMode,
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

from .email_drafts import McpEmailDraftStateMixin
from .native_approval import McpNativeApprovalMixin
from .phone_approval import McpPhoneApprovalMixin
from .tool_trace_summary import McpToolTraceSummaryMixin
from ..text.redaction import redact_tool_input_payload


logger = logging.getLogger(__name__)


class McpApprovalPolicyMixin(
    McpNativeApprovalMixin,
    McpEmailDraftStateMixin,
    McpToolTraceSummaryMixin,
    McpPhoneApprovalMixin,
):


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
