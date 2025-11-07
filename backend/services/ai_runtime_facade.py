"""Facade to prepare AI runtime for an agent turn (prompt + tools + config + plan).

This module composes:
- AiPromptService.prepare_runtime(...) to build prompt/config/knowledge allow-list
- LlmOrchestrator.StartTurnContext to carry context into streaming orchestration

It does not call any LLM provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.ai_prompt_service import (
    AiPromptService,
    PreparedAgentRuntime,
)
from app.services.llm_orchestrator import (
    StartTurnContext,
    KnowledgePlan,  # re-used type
)


@dataclass(frozen=True)
class PreparedTurn:
    """Bundle returned to the caller to start an AI agent turn."""
    runtime: PreparedAgentRuntime
    start_ctx: StartTurnContext


class AgentTurnRuntimeService:
    """Small composition service to fetch {prompt, tools, config, plan} + start context."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.prompts = AiPromptService(session)

    def prepare(
        self,
        *,
        business_id: UUID,
        agent_id: UUID,
        conversation_id: UUID,
        top_k: int = 5,
        max_chunks_per_item: int = 3,
    ) -> PreparedTurn:
        runtime = self.prompts.prepare_runtime(
            business_id=business_id,
            agent_id=agent_id,
            top_k=top_k,
            max_chunks_per_item=max_chunks_per_item,
        )

        start_ctx = StartTurnContext(
            business_id=business_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            runtime_profile_version=runtime.runtime_profile_version,
            knowledge_plan=runtime.knowledge_plan,
        )

        return PreparedTurn(runtime=runtime, start_ctx=start_ctx)


__all__ = [
    "PreparedTurn",
    "AgentTurnRuntimeService",
]
