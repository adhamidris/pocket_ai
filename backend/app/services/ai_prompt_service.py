"""AI Prompt Service: compiler, RAG planner, escalation, and output parsing.

This module does **not** call any LLM provider. It prepares runtime configuration
for an agent turn: prompt template, structured output JSON schema, model config,
and a knowledge allow-list plan. It also validates final structured payloads.

Components
----------
- PromptCompiler
- RagPlanner
- EscalationEngine
- OutputParser
- AiPromptService (facade)

No database schema changes. Only SQLAlchemy reads for planning.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence
from uuid import UUID

from sqlalchemy import Select, and_, select
from sqlalchemy.orm import Session, joinedload

from app.models.cases import CasePriority, EscalationTrigger
from app.models.registration import (
    Agent,
    AgentRole,
    AgentTone,
    AgentTrait,
    EscalationRule,
    KnowledgeItem,
    KnowledgeStatus,
)
from app.models.agents_runtime import (
    AgentKpi,
    AgentKnowledgeAccess,
    AgentKnowledgeAccessState,
)
from app.models.knowledge_extras import KnowledgeCollection, KnowledgeCollectionLink
from app.schemas.ai_runtime import AiMessagePayload

# -------------------------
# Data contracts
# -------------------------


@dataclass(frozen=True)
class KnowledgePlan:
    """Allow-list and formatting plan for retrieval/citations."""
    item_ids: tuple[UUID, ...]
    collections: tuple[str, ...]
    top_k: int = 5
    max_chunks_per_item: int = 3
    citation_format: str = "[{display_name} §{chunk}]"


@dataclass(frozen=True)
class EscalationDecision:
    flagged: bool
    trigger: EscalationTrigger | None
    reason: str | None


@dataclass(frozen=True)
class RuntimeSignals:
    """Runtime signals that inform escalation rules (pluggable)."""
    fallback_count: int = 0
    tool_failures: int = 0
    sentiment_score: float | None = None  # expected -100..100
    case_priority: CasePriority | None = None
    sla_at_risk: bool = False
    manual_flag: bool = False


@dataclass(frozen=True)
class PreparedAgentRuntime:
    prompt_template: str
    tools_json: dict
    model_config_json: dict
    knowledge_plan: KnowledgePlan
    runtime_profile_version: int | None = None


# -------------------------
# Prompt compilation
# -------------------------


class PromptCompiler:
    """Compiles Agent role/tone/traits + KPIs into a prompt and tool schema."""

    def __init__(self) -> None:
        pass

    def _role_capabilities(self, role: AgentRole) -> list[str]:
        if role == AgentRole.SUPPORT or role == AgentRole.RESEARCH or role == AgentRole.SUCCESS:
            return [
                "- Diagnose the user's issue and ask for needed details (order/device/account).",
                "- Offer safe troubleshooting and next steps.",
                "- Categorize into case type and propose priority heuristics.",
            ]
        if role == AgentRole.SALES or role == AgentRole.MARKETING:
            return [
                "- Qualify the lead (need, budget, timeline).",
                "- Propose next steps and (optionally) appointment slots.",
                "- Capture contact info politely.",
            ]
        return [
            "- Be helpful and accurate according to allowed knowledge.",
            "- Ask clarifying questions if unsure.",
        ]

    def _trait_guidance(self, traits: Sequence[AgentTrait]) -> list[str]:
        tset = {t for t in traits}
        lines: list[str] = []
        if AgentTrait.CONCISE in tset:
            lines.append("- Keep responses concise (~3–5 sentences) unless asked for more.")
        if AgentTrait.DETAILED in tset:
            lines.append("- Provide step-by-step guidance and caveats when relevant.")
        if AgentTrait.PROACTIVE in tset:
            lines.append("- Proactively propose next steps or creating a case when warranted.")
        if AgentTrait.PATIENT in tset:
            lines.append("- Use reassuring, patient phrasing.")
        if AgentTrait.DIRECT in tset:
            lines.append("- Prefer clear, minimal preambles.")
        if AgentTrait.CREATIVE in tset:
            lines.append("- Offer 2–3 variations or options when helpful.")
        if AgentTrait.CURIOUS in tset:
            lines.append("- Ask clarifying questions before committing to an answer.")
        return lines

    def build_prompt_template(
        self,
        *,
        agent: Agent,
        business_name: str | None,
        language_pref: str | None,
        traits: Sequence[AgentTrait],
        kpis: Sequence[AgentKpi],
        knowledge_display_names: Sequence[str],
        escalation_summary: str,
    ) -> str:
        traits_csv = ", ".join(t.value for t in traits) if traits else "none"
        kpi_lines = [f"- {k.label}" + (f" (target: {k.target_value})" if k.target_value else "") for k in kpis if k.is_active]
        role_caps = self._role_capabilities(agent.role)
        trait_caps = self._trait_guidance(traits)

        knowledge_list = "\n".join(f"  • {name}" for name in knowledge_display_names) if knowledge_display_names else "  • (none)"
        biz = business_name or "your company"
        lang = language_pref or agent.default_language or "en"

        # System content
        prompt = "\n".join(
            [
                f"You are {agent.name}, a {agent.role.value} agent for {biz}.",
                f"Tone: {agent.tone.value}. Traits: {traits_csv}. Language: {lang}.",
                "",
                "Capabilities:",
                "- Answer only from the allowed knowledge sources listed below; cite sources.",
                "- If unsure and no relevant knowledge is available, ask clarifying questions.",
                "- Politely capture customer info (name, email/phone) when relevant.",
                "- For support: categorize issue; for sales: qualify and propose next steps.",
                "- Do not make irreversible promises or confirm appointments; only propose slots.",
                "- Emit structured JSON (schema provided) at the end of each turn under `payload_json`.",
                *role_caps,
                *trait_caps,
                "",
                "KPIs to optimize:",
                *(kpi_lines or ["- Stay helpful and time-efficient."]),
                "",
                "RAG policy:",
                "- Only use allowed items (list below) and cite them.",
                "- If no relevant knowledge, say so and proceed safely.",
                "Allowed knowledge items:",
                knowledge_list,
                "",
                "Escalation policy (summary): " + escalation_summary,
            ]
        )
        return prompt

    def build_tools_json(self) -> dict:
        """JSON schema for the structured payload (function/JSON mode)."""
        return {
            "name": "payload_json",
            "description": "Structured agent output envelope to persist in ConversationMessage.payload_json",
            "json_schema": AiMessagePayload.model_json_schema(),
        }

    def build_model_config_json(self, agent: Agent) -> dict:
        """Model config defaults; tunable per role later."""
        # Reasonable defaults; caller may override per role.
        temperature = 0.2 if agent.role in {AgentRole.SUPPORT, AgentRole.RESEARCH, AgentRole.SUCCESS} else 0.4
        top_p = 0.9
        return {
            "temperature": temperature,
            "top_p": top_p,
            "response_format": "json",
        }


# -------------------------
# RAG planner
# -------------------------


class RagPlanner:
    """Build an allow-list of knowledge items and collections."""

    def plan(
        self,
        session: Session,
        *,
        business_id: UUID,
        agent_id: UUID,
        top_k: int = 5,
        max_chunks_per_item: int = 3,
    ) -> KnowledgePlan:
        # Allowed item IDs (explicit per-agent access + READY)
        stmt_items: Select = (
            select(KnowledgeItem.id)
            .join(AgentKnowledgeAccess, AgentKnowledgeAccess.knowledge_item_id == KnowledgeItem.id)
            .where(
                KnowledgeItem.business_id == business_id,
                KnowledgeItem.status == KnowledgeStatus.READY,
                AgentKnowledgeAccess.agent_id == agent_id,
                AgentKnowledgeAccess.access_state == AgentKnowledgeAccessState.ALLOWED,
            )
        )
        item_ids = tuple(row[0] for row in session.execute(stmt_items).all())

        # Collection labels that contain allowed items (for context in prompt & UI)
        if item_ids:
            stmt_colls: Select = (
                select(KnowledgeCollection.label)
                .join(KnowledgeCollectionLink, KnowledgeCollectionLink.collection_id == KnowledgeCollection.id)
                .where(KnowledgeCollectionLink.knowledge_item_id.in_(item_ids))
            )
            collections = tuple(sorted({row[0] for row in session.execute(stmt_colls).all()}))
        else:
            collections = tuple()

        return KnowledgePlan(
            item_ids=item_ids,
            collections=collections,
            top_k=top_k,
            max_chunks_per_item=max_chunks_per_item,
        )


# -------------------------
# Escalation decision engine
# -------------------------


class EscalationEngine:
    """Computes escalation decisions based on configured rule and runtime signals."""

    def decide(self, rule: EscalationRule, signals: RuntimeSignals) -> EscalationDecision:
        # Manual flag always wins
        if signals.manual_flag:
            return EscalationDecision(True, EscalationTrigger.MANUAL, "Manually flagged by runtime/operator")

        # SLA risk can raise SLA_BREACH trigger regardless of rule
        if signals.sla_at_risk:
            return EscalationDecision(True, EscalationTrigger.SLA_BREACH, "SLA at risk for this turn")

        if rule == EscalationRule.ALWAYS:
            return EscalationDecision(True, EscalationTrigger.RULE, "Escalation rule: ALWAYS")

        if rule == EscalationRule.NEVER:
            return EscalationDecision(False, None, None)

        if rule == EscalationRule.ON_FALLBACK:
            if signals.fallback_count > 0 or signals.tool_failures > 0:
                return EscalationDecision(True, EscalationTrigger.RULE, "Fallback/tool failure encountered")
            return EscalationDecision(False, None, None)

        if rule == EscalationRule.ON_NEGATIVE_SENTIMENT:
            if signals.sentiment_score is not None and signals.sentiment_score < -20:
                return EscalationDecision(True, EscalationTrigger.NEGATIVE_SENTIMENT, "Strong negative sentiment detected")
            return EscalationDecision(False, None, None)

        if rule == EscalationRule.ON_HIGH_VALUE:
            if signals.case_priority in {CasePriority.HIGH, CasePriority.URGENT}:
                return EscalationDecision(True, EscalationTrigger.RULE, f"High-value case priority: {signals.case_priority.value}")
            return EscalationDecision(False, None, None)

        # Default safe fallback
        return EscalationDecision(False, None, None)


# -------------------------
# Output parsing/validation
# -------------------------


class OutputParser:
    """Validates model output into AiMessagePayload (extra fields forbidden)."""

    def parse_payload(self, data: dict | str) -> AiMessagePayload:
        if isinstance(data, str):
            import json
            parsed = json.loads(data)
        else:
            parsed = data
        return AiMessagePayload.model_validate(parsed)


# -------------------------
# Facade
# -------------------------


class AiPromptService:
    """Facade to prepare prompt + tools + config + knowledge plan for an agent turn."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.compiler = PromptCompiler()
        self.rag_planner = RagPlanner()
        self.escalation_engine = EscalationEngine()
        self.output_parser = OutputParser()

    def prepare_runtime(
        self,
        *,
        business_id: UUID,
        agent_id: UUID,
        top_k: int = 5,
        max_chunks_per_item: int = 3,
    ) -> PreparedAgentRuntime:
        # Load Agent with traits/kpis/biz details
        agent = self.session.execute(
            select(Agent)
            .options(
                joinedload(Agent.traits),
                joinedload(Agent.kpis),
                joinedload(Agent.business),
            )
            .where(Agent.id == agent_id, Agent.business_id == business_id)
        ).scalar_one()

        plan = self.rag_planner.plan(
            self.session,
            business_id=business_id,
            agent_id=agent_id,
            top_k=top_k,
            max_chunks_per_item=max_chunks_per_item,
        )

        # Resolve display names for prompt readability (best-effort)
        knowledge_names: list[str] = []
        if plan.item_ids:
            rows = self.session.execute(
                select(KnowledgeItem.display_name)
                .where(KnowledgeItem.id.in_(plan.item_ids))
            ).all()
            knowledge_names = [r[0] or "(unnamed item)" for r in rows]

        escalation_summary = self._summarize_escalation(agent.escalation_rule)

        prompt_template = self.compiler.build_prompt_template(
            agent=agent,
            business_name=getattr(agent.business, "name", None),
            language_pref=agent.default_language,
            traits=[t.trait_code for t in agent.traits],
            kpis=agent.kpis,
            knowledge_display_names=knowledge_names,
            escalation_summary=escalation_summary,
        )

        tools_json = self.compiler.build_tools_json()
        model_config_json = self.compiler.build_model_config_json(agent)

        return PreparedAgentRuntime(
            prompt_template=prompt_template,
            tools_json=tools_json,
            model_config_json=model_config_json,
            knowledge_plan=plan,
            runtime_profile_version=None,  # filled when persisted to AgentRuntimeProfile later
        )

    # -------------------------
    # Helpers
    # -------------------------

    def _summarize_escalation(self, rule: EscalationRule) -> str:
        mapping = {
            EscalationRule.NEVER: "Never escalate automatically.",
            EscalationRule.ALWAYS: "Always escalate after initial handling.",
            EscalationRule.ON_FALLBACK: "Escalate when fallbacks or tool failures occur.",
            EscalationRule.ON_NEGATIVE_SENTIMENT: "Escalate on strong negative sentiment.",
            EscalationRule.ON_HIGH_VALUE: "Escalate for high-priority/urgent cases.",
        }
        return mapping.get(rule, "Follow safe defaults; escalate when user requests or risk detected.")


__all__ = [
    "AiPromptService",
    "PromptCompiler",
    "RagPlanner",
    "EscalationEngine",
    "OutputParser",
    "KnowledgePlan",
    "EscalationDecision",
    "RuntimeSignals",
    "PreparedAgentRuntime",
]
