from __future__ import annotations

import dataclasses
import logging
import uuid
from enum import Enum
from typing import Callable, Iterable, Mapping, Sequence

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import AgentProfile, KnowledgeStatus, KnowledgeUpload
from apps.cases.models import Case, CaseHistoryEntry, CasePriority, CaseStatus
from apps.conversations.models import (
    Conversation,
    ConversationExtraction,
    ConversationExtractionType,
    ConversationStatus,
)
from apps.customers.models import Customer, CustomerRecordOrigin
from apps.services.ai_prompt_builder import PromptBuilder, PromptBundle
from apps.services.llm_provider import BaseLLMProvider, PromptGenerationError


logger = logging.getLogger(__name__)


class ActionType(str, Enum):
    CREATE_CASE = "create_case"
    UPDATE_CASE_STATUS = "update_case_status"
    UPDATE_CASE_DETAILS = "update_case_details"
    ADD_CASE_HISTORY = "add_case_history"
    FLAG_ESCALATION = "flag_escalation"
    CREATE_CUSTOMER = "create_customer"
    UPDATE_CUSTOMER = "update_customer"
    CREATE_LEAD = "create_lead"
    CREATE_APPOINTMENT = "create_appointment"


def _is_business_text(text: str | None) -> bool:
    if not text:
        return False
    normalized = text.strip().lower()
    if not normalized:
        return False
    greetings = {
        "hi",
        "hello",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
        "hola",
        "hey there",
    }
    if normalized in greetings:
        return False
    keywords = [
        "order",
        "invoice",
        "shipment",
        "payment",
        "account",
        "refund",
        "support",
        "issue",
        "problem",
        "error",
        "login",
        "subscription",
        "appointment",
        "contract",
        "service",
        "escalate",
        "case",
        "complaint",
        "ticket",
        "transfer",
        "finance",
    ]
    if any(keyword in normalized for keyword in keywords):
        return True
    if any(ch.isdigit() for ch in normalized):
        return True
    if any(sym in normalized for sym in ("$", "€", "£")):
        return True
    return len(normalized.split()) >= 6


@dataclasses.dataclass(frozen=True)
class ActionDescriptor:
    key: ActionType
    label: str
    description: str
    default_enabled: bool = True


ACTION_REGISTRY: dict[ActionType, ActionDescriptor] = {
    ActionType.CREATE_CASE: ActionDescriptor(
        key=ActionType.CREATE_CASE,
        label="Create Case",
        description="Create a structured case with AI diagnosis and suggested actions.",
    ),
    ActionType.UPDATE_CASE_STATUS: ActionDescriptor(
        key=ActionType.UPDATE_CASE_STATUS,
        label="Update Case Status",
        description="Change the linked case lifecycle (open/resolved).",
        default_enabled=True,
    ),
    ActionType.UPDATE_CASE_DETAILS: ActionDescriptor(
        key=ActionType.UPDATE_CASE_DETAILS,
        label="Update Case Details",
        description="Refresh case title, description, or priority using new info.",
    ),
    ActionType.ADD_CASE_HISTORY: ActionDescriptor(
        key=ActionType.ADD_CASE_HISTORY,
        label="Add Case History Entry",
        description="Log significant updates to the case timeline without mutating the description.",
    ),
    ActionType.FLAG_ESCALATION: ActionDescriptor(
        key=ActionType.FLAG_ESCALATION,
        label="Flag Escalation",
        description="Escalate the conversation for human follow-up.",
        default_enabled=True,
    ),
    ActionType.CREATE_CUSTOMER: ActionDescriptor(
        key=ActionType.CREATE_CUSTOMER,
        label="Create Customer",
        description="Capture a new customer record extracted from the chat.",
    ),
    ActionType.UPDATE_CUSTOMER: ActionDescriptor(
        key=ActionType.UPDATE_CUSTOMER,
        label="Update Customer",
        description="Refresh an existing customer profile with new details.",
    ),
    ActionType.CREATE_LEAD: ActionDescriptor(
        key=ActionType.CREATE_LEAD,
        label="Create Lead",
        description="Store structured lead intents discovered in chat.",
    ),
    ActionType.CREATE_APPOINTMENT: ActionDescriptor(
        key=ActionType.CREATE_APPOINTMENT,
        label="Create Appointment",
        description="Persist appointment requests for downstream scheduling.",
    ),
}


