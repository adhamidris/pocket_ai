from __future__ import annotations

import uuid

from django.utils import timezone

from apps.conversations.models import (
    Conversation,
    ConversationFeedback,
    ConversationSender,
    ConversationStatus,
)
from apps.conversations.portal_service.types import (
    PortalNotFoundError,
    PortalSessionState,
    PortalValidationError,
)
from apps.knowledge.models import KnowledgeFeedbackCase


class PortalFeedbackMixin:
    def record_csat(self, *, session_token: str, score: int, comment: str | None = None) -> PortalSessionState:
        if score < 1 or score > 5:
            raise PortalValidationError("Score must be between 1 and 5")
        conversation = self._resolve_conversation(
            session_token=session_token,
            conversation=None,
            include_messages=False,
        )
        conversation.csat_score = score
        conversation.csat_comment = (comment or "").strip()
        conversation.csat_recorded_at = timezone.now()
        if conversation.status == ConversationStatus.LIVE:
            conversation.status = ConversationStatus.RESOLVED
        conversation.save(update_fields=["csat_score", "csat_comment", "csat_recorded_at", "status", "last_activity_at", "closed_at"])
        return self._serialize_session(conversation)

    def record_feedback(
        self,
        *,
        session_token: str,
        feedback_type: str,
        message_id: uuid.UUID | None = None,
        payload: dict | None = None,
    ) -> ConversationFeedback:
        if feedback_type not in ConversationFeedback.FeedbackType.values:
            raise PortalValidationError("Unsupported feedback_type")
        conversation = self._resolve_conversation(
            session_token=session_token,
            conversation=None,
            include_messages=False,
        )
        message = None
        if message_id:
            message = conversation.messages.filter(id=message_id).first()
            if message is None:
                raise PortalNotFoundError("Message not found")
        feedback = ConversationFeedback.objects.create(
            conversation=conversation,
            message=message,
            feedback_type=feedback_type,
            payload=payload or {},
        )
        if feedback_type == ConversationFeedback.FeedbackType.NOT_FOUND_INCORRECT:
            self._promote_feedback_case(conversation=conversation, feedback=feedback)
        return feedback

    def _promote_feedback_case(self, *, conversation: Conversation, feedback: ConversationFeedback) -> None:
        payload = feedback.payload or {}
        query_text = (payload.get("query_text") or "").strip()
        if not query_text:
            latest_customer = (
                conversation.messages.filter(sender=ConversationSender.CUSTOMER)
                .order_by("-sent_at", "-created_at")
                .first()
            )
            query_text = (latest_customer.body if latest_customer else "").strip()
        if not query_text:
            return
        expected_behavior = (
            payload.get("expected_behavior")
            or KnowledgeFeedbackCase.BehaviorChoices.ALIAS
        )
        metadata_snapshot = conversation.metadata if isinstance(conversation.metadata, dict) else {}
        metadata_payload = {"source": "portal_feedback"}
        route = metadata_snapshot.get("knowledge_route")
        diagnostics = metadata_snapshot.get("knowledge_last_diagnostics")
        if isinstance(route, dict):
            metadata_payload["search_route"] = route
        if isinstance(diagnostics, dict):
            metadata_payload["retrieval_diagnostics"] = diagnostics
        KnowledgeFeedbackCase.objects.get_or_create(
            conversation_feedback=feedback,
            defaults={
                "business_profile": conversation.business_profile,
                "query_text": query_text,
                "expected_behavior": expected_behavior,
                "expected_entities": payload.get("expected_entities") or [],
                "expected_aliases": payload.get("expected_aliases") or [],
                "notes": payload.get("notes") or "",
                "is_active": bool(payload.get("auto_promote", True)),
                "metadata": metadata_payload,
            },
        )
