from __future__ import annotations

import uuid
from typing import Mapping

from django.utils import timezone

from apps.conversations.models import Conversation

from core.tenancy import tenant_context


class McpEmailDraftStateMixin:

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