@dataclasses.dataclass(frozen=True)
class KnowledgeSnippet:
    id: uuid.UUID
    title: str
    summary: str
    source: str


@dataclasses.dataclass(frozen=True)
class PlannedAction:
    action: ActionType
    payload: dict


@dataclasses.dataclass(frozen=True)
class ExtractionPlan:
    extraction_type: ConversationExtractionType
    payload: dict


@dataclasses.dataclass(frozen=True)
class AiOrchestratorPlan:
    response_text: str
    citations: Sequence[KnowledgeSnippet]
    planned_actions: Sequence[PlannedAction]
    extractions: Sequence[ExtractionPlan]
    diagnostics: dict


@dataclasses.dataclass(frozen=True)
class ActionExecutionResult:
    action: ActionType
    status: str
    metadata: dict
    error: str | None = None


class ActionExecutionError(Exception):
    """Raised when an action cannot be executed."""


class KnowledgeSearchService:
    """Stubbed RAG search leveraging the KnowledgeUpload catalog."""

    def search(self, *, business_profile, query: str, limit: int = 3) -> Sequence[KnowledgeSnippet]:
        qs = (
            KnowledgeUpload.objects.filter(
                business_profile=business_profile,
                status=KnowledgeStatus.ACTIVE,
            )
            .order_by("-updated_at")[:limit]
        )
        snippets: list[KnowledgeSnippet] = []
        for upload in qs:
            snippets.append(
                KnowledgeSnippet(
                    id=upload.id,
                    title=upload.display_name or upload.source_name or "Untitled document",
                    summary=(upload.summary or upload.description or "")[:280],
                    source=upload.source_name or upload.source_type,
                )
            )
        return tuple(snippets)


@dataclasses.dataclass(frozen=True)
class LlmPlan:
    response_text: str
    planned_actions: Sequence[PlannedAction]
    extractions: Sequence[ExtractionPlan]


