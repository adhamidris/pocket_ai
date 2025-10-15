"""Provider-agnostic LLM orchestrator interfaces and persistence hooks.

This module does NOT perform provider API calls. It defines:
- StartTurnContext: inputs to start an agent turn
- StreamEvent: typing for streaming callbacks
- LlmOrchestrator: a seam exposing `start_turn` (to be implemented by a provider layer)
  and `on_final` which validates payloads and persists the AGENT message.

Downstream provider adapters may subclass/compose this class.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, TypedDict
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.conversations import (
    ConversationMessageChannel,
    ConversationMessageType,
    ConversationMessageVisibility,
    ConversationTurnSnapshot,
)
from app.schemas.ai_runtime import AiMessagePayload
from app.services.messages import AddMessageInput, MessagesService

# We reuse the KnowledgePlan dataclass from ai_prompt_service to keep types aligned
try:
    from app.services.ai_prompt_service import KnowledgePlan
except Exception:
    # Fallback lightweight alias to avoid import errors if refactored independently
    @dataclass(frozen=True)
    class KnowledgePlan:
        item_ids: tuple[UUID, ...]
        collections: tuple[str, ...]
        top_k: int = 5
        max_chunks_per_item: int = 3
        citation_format: str = "[{display_name} §{chunk}]"


@dataclass(frozen=True)
class StartTurnContext:
    business_id: UUID
    agent_id: UUID
    conversation_id: UUID
    runtime_profile_version: int | None = None
    knowledge_plan: KnowledgePlan | None = None
    # Optional snapshot hints (provider adapters may fill these)
    model_name: str | None = None
    temperature: float | None = None
    prompt_excerpt: str | None = None
    request_started_at: datetime | None = None


class StreamEvent(TypedDict):
    type: Literal["delta", "tool", "final", "error"]
    data: Any


class LlmOrchestrator:
    """Provider-agnostic seam; implementors can override `start_turn` to stream tokens.

    This class provides a safe `on_final` that:
    - validates the structured `AiMessagePayload`,
    - persists the AGENT message using `MessagesService`,
    - (future) records a ConversationTurnSnapshot.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.messages = MessagesService(session)

    # -------- Streaming entrypoint (to be implemented by provider adapters) --------
    def start_turn(self, ctx: StartTurnContext) -> Iterable[StreamEvent]:
        """Start a turn. Default implementation yields nothing.

        Provider adapters should:
        - stream `{"type": "delta", "data": str}` events for text deltas,
        - optionally emit `{"type": "tool", "data": {...}}` for tool call updates,
        - finally call `on_final(text=..., payload=...)`.
        """
        if False:  # pragma: no cover (placeholder generator)
            yield {"type": "delta", "data": ""}

        return []

    # -------- Finalization hook --------
    def on_final(self, *, ctx: StartTurnContext, text: str, payload: AiMessagePayload) -> None:
        """Validate payload and persist as an AGENT message (public text channel)."""
        # Validate payload explicitly (for defensive programming).
        validated = AiMessagePayload.model_validate(payload.model_dump())

        input_data = AddMessageInput(
            business_id=ctx.business_id,
            conversation_id=ctx.conversation_id,
            message_type=ConversationMessageType.AGENT,
            visibility=ConversationMessageVisibility.PUBLIC,
            channel=ConversationMessageChannel.TEXT,
            body=text,
            payload=validated.model_dump(),
            author_agent_id=ctx.agent_id,
            sent_at=datetime.now(timezone.utc),
        )
        result = self.messages.add_message(input_data)

        # Record a ConversationTurnSnapshot with model/config/tokens/latency (best-effort).
        metrics = getattr(validated.meta, "metrics", None)
        prompt_tokens = getattr(metrics, "prompt_tokens", None) if metrics is not None else None
        completion_tokens = getattr(metrics, "completion_tokens", None) if metrics is not None else None
        latency_ms = getattr(metrics, "latency_ms", None) if metrics is not None else None
        if latency_ms is None and ctx.request_started_at is not None:
            try:
                latency_ms = max(int((datetime.now(timezone.utc) - ctx.request_started_at).total_seconds() * 1000), 0)
            except Exception:
                latency_ms = None

        model_name = ctx.model_name or "unknown"
        temperature = ctx.temperature
        prompt_content = ctx.prompt_excerpt
        completion_content = text

        snapshot = ConversationTurnSnapshot(
            conversation_id=ctx.conversation_id,
            message_id=result.message.id,
            model=model_name,
            temperature=temperature,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            prompt_content=prompt_content,
            completion_content=completion_content,
            metadata_json={
                "runtime_profile_version": ctx.runtime_profile_version,
                "knowledge_plan": (
                    {
                        "item_ids": list(ctx.knowledge_plan.item_ids),
                        "collections": list(ctx.knowledge_plan.collections),
                        "top_k": ctx.knowledge_plan.top_k,
                        "max_chunks_per_item": ctx.knowledge_plan.max_chunks_per_item,
                    } if ctx.knowledge_plan else None
                ),
            },
        )
        self.session.add(snapshot)
        self.session.flush()
        # Turn snapshot hook (provider adapters may record model/config/token usage).
        # Left as a stub here; a future patch can add a dedicated service for snapshots.

__all__ = [
    "StartTurnContext",
    "StreamEvent",
    "LlmOrchestrator",
    "KnowledgePlan",
]
