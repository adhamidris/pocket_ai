from __future__ import annotations

import uuid
from typing import Mapping

from django.conf import settings

from apps.conversations.models import Conversation


def _coerce_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)

def _resolve_file_context_conversation(conversation: Conversation) -> Conversation:
    """
    Agent runs execute in isolated conversations, but still need access to the
    anchor chat's uploaded files/artifacts.

    If the current conversation is an agent-run execution context and it has an
    `anchor_conversation_id`, route file tools against that anchor conversation.
    """

    cached = getattr(conversation, "_file_context_conversation", None)
    if isinstance(cached, Conversation):
        return cached

    meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    if isinstance(meta, Mapping):
        source = str(meta.get("source") or "").strip().lower()
        if source == "agent_run":
            anchor_id_raw = str(meta.get("anchor_conversation_id") or meta.get("anchorConversationId") or "").strip()
            if anchor_id_raw:
                try:
                    anchor_uuid = uuid.UUID(anchor_id_raw)
                except (TypeError, ValueError):
                    anchor_uuid = None
                if anchor_uuid:
                    anchor = Conversation.objects.filter(
                        id=anchor_uuid,
                        business_profile_id=getattr(conversation, "business_profile_id", None),
                    ).first()
                    if anchor is not None:
                        setattr(conversation, "_file_context_conversation", anchor)
                        return anchor

    setattr(conversation, "_file_context_conversation", conversation)
    return conversation


def _portal_file_download_url(conversation: Conversation, file_id: uuid.UUID) -> str:
    from datetime import timedelta

    from apps.conversations.portal_files import sign_portal_file_token

    ttl_seconds = int(getattr(settings, "PORTAL_FILE_DOWNLOAD_TTL_SECONDS", 3600) or 0)
    if ttl_seconds <= 0:
        ttl_seconds = 3600
    token = sign_portal_file_token(
        file_id=file_id,
        business_id=conversation.business_profile_id,
        ttl=timedelta(seconds=ttl_seconds),
    )
    return f"/api/chat/portal/files/{file_id}/download/?token={token}"