class AiOrchestratorService:
    """
    Coordinates knowledge retrieval, action planning, and future LLM interactions.

    The current implementation uses heuristic planning to keep the pipeline testable
    until the prompt builder + provider integration is ready.
    """

    def __init__(self, *, agent: AgentProfile, provider: BaseLLMProvider | None = None):
        self.agent = agent
        self.knowledge_service = KnowledgeSearchService()
        self.prompt_builder = PromptBuilder(agent)
        self.provider = provider
        self._permission_cache: dict[str, bool] = {}
        self._hydrate_permissions()

    # ------------------------------------------------------------------
    # Public API

    def run_turn(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        on_response_text_delta: Callable[[str], None] | None = None,
    ) -> AiOrchestratorPlan:
        """
        Build the orchestration plan for the latest customer message.

        Returns the AI response text placeholder, citations, action plans, and any
        extracted entities (lead, appointment, etc.) that should be persisted.
        """

        query = user_message.strip()
        logger.info(
            "orchestrator turn start conversation=%s agent=%s case=%s message=%s",
            conversation.id,
            self.agent.id,
            conversation.case_id,
            query[:160],
        )
        citations = self.knowledge_service.search(
            business_profile=conversation.business_profile,
            query=query,
        )
        actions_catalog = self._actions_catalog()
        recent_messages = self._recent_messages(conversation)
        prompt_bundle = self.prompt_builder.build(
            conversation=conversation,
            knowledge_snippets=[
                {"id": str(snippet.id), "title": snippet.title, "summary": snippet.summary, "source": snippet.source}
                for snippet in citations
            ],
            transcript=recent_messages,
            actions_catalog=actions_catalog,
        )

        llm_plan = self._invoke_llm(prompt_bundle, on_response_text_delta=on_response_text_delta)
        if llm_plan:
            planned_actions = list(llm_plan.planned_actions)
            extractions = list(llm_plan.extractions)
            response_text = llm_plan.response_text
            llm_source = "provider"
        else:
            planned_actions, extractions = self._plan_actions(conversation=conversation, user_message=query)
            response_text = self._compose_placeholder_response(
                user_message=query,
                citations=citations,
                planned_actions=planned_actions,
            )
            llm_source = "heuristic"

        diagnostics = {
            "planned_action_count": len(planned_actions),
            "extraction_count": len(extractions),
            "citations": [snippet.title for snippet in citations],
            "llm_strategy": llm_source,
            "prompt_preview": prompt_bundle.system_prompt[:160],
            "transcript_messages": len(prompt_bundle.transcript),
        }

        self._log_plan_summary(
            conversation=conversation,
            source=llm_source,
            planned_actions=planned_actions,
            extractions=extractions,
            diagnostics=diagnostics,
        )

        return AiOrchestratorPlan(
            response_text=response_text,
            citations=citations,
            planned_actions=planned_actions,
            extractions=extractions,
            diagnostics=diagnostics,
        )

    # ------------------------------------------------------------------
    # Internal helpers

    def _hydrate_permissions(self) -> None:
        permissions = self.agent.action_permissions.all() if hasattr(self.agent, "action_permissions") else []
        for perm in permissions:
            self._permission_cache[str(perm.action_key)] = perm.is_enabled

    def _is_enabled(self, action: ActionType) -> bool:
        if action.value in self._permission_cache:
            return self._permission_cache[action.value]
        descriptor = ACTION_REGISTRY.get(action)
        return descriptor.default_enabled if descriptor else False

    def _actions_catalog(self) -> list[Mapping[str, str]]:
        catalog: list[Mapping[str, str]] = []
        for action, descriptor in ACTION_REGISTRY.items():
            catalog.append(
                {
                    "key": action.value,
                    "label": descriptor.label,
                    "description": descriptor.description,
                    "enabled": self._is_enabled(action),
                }
            )
        return catalog

    @staticmethod
    def _recent_messages(conversation: Conversation, limit: int = 8) -> Sequence:
        qs = conversation.messages.all().order_by("-sent_at", "-created_at")[:limit]
        return tuple(reversed(tuple(qs)))

    def _invoke_llm(self, bundle: PromptBundle, *, on_response_text_delta: Callable[[str], None] | None = None) -> LlmPlan | None:
        if not self.provider:
            logger.info("LLM provider is not configured; using heuristic planner.")
            return None
        try:
            logger.info("Invoking LLM provider %s", self.provider.__class__.__name__)
            raw = self.provider.generate(bundle, on_stream_delta=on_response_text_delta)
        except PromptGenerationError as exc:
            logger.warning("LLM provider failed; falling back to heuristics: %s", exc)
            return None
        return self._parse_llm_plan(raw)

    def _log_plan_summary(
        self,
        *,
        conversation: Conversation,
        source: str,
        planned_actions: Sequence[PlannedAction],
        extractions: Sequence[ExtractionPlan],
        diagnostics: Mapping[str, object],
    ) -> None:
        action_dump = [
            {
                "action": plan.action.value,
                "payload_keys": sorted(plan.payload.keys()),
            }
            for plan in planned_actions
        ]
        extraction_dump = [
            {
                "type": extraction.extraction_type.value,
                "payload_keys": sorted(extraction.payload.keys()),
            }
            for extraction in extractions
        ]

        logger.info(
            "orchestrator plan conversation=%s source=%s actions=%s extractions=%s diagnostics=%s",
            conversation.id,
            source,
            action_dump,
            extraction_dump,
            diagnostics,
        )

    def _parse_llm_plan(self, raw_payload: Mapping[str, object]) -> LlmPlan | None:
        text = str(raw_payload.get("response_text") or "").strip()
        if not text:
            return None
        planned_actions: list[PlannedAction] = []
        for action_payload in raw_payload.get("actions", []) or []:
            key = action_payload.get("action") if isinstance(action_payload, dict) else None
            if not key:
                continue
            try:
                action_type = ActionType(key)
            except ValueError:
                continue
            if not self._is_enabled(action_type):
                continue
            planned_actions.append(PlannedAction(action=action_type, payload=action_payload.get("payload") or {}))

        extractions: list[ExtractionPlan] = []
        for extraction_payload in raw_payload.get("extractions", []) or []:
            if not isinstance(extraction_payload, dict):
                continue
            kind = extraction_payload.get("type")
            try:
                extraction_type = ConversationExtractionType(kind)
            except ValueError:
                continue
            extractions.append(
                ExtractionPlan(
                    extraction_type=extraction_type,
                    payload=extraction_payload.get("payload") or {},
                )
            )

        return LlmPlan(response_text=text, planned_actions=planned_actions, extractions=extractions)

    def _plan_actions(self, *, conversation: Conversation, user_message: str) -> tuple[list[PlannedAction], list[ExtractionPlan]]:
        plans: list[PlannedAction] = []
        extractions: list[ExtractionPlan] = []
        lower = user_message.lower()

        if (
            conversation.case_id is None
            and self._is_enabled(ActionType.CREATE_CASE)
            and _is_business_text(user_message)
        ):
            plans.append(
                PlannedAction(
                    action=ActionType.CREATE_CASE,
                    payload={
                        "title": self._derive_case_title(user_message),
                        "description": user_message,
                        "priority": CasePriority.HIGH if "urgent" in lower or "immediately" in lower else CasePriority.MEDIUM,
                        "ai_diagnosis": f"Initial issue reported: {user_message[:500]}",
                        "ai_actions_taken": "",
                        "ai_suggested_actions": [
                            "Review customer account",
                            "Follow up via email once resolved",
                        ],
                        "metadata": {"source": "ai_orchestrator"},
                    },
                )
            )

        if conversation.case_id and "resolved" in lower and self._is_enabled(ActionType.UPDATE_CASE_STATUS):
            plans.append(
                PlannedAction(
                    action=ActionType.UPDATE_CASE_STATUS,
                    payload={"status": CaseStatus.CLOSED},
                )
            )

        if (
            conversation.case_id
            and self._is_enabled(ActionType.ADD_CASE_HISTORY)
            and _is_business_text(user_message)
        ):
            summary = self._summarize_history(user_message)
            if summary:
                plans.append(
                    PlannedAction(
                        action=ActionType.ADD_CASE_HISTORY,
                        payload={
                            "summary": summary,
                            "source": "customer",
                            "metadata": {"auto_generated": True},
                        },
                    )
                )

        if "escalate" in lower and self._is_enabled(ActionType.FLAG_ESCALATION):
            plans.append(
                PlannedAction(
                    action=ActionType.FLAG_ESCALATION,
                    payload={"reason": "Customer requested escalation", "priority": CasePriority.CRITICAL},
                )
            )

        if "appointment" in lower and self._is_enabled(ActionType.CREATE_APPOINTMENT):
            extractions.append(
                ExtractionPlan(
                    extraction_type=ConversationExtractionType.APPOINTMENT,
                    payload={"note": user_message, "detected_at": timezone.now().isoformat()},
                )
            )

        if "lead" in lower or "pricing" in lower:
            extractions.append(
                ExtractionPlan(
                    extraction_type=ConversationExtractionType.LEAD,
                    payload={"note": user_message, "interest": "pricing" if "pricing" in lower else "general"},
                )
            )

        if "complaint" in lower:
            extractions.append(
                ExtractionPlan(
                    extraction_type=ConversationExtractionType.COMPLAINT,
                    payload={"note": user_message},
                )
            )

        return plans, extractions

    @staticmethod
    def _derive_case_title(user_message: str) -> str:
        base = user_message.split(".")[0][:80]
        return base or "Customer issue reported"

    @staticmethod
    def _infer_customer_name(user_message: str) -> str:
        tokens = user_message.split()
        hint = tokens[0].strip(",:") if tokens else "Customer"
        return hint.title() if hint else "Customer"

    @staticmethod
    def _summarize_history(user_message: str) -> str:
        snippet = " ".join(user_message.strip().split())
        return snippet[:320]

    def _compose_placeholder_response(
        self,
        *,
        user_message: str,
        citations: Sequence[KnowledgeSnippet],
        planned_actions: Sequence[PlannedAction],
    ) -> str:
        lines = [
            f"Thanks for the update. I’m reviewing your note: \"{user_message[:160]}\"."
        ]
        if citations:
            cited = ", ".join(snippet.title for snippet in citations)
            lines.append(f"I’m referencing our latest resources ({cited}) to keep details accurate.")
        lines.append("I’ll follow up with any updates as soon as they’re ready—feel free to share more context meanwhile.")
        return " ".join(lines)


