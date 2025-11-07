"""Consume AiMessagePayload and apply side-effects (Customer, Case, Escalation).

This service is intended to be called AFTER an AGENT message has been persisted
(with its validated AiMessagePayload stored in ConversationMessage.payload_json).

Responsibilities
- Customer upsert from payload.customer_capture (attach to Conversation).
- Case create/update from payload.case (attach to Conversation) and add CaseLinks.
- Escalation create when payload.escalation.flagged (update Case + Conversation statuses).

No database schema changes; uses existing models and CustomersService.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.schemas.ai_runtime import (
    AiMessagePayload,
    AiContactMethod,
)
from app.models.conversations import Conversation, ConversationStatus
from app.models.cases import (
    Case,
    CaseLink,
    CaseLinkTargetType,
    CasePriority,
    CaseStatus,
    CaseType,
    Escalation,
    EscalationStatus,
    EscalationTrigger,
)
from app.models.customers import Customer
from app.services.customers import (
    CustomersService,
    CreateCustomerInput,
    UpdateCustomerInput,
    CustomerContactMethodDTO,
    CustomerContactMethodType,
)
from app.services.errors import ServiceNotFoundError


@dataclass(frozen=True)
class ApplyResult:
    business_id: UUID
    conversation_id: UUID
    customer_id: UUID | None
    case_id: UUID | None
    escalated: bool


class AiPayloadConsumer:
    """Applies structured AI payload side-effects safely and idempotently."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.customers = CustomersService(session)

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #
    def apply(
        self,
        *,
        business_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        payload: AiMessagePayload,
        agent_id: UUID | None = None,
    ) -> ApplyResult:
        """Apply side-effects described by `payload` to domain entities.

        Idempotency: We avoid duplicate creations by:
        - Reusing `Conversation.customer_id` if already set.
        - Reusing/setting `Conversation.case_id` when creating/updating a case.
        - De-duping CaseLinks on (target_type, target_id, external_url).
        """
        # Load conversation (ownership check)
        convo = self._get_conversation(business_id, conversation_id)

        # 1) Customer upsert
        customer_id = self._apply_customer(business_id, convo, payload)

        # 2) Case create/update + links
        case_id = self._apply_case(business_id, convo, customer_id, payload)

        # 3) Escalation (if flagged)
        escalated = self._apply_escalation(business_id, convo, case_id, agent_id, payload)

        self.session.flush()

        return ApplyResult(
            business_id=business_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            case_id=case_id,
            escalated=escalated,
        )

    # --------------------------------------------------------------------- #
    # Internals
    # --------------------------------------------------------------------- #
    def _get_conversation(self, business_id: UUID, conversation_id: UUID) -> Conversation:
        stmt = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.business_id == business_id,
        )
        convo = self.session.execute(stmt).scalar_one_or_none()
        if convo is None:
            raise ServiceNotFoundError("Conversation not found")
        return convo

    def _apply_customer(
        self,
        business_id: UUID,
        convo: Conversation,
        payload: AiMessagePayload,
    ) -> UUID | None:
        cc = payload.customer_capture
        if cc is None:
            return convo.customer_id

        # Prefer existing conversation customer if present
        if convo.customer_id:
            # Update minimal fields (only those provided)
            self._update_customer_from_capture(business_id, convo.customer_id, cc)
            return convo.customer_id

        # Try lookup by primary_email first (unique per business)
        email = (cc.primary_email or "").strip() or None
        phone = (cc.primary_phone or "").strip() or None
        existing = None
        if email:
            existing = self.session.execute(
                select(Customer).where(Customer.business_id == business_id, Customer.primary_email == email)
            ).scalar_one_or_none()
        if existing is None and phone:
            existing = self.session.execute(
                select(Customer).where(Customer.business_id == business_id, Customer.primary_phone == phone)
            ).scalar_one_or_none()

        if existing:
            # Update
            self._update_customer_from_capture(business_id, existing.id, cc)
            convo.customer_id = existing.id
            self.session.add(convo)
            return existing.id

        # Create
        cm_list: list[CustomerContactMethodDTO] = []
        for m in (cc.contact_methods or ()):
            if isinstance(m, AiContactMethod):
                cm_list.append(CustomerContactMethodDTO(method_type=m.type, value=m.value, is_primary=m.is_primary))
            else:
                # If dict-like (shouldn't happen after validation), best-effort cast
                try:
                    cm_list.append(
                        CustomerContactMethodDTO(
                            method_type=CustomerContactMethodType(m.get("type")),  # type: ignore[arg-type]
                            value=str(m.get("value", "")),
                            is_primary=bool(m.get("is_primary", False)),
                        )
                    )
                except Exception:
                    continue

        create_input = CreateCustomerInput(
            business_id=business_id,
            full_name=(cc.full_name or "").strip() or "Visitor",
            primary_email=email,
            primary_phone=phone,
            country=cc.country,
            # Reasonable defaults; these can be updated by business logic later
            lifecycle_stage=self._default_lifecycle_stage(),
            satisfaction_score=None,
            persona_tags=(),
            contact_methods=tuple(cm_list),
            tag_ids=(),
        )
        detail = self.customers.create_customer(create_input)
        convo.customer_id = detail.id
        self.session.add(convo)
        return detail.id

    def _update_customer_from_capture(self, business_id: UUID, customer_id: UUID, cc) -> None:
        # Build contact methods DTOs if provided
        cm: list[CustomerContactMethodDTO] = []
        for m in (cc.contact_methods or ()):
            cm.append(CustomerContactMethodDTO(method_type=m.type, value=m.value, is_primary=m.is_primary))

        update_input = UpdateCustomerInput(
            business_id=business_id,
            customer_id=customer_id,
            full_name=cc.full_name,
            primary_email=(cc.primary_email or "").strip() or None,
            primary_phone=(cc.primary_phone or "").strip() or None,
            country=cc.country,
            lifecycle_stage=None,
            satisfaction_score=None,
            persona_tags=None,
            contact_methods=tuple(cm) if cm else None,
            tag_ids=None,
        )
        self.customers.update_customer(update_input)

    def _default_lifecycle_stage(self):
        # Late import to avoid heavy models import if not needed
        from app.models.customers import CustomerLifecycleStage

        return CustomerLifecycleStage.LEAD

    def _apply_case(
        self,
        business_id: UUID,
        convo: Conversation,
        customer_id: UUID | None,
        payload: AiMessagePayload,
    ) -> UUID | None:
        cp = payload.case
        if cp is None:
            return convo.case_id

        now = datetime.now(timezone.utc)

        # Determine existing case (treat create as update if exists)
        case: Case | None = None
        if convo.case_id:
            case = self.session.get(Case, convo.case_id)

        # Handle create
        if cp.action == "create" and case is None:
            case = Case(
                business_id=business_id,
                customer_id=customer_id,
                origin_conversation_id=convo.id,
                title=(cp.title or "Untitled case"),
                description=cp.description,
                priority=cp.priority or CasePriority.MEDIUM,
                case_type=cp.case_type or CaseType.OTHER,
                status=cp.status or CaseStatus.OPEN,
                opened_at=now,
            )
            self.session.add(case)
            self.session.flush()
            convo.case_id = case.id
            self.session.add(convo)

        # Handle update (or create->update path if created above)
        if cp.action in ("update", "create") and case is not None:
            if cp.title is not None:
                case.title = cp.title
            if cp.description is not None:
                case.description = cp.description
            if cp.priority is not None:
                case.priority = cp.priority
            if cp.case_type is not None:
                case.case_type = cp.case_type
            if cp.status is not None:
                case.status = cp.status
                if cp.status == CaseStatus.RESOLVED and case.resolved_at is None:
                    case.resolved_at = now
                if cp.status == CaseStatus.ESCALATED and case.escalated_at is None:
                    case.escalated_at = now
            # Add links (dedupe)
            if cp.links:
                self._add_case_links_if_missing(case.id, cp.links)

            self.session.add(case)

        return case.id if case is not None else convo.case_id

    def _add_case_links_if_missing(self, case_id: UUID, links: Sequence) -> None:
        # Build an in-memory set for existing unique keys (target_type, target_id, external_url)
        existing_rows = self.session.execute(
            select(CaseLink).where(CaseLink.case_id == case_id)
        ).scalars().all()
        existing_keys = {
            (row.target_type, str(row.target_id) if row.target_id else None, row.external_url or None) for row in existing_rows
        }

        for link in links:
            tt = link.target_type
            tid = str(link.target_id) if link.target_id else None
            url = link.external_url or None
            key = (tt, tid, url)
            if key in existing_keys:
                continue
            self.session.add(
                CaseLink(
                    case_id=case_id,
                    target_type=tt,
                    target_id=link.target_id,
                    external_url=link.external_url,
                    metadata_json=link.metadata_json,
                )
            )
            existing_keys.add(key)

    def _apply_escalation(
        self,
        business_id: UUID,
        convo: Conversation,
        case_id: UUID | None,
        agent_id: UUID | None,
        payload: AiMessagePayload,
    ) -> bool:
        esc = payload.escalation
        if esc is None or not esc.flagged:
            return False

        now = datetime.now(timezone.utc)

        # Ensure we have a case to attach escalation to; if not, create minimal case
        case: Case | None = None
        if case_id:
            case = self.session.get(Case, case_id)

        if case is None:
            case = Case(
                business_id=business_id,
                customer_id=convo.customer_id,
                origin_conversation_id=convo.id,
                title="Escalation",
                description=esc.reason,
                priority=CasePriority.HIGH,
                case_type=CaseType.ESCALATION,
                status=CaseStatus.ESCALATED,
                opened_at=now,
                escalated_at=now,
            )
            self.session.add(case)
            self.session.flush()
            convo.case_id = case.id
            self.session.add(convo)

        # Update case status to ESCALATED if not already
        if case.status != CaseStatus.ESCALATED:
            case.status = CaseStatus.ESCALATED
            if case.escalated_at is None:
                case.escalated_at = now
            self.session.add(case)

        # Create Escalation row
        trig = esc.trigger or EscalationTrigger.RULE
        escalation = Escalation(
            case_id=case.id,
            conversation_id=convo.id,
            agent_id=agent_id,
            status=EscalationStatus.PENDING_REVIEW,
            trigger=trig,
            reason=esc.reason,
        )
        self.session.add(escalation)

        # Update conversation status to ESCALATED
        if convo.status != ConversationStatus.ESCALATED:
            convo.status = ConversationStatus.ESCALATED
            self.session.add(convo)

        return True


__all__ = ["AiPayloadConsumer", "ApplyResult"]
