from __future__ import annotations

from typing import Mapping

from core.tenancy import tenant_context


EMAIL_PENDING_DRAFT_META_KEY = "email_pending_draft"


def _pending_email_account_id_for_draft(conversation: object, *, draft_id: str) -> str:
    if not draft_id:
        return ""
    meta = getattr(conversation, "metadata", None)
    if not isinstance(meta, Mapping):
        return ""
    pending = meta.get(EMAIL_PENDING_DRAFT_META_KEY)
    if not isinstance(pending, Mapping):
        return ""
    pending_draft_id = str(pending.get("draft_id") or "").strip()
    if not pending_draft_id or pending_draft_id != draft_id:
        return ""
    return str(pending.get("email_account_id") or "").strip()


def _clear_pending_email_draft_meta(
    conversation: object,
    *,
    draft_id: str,
    email_account_id: str | None = None,
) -> bool:
    if not draft_id:
        return False
    business_id = getattr(conversation, "business_profile_id", None)
    with tenant_context(business_id):
        existing_meta = getattr(conversation, "metadata", None)
        meta = dict(existing_meta) if isinstance(existing_meta, Mapping) else {}
        pending = meta.get(EMAIL_PENDING_DRAFT_META_KEY)
        if not isinstance(pending, Mapping):
            return False
        pending_draft_id = str(pending.get("draft_id") or "").strip()
        if pending_draft_id and pending_draft_id != draft_id:
            return False
        pending_email_account_id = str(pending.get("email_account_id") or "").strip()
        if email_account_id and pending_email_account_id and pending_email_account_id != email_account_id:
            return False
        meta.pop(EMAIL_PENDING_DRAFT_META_KEY, None)
        setattr(conversation, "metadata", meta)
        save = getattr(conversation, "save", None)
        if callable(save):
            conversation.save(update_fields=["metadata", "last_activity_at"])
        return True