class ActionDispatcher:
    """Executes orchestrator planned actions if the agent has them enabled."""

    def __init__(self, *, agent: AgentProfile):
        self.agent = agent

    def execute(self, *, conversation: Conversation, planned_actions: Iterable[PlannedAction]) -> Sequence[ActionExecutionResult]:
        results: list[ActionExecutionResult] = []
        for plan in planned_actions:
            handler = getattr(self, f"_handle_{plan.action.value}", None)
            if handler is None:
                logger.warning("action_dispatcher skipped action %s: no handler", plan.action)
                results.append(
                    ActionExecutionResult(
                        action=plan.action,
                        status="skipped",
                        metadata={},
                        error="Handler not implemented",
                    )
                )
                continue
            try:
                metadata = handler(conversation=conversation, payload=plan.payload)
                logger.info("action_dispatcher applied %s | payload=%s", plan.action, plan.payload)
                results.append(
                    ActionExecutionResult(
                        action=plan.action,
                        status="applied",
                        metadata=metadata,
                    )
                )
            except ActionExecutionError as exc:
                logger.warning("action_dispatcher failed %s | error=%s | payload=%s", plan.action, exc, plan.payload)
                results.append(
                    ActionExecutionResult(
                        action=plan.action,
                        status="failed",
                        metadata={},
                        error=str(exc),
                    )
                )
        return tuple(results)

    # ------------------------------------------------------------------
    # Individual action handlers

    def _handle_create_case(self, *, conversation: Conversation, payload: dict) -> dict:
        if conversation.case_id:
            return {"case_id": str(conversation.case_id), "skipped": True}
        description = (payload.get("description") or "").strip()
        if not _is_business_text(description):
            raise ActionExecutionError("Case description lacks business context; creation aborted.")
        with transaction.atomic():
            metadata_payload = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            case = Case.objects.create(
                business_profile=conversation.business_profile,
                agent_profile=conversation.agent_profile,
                customer=conversation.customer,
                title=payload.get("title") or "Customer request",
                description=payload.get("description") or "",
                priority=payload.get("priority") or CasePriority.MEDIUM,
                status=CaseStatus.OPEN,
                ai_diagnosis=payload.get("ai_diagnosis", ""),
                ai_actions_taken=payload.get("ai_actions_taken", ""),
                ai_suggested_actions=payload.get("ai_suggested_actions", []),
                metadata={"source": "ai_orchestrator", **metadata_payload},
            )
            conversation.case = case
            conversation.status = ConversationStatus.LIVE
            conversation.save(update_fields=["case", "status", "last_activity_at"])
        return {"case_id": str(case.id), "case_number": case.case_number}

    def _handle_update_case_status(self, *, conversation: Conversation, payload: dict) -> dict:
        if not conversation.case_id:
            raise ActionExecutionError("No linked case to update")
        status = (payload.get("status") or "").strip().lower()
        alias = {
            "resolved": CaseStatus.CLOSED,
            "close": CaseStatus.CLOSED,
            "closed": CaseStatus.CLOSED,
            "open": CaseStatus.OPEN,
            "reopen": CaseStatus.OPEN,
        }
        status = alias.get(status, status)
        if status not in CaseStatus.values:
            raise ActionExecutionError(f"Unsupported status transition: {payload.get('status')}")
        fields = ["last_activity_at"]
        conversation.case.status = status
        conversation.case.save(update_fields=["status", "updated_at"])
        if status == CaseStatus.CLOSED:
            # Preserve the live chat session so the visitor can continue chatting even after the case closes.
            fields.append("closed_at")
        conversation.save(update_fields=fields)
        return {"case_id": str(conversation.case_id), "status": status}

    def _handle_update_case_details(self, *, conversation: Conversation, payload: dict) -> dict:
        if not conversation.case_id:
            raise ActionExecutionError("No linked case to update")
        case = conversation.case
        fields: list[str] = []
        if payload.get("description"):
            if not payload.get("allow_description_overwrite"):
                raise ActionExecutionError("Description updates require allow_description_overwrite=true")
            if not _is_business_text(payload["description"]):
                raise ActionExecutionError("New description lacks business context")
        for attr in ("title", "ai_diagnosis", "ai_actions_taken"):
            if payload.get(attr) and getattr(case, attr) != payload[attr]:
                setattr(case, attr, payload[attr])
                fields.append(attr)
        if payload.get("description") and case.description != payload["description"]:
            case.description = payload["description"]
            fields.append("description")
        priority = payload.get("priority")
        if priority:
            normalized = priority.lower()
            alias = {
                "high": CasePriority.HIGH,
                "critical": CasePriority.CRITICAL,
                "medium": CasePriority.MEDIUM,
                "low": CasePriority.LOW,
            }
            normalized = alias.get(normalized, normalized)
            if normalized in CasePriority.values and case.priority != normalized:
                case.priority = normalized
                fields.append("priority")
        metadata = payload.get("metadata")
        if metadata and isinstance(metadata, dict):
            case.metadata = {**(case.metadata or {}), **metadata}
            fields.append("metadata")
        if not fields:
            return {"case_id": str(case.id), "updated_fields": []}
        fields.append("updated_at")
        case.save(update_fields=fields)
        return {"case_id": str(case.id), "updated_fields": fields}

    def _handle_flag_escalation(self, *, conversation: Conversation, payload: dict) -> dict:
        reason = payload.get("reason") or "Escalated by AI orchestrator"
        conversation.status = ConversationStatus.ESCALATED
        conversation.save(update_fields=["status", "last_activity_at"])
        if conversation.case_id:
            case = conversation.case
            priority = payload.get("priority") or CasePriority.CRITICAL
            case.priority = priority
            case.save(update_fields=["priority", "updated_at"])
        ConversationExtraction.objects.create(
            conversation=conversation,
            extraction_type=ConversationExtractionType.ESCALATION,
            payload={"reason": reason, "metadata": payload},
        )
        return {"reason": reason}

    def _handle_add_case_history(self, *, conversation: Conversation, payload: dict) -> dict:
        if not conversation.case_id:
            raise ActionExecutionError("No linked case to update")
        summary = (payload.get("summary") or "").strip()
        if not summary:
            raise ActionExecutionError("History summary is required")
        source = payload.get("source") or "ai"
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        case = conversation.case
        if not case:
            case = Case.objects.filter(pk=conversation.case_id).first()
            if not case:
                raise ActionExecutionError("Case not found")
        entry = CaseHistoryEntry.objects.create(
            case=case,
            summary=summary[:500],
            source=source,
            session_reference=str(conversation.session_token),
            metadata={**metadata, "author": "ai_orchestrator"},
        )
        return {"history_id": str(entry.id)}

    def _handle_create_customer(self, *, conversation: Conversation, payload: dict) -> dict:
        business = conversation.business_profile
        email_raw = payload.get("primary_email") or payload.get("email")
        phone_raw = (
            payload.get("primary_phone")
            or payload.get("phone")
            or payload.get("mobile")
            or payload.get("contact_number")
        )
        email = self._normalize_email(email_raw)
        phone = self._normalize_phone(phone_raw)
        display_name = (payload.get("display_name") or "").strip() or "Web Visitor"
        refused_contact = bool(payload.get("refused_contact"))

        existing = self._match_customer(business_profile=business, email=email, phone=phone)
        if existing:
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            updated_fields: list[str] = []
            if (
                display_name
                and display_name.lower() not in {"web visitor", "customer"}
                and existing.display_name != display_name
            ):
                existing.display_name = display_name
                updated_fields.append("display_name")
            if metadata:
                existing.metadata = {**(existing.metadata or {}), **metadata}
                updated_fields.append("metadata")
            if updated_fields:
                updated_fields.append("updated_at")
                existing.save(update_fields=updated_fields)
            conversation.customer = existing
            conversation.save(update_fields=["customer", "last_activity_at"])
            self._link_customer_to_case(conversation=conversation, customer=existing)
            return {"customer_id": str(existing.id), "matched": True}

        if not email and not phone and not refused_contact:
            raise ActionExecutionError("Cannot create customer without email/phone or explicit refusal.")

        with transaction.atomic():
            customer = Customer.objects.create(
                business_profile=business,
                agent_profile=conversation.agent_profile,
                display_name=display_name,
                primary_email=email or "",
                primary_phone=phone_raw or "",
                record_origin=payload.get("record_origin") or CustomerRecordOrigin.AI_EXTRACTED,
                metadata={**(payload.get("metadata") or {}), **({"contact_refused": True} if not email and not phone else {})},
            )
            conversation.customer = customer
            conversation.save(update_fields=["customer", "last_activity_at"])
        self._link_customer_to_case(conversation=conversation, customer=customer)
        return {"customer_id": str(customer.id), "matched": False}

    def _handle_update_customer(self, *, conversation: Conversation, payload: dict) -> dict:
        if not conversation.customer_id:
            raise ActionExecutionError("No customer attached to conversation")
        customer = conversation.customer
        fields = []
        for attr in ("primary_email", "primary_phone"):
            if attr in payload and payload.get(attr):
                raise ActionExecutionError(f"Updating {attr} is not allowed")
        if payload.get("display_name") and customer.display_name != payload["display_name"]:
            customer.display_name = payload["display_name"]
            fields.append("display_name")
        if payload.get("metadata"):
            customer.metadata = {**(customer.metadata or {}), **payload["metadata"]}
            fields.append("metadata")
        if not fields:
            return {"customer_id": str(customer.id), "updated": False}
        fields.append("updated_at")
        customer.save(update_fields=fields)
        return {"customer_id": str(customer.id), "updated": True}

    def _match_customer(self, *, business_profile, email: str | None, phone: str | None) -> Customer | None:
        qs = Customer.objects.filter(business_profile=business_profile)
        if email:
            try:
                return qs.get(primary_email__iexact=email)
            except Customer.DoesNotExist:
                pass
        if phone:
            normalized_phone = phone
            for candidate in qs.exclude(primary_phone=""):
                if self._normalize_phone(candidate.primary_phone) == normalized_phone:
                    return candidate
        return None

    @staticmethod
    def _normalize_email(value: str | None) -> str | None:
        if not value:
            return None
        email = value.strip().lower()
        return email or None

    @staticmethod
    def _normalize_phone(value: str | None) -> str | None:
        if not value:
            return None
        digits = "".join(ch for ch in value if ch.isdigit())
        return digits or None

    def _link_customer_to_case(self, *, conversation: Conversation, customer: Customer) -> None:
        if not conversation.case_id:
            return
        case = conversation.case
        if not case:
            try:
                case = Case.objects.get(pk=conversation.case_id)
            except Case.DoesNotExist:
                return
        if case.customer_id == customer.id:
            return
        case.customer = customer
        case.customer_snapshot = {
            "display_name": customer.display_name,
            "primary_email": customer.primary_email,
            "primary_phone": customer.primary_phone,
        }
        case.save(update_fields=["customer", "customer_snapshot", "updated_at"])

    def _handle_create_lead(self, *, conversation: Conversation, payload: dict) -> dict:
        extraction = ConversationExtraction.objects.create(
            conversation=conversation,
            extraction_type=ConversationExtractionType.LEAD,
            payload=payload or {},
        )
        return {"extraction_id": str(extraction.id)}

    def _handle_create_appointment(self, *, conversation: Conversation, payload: dict) -> dict:
        extraction = ConversationExtraction.objects.create(
            conversation=conversation,
            extraction_type=ConversationExtractionType.APPOINTMENT,
            payload=payload or {},
        )
        return {"extraction_id": str(extraction.id)}
