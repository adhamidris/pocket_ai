"""Customer 360 service implementation backed by SQLAlchemy models."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Sequence

from sqlalchemy import (
    and_,
    delete,
    func,
    or_,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models.cases import Case, CasePriority, CaseStatus
from app.models.conversations import Conversation
from app.models.customers import (
    Customer,
    CustomerActivityEvent,
    CustomerActivityEventType,
    CustomerContactMethod,
    CustomerContactMethodType,
    CustomerLifecycleStage,
    CustomerNote,
    CustomerNoteVisibility,
    CustomerTag,
    CustomerTagLink,
)
from app.services.errors import (
    ServiceConflictError,
    ServiceError,
    ServiceNotFoundError,
    ServiceValidationError,
)


# ---------------------------------------------------------------------------
# DTOs returned to the API layer


@dataclass(slots=True, frozen=True)
class CustomerTagDTO:
    id: uuid.UUID
    label: str
    color: str | None = None


@dataclass(slots=True, frozen=True)
class CustomerNoteDTO:
    id: uuid.UUID
    customer_id: uuid.UUID
    author_user_id: uuid.UUID | None
    author_agent_id: uuid.UUID | None
    visibility: CustomerNoteVisibility
    body: str
    pinned: bool
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class CustomerActivityEventDTO:
    id: uuid.UUID
    customer_id: uuid.UUID
    event_type: CustomerActivityEventType
    occurred_at: datetime
    actor_user_id: uuid.UUID | None
    actor_agent_id: uuid.UUID | None
    actor_customer_id: uuid.UUID | None
    case_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    details: dict | None = None


@dataclass(slots=True, frozen=True)
class CustomerCaseLinkDTO:
    id: uuid.UUID
    title: str
    status: CaseStatus
    priority: CasePriority
    opened_at: datetime


@dataclass(slots=True, frozen=True)
class CustomerStatsDTO:
    conversations_total: int = 0
    conversations_last_30_days: int = 0
    csat_average: float | None = None
    csat_trend: float | None = None
    expansion_opportunities: int = 0


@dataclass(slots=True, frozen=True)
class CustomerContactMethodDTO:
    method_type: CustomerContactMethodType
    value: str
    is_primary: bool


@dataclass(slots=True, frozen=True)
class CustomerDetailDTO:
    id: uuid.UUID
    business_id: uuid.UUID
    full_name: str
    primary_email: str | None
    primary_phone: str | None
    country: str | None
    lifecycle_stage: CustomerLifecycleStage
    satisfaction_score: float | None
    persona_tags: tuple[str, ...]
    last_contact_at: datetime | None
    created_at: datetime
    updated_at: datetime
    stats: CustomerStatsDTO = field(default_factory=CustomerStatsDTO)
    contacts: tuple[CustomerContactMethodDTO, ...] = field(default_factory=tuple)
    tags: tuple[CustomerTagDTO, ...] = field(default_factory=tuple)
    cases_open: tuple[CustomerCaseLinkDTO, ...] = field(default_factory=tuple)
    cases_resolved: tuple[CustomerCaseLinkDTO, ...] = field(default_factory=tuple)
    notes: tuple[CustomerNoteDTO, ...] = field(default_factory=tuple)
    activity: tuple[CustomerActivityEventDTO, ...] = field(default_factory=tuple)


@dataclass(slots=True, frozen=True)
class CustomerListItemDTO:
    id: uuid.UUID
    full_name: str
    primary_email: str | None
    conversations_count: int
    satisfaction_score: float | None
    last_contact_at: datetime | None
    lifecycle_stage: CustomerLifecycleStage


# ---------------------------------------------------------------------------
# Service-layer input/output dataclasses


@dataclass(slots=True, frozen=True)
class ListCustomersInput:
    business_id: uuid.UUID
    search: str | None
    lifecycle_stage: CustomerLifecycleStage | None
    tags: Sequence[str] | None
    date_from: date | None
    date_to: date | None
    limit: int
    cursor: str | None


@dataclass(slots=True, frozen=True)
class ListCustomersResult:
    items: tuple[CustomerListItemDTO, ...]
    total: int
    has_next: bool
    next_cursor: str | None


@dataclass(slots=True, frozen=True)
class CreateCustomerInput:
    business_id: uuid.UUID
    full_name: str
    primary_email: str | None
    primary_phone: str | None
    country: str | None
    lifecycle_stage: CustomerLifecycleStage
    satisfaction_score: float | None
    persona_tags: Sequence[str]
    contact_methods: Sequence[CustomerContactMethodDTO]
    tag_ids: Sequence[uuid.UUID]


@dataclass(slots=True, frozen=True)
class UpdateCustomerInput:
    business_id: uuid.UUID
    customer_id: uuid.UUID
    full_name: str | None
    primary_email: str | None
    primary_phone: str | None
    country: str | None
    lifecycle_stage: CustomerLifecycleStage | None
    satisfaction_score: float | None
    persona_tags: Sequence[str] | None
    contact_methods: Sequence[CustomerContactMethodDTO] | None
    tag_ids: Sequence[uuid.UUID] | None


@dataclass(slots=True, frozen=True)
class GetCustomerDetailInput:
    business_id: uuid.UUID
    customer_id: uuid.UUID
    include_notes: bool = False
    include_activity: bool = False
    include_cases: bool = False


@dataclass(slots=True, frozen=True)
class CreateCustomerNoteInput:
    business_id: uuid.UUID
    customer_id: uuid.UUID
    author_user_id: uuid.UUID | None
    author_agent_id: uuid.UUID | None
    visibility: CustomerNoteVisibility
    body: str
    pinned: bool


@dataclass(slots=True, frozen=True)
class UpdateCustomerNoteInput:
    business_id: uuid.UUID
    customer_id: uuid.UUID
    note_id: uuid.UUID
    body: str | None
    visibility: CustomerNoteVisibility | None
    pinned: bool | None


@dataclass(slots=True, frozen=True)
class ListCustomerActivityInput:
    business_id: uuid.UUID
    customer_id: uuid.UUID
    limit: int
    cursor: str | None


@dataclass(slots=True, frozen=True)
class ListCustomerNotesInput:
    business_id: uuid.UUID
    customer_id: uuid.UUID
    limit: int
    cursor: str | None


@dataclass(slots=True, frozen=True)
class ListCustomerActivityResult:
    items: tuple[CustomerActivityEventDTO, ...]
    total: int
    has_next: bool
    next_cursor: str | None


@dataclass(slots=True, frozen=True)
class ListCustomerNotesResult:
    items: tuple[CustomerNoteDTO, ...]
    total: int
    has_next: bool
    next_cursor: str | None


@dataclass(slots=True, frozen=True)
class CustomerImportRowInput:
    full_name: str
    email: str | None
    phone: str | None
    country: str | None
    lifecycle_stage: CustomerLifecycleStage | None
    tags: Sequence[str]


@dataclass(slots=True, frozen=True)
class CustomerImportInput:
    business_id: uuid.UUID
    rows: Sequence[CustomerImportRowInput]
    skip_duplicates: bool


@dataclass(slots=True, frozen=True)
class CustomerImportOutput:
    imported_count: int
    skipped_count: int
    errors: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class ListCustomerTagsResult:
    items: tuple[CustomerTagDTO, ...]


@dataclass(slots=True, frozen=True)
class CreateCustomerTagInput:
    business_id: uuid.UUID
    label: str
    color: str | None


@dataclass(slots=True, frozen=True)
class UpdateCustomerTagsInput:
    business_id: uuid.UUID
    customer_id: uuid.UUID
    tag_ids: Sequence[uuid.UUID]


@dataclass(slots=True, frozen=True)
class UpdateCustomerTagsResult:
    customer_id: uuid.UUID
    tags: tuple[CustomerTagDTO, ...]


# ---------------------------------------------------------------------------
# Service implementation


class CustomersService:
    """Coordinates SQLAlchemy operations for Customer 360 endpoints."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # -------------------- listing & pagination ---------------------------

    def list_customers(self, input_data: ListCustomersInput) -> ListCustomersResult:
        base_stmt = self._build_list_query(input_data)
        stmt = base_stmt.limit(input_data.limit + 1)
        rows = self.session.execute(stmt).all()

        has_next = len(rows) > input_data.limit
        if has_next:
            rows = rows[: input_data.limit]

        items = tuple(
            CustomerListItemDTO(
                id=customer.id,
                full_name=customer.full_name,
                primary_email=customer.primary_email,
                conversations_count=conversations_count,
                satisfaction_score=customer.satisfaction_score,
                last_contact_at=customer.last_contact_at,
                lifecycle_stage=customer.lifecycle_stage,
            )
            for customer, conversations_count in rows
        )

        next_cursor = None
        if has_next and rows:
            last_customer = rows[-1][0]
            next_cursor = self._encode_cursor(last_customer.created_at, last_customer.id)

        total = self._count_customers(input_data)
        return ListCustomersResult(items=items, total=total, has_next=has_next, next_cursor=next_cursor)

    # ------------------------ CRUD operations ---------------------------

    def create_customer(self, input_data: CreateCustomerInput) -> CustomerDetailDTO:
        self._validate_tag_ids(input_data.business_id, input_data.tag_ids)
        self._ensure_email_unique(input_data.business_id, input_data.primary_email)

        customer = Customer(
            business_id=input_data.business_id,
            full_name=input_data.full_name,
            primary_email=input_data.primary_email,
            primary_phone=input_data.primary_phone,
            country=input_data.country,
            lifecycle_stage=input_data.lifecycle_stage,
            satisfaction_score=input_data.satisfaction_score,
            persona_tags=list(input_data.persona_tags),
        )
        self.session.add(customer)
        self.session.flush()

        self._replace_contact_methods(customer.id, input_data.contact_methods)
        self._replace_customer_tags(customer.business_id, customer.id, input_data.tag_ids)

        self.session.flush()
        return self._build_customer_detail(customer.id, customer.business_id)

    def update_customer(self, input_data: UpdateCustomerInput) -> CustomerDetailDTO:
        customer = self._get_customer_model(input_data.customer_id, input_data.business_id)

        if input_data.full_name is not None:
            customer.full_name = input_data.full_name
        if input_data.primary_email is not None:
            if input_data.primary_email != customer.primary_email:
                self._ensure_email_unique(input_data.business_id, input_data.primary_email, exclude_id=customer.id)
            customer.primary_email = input_data.primary_email
        if input_data.primary_phone is not None:
            customer.primary_phone = input_data.primary_phone
        if input_data.country is not None:
            customer.country = input_data.country
        if input_data.lifecycle_stage is not None:
            customer.lifecycle_stage = input_data.lifecycle_stage
        if input_data.satisfaction_score is not None:
            customer.satisfaction_score = input_data.satisfaction_score
        if input_data.persona_tags is not None:
            customer.persona_tags = list(input_data.persona_tags)

        if input_data.contact_methods is not None:
            self._replace_contact_methods(customer.id, input_data.contact_methods)

        if input_data.tag_ids is not None:
            self._validate_tag_ids(input_data.business_id, input_data.tag_ids)
            self._replace_customer_tags(customer.business_id, customer.id, input_data.tag_ids)

        self.session.flush()
        return self._build_customer_detail(customer.id, customer.business_id)

    def get_customer_detail(self, input_data: GetCustomerDetailInput) -> CustomerDetailDTO:
        return self._build_customer_detail(
            input_data.customer_id,
            input_data.business_id,
            include_notes=input_data.include_notes,
            include_activity=input_data.include_activity,
            include_cases=input_data.include_cases,
        )

    # ----------------------------- Notes ---------------------------------

    def create_note(self, input_data: CreateCustomerNoteInput) -> CustomerNoteDTO:
        customer = self._get_customer_model(input_data.customer_id, input_data.business_id)

        note = CustomerNote(
            customer_id=customer.id,
            author_user_id=input_data.author_user_id,
            author_agent_id=input_data.author_agent_id,
            visibility=input_data.visibility,
            body=input_data.body,
            pinned=input_data.pinned,
        )
        self.session.add(note)
        self.session.flush()
        return self._to_note_dto(note)

    def update_note(self, input_data: UpdateCustomerNoteInput) -> CustomerNoteDTO:
        note = self._get_note_model(
            note_id=input_data.note_id,
            customer_id=input_data.customer_id,
            business_id=input_data.business_id,
        )
        if input_data.body is not None:
            note.body = input_data.body
        if input_data.visibility is not None:
            note.visibility = input_data.visibility
        if input_data.pinned is not None:
            note.pinned = input_data.pinned
        self.session.flush()
        return self._to_note_dto(note)

    def list_notes(self, input_data: ListCustomerNotesInput) -> ListCustomerNotesResult:
        stmt = (
            select(CustomerNote)
            .join(Customer, Customer.id == CustomerNote.customer_id)
            .where(Customer.business_id == input_data.business_id, CustomerNote.customer_id == input_data.customer_id)
            .order_by(CustomerNote.created_at.desc(), CustomerNote.id.desc())
        )
        stmt = self._apply_cursor(stmt, input_data.cursor, CustomerNote.created_at, CustomerNote.id)
        rows = self.session.execute(stmt.limit(input_data.limit + 1)).scalars().all()

        has_next = len(rows) > input_data.limit
        if has_next:
            rows = rows[: input_data.limit]

        items = tuple(self._to_note_dto(note) for note in rows)
        next_cursor = None
        if has_next and rows:
            last = rows[-1]
            next_cursor = self._encode_cursor(last.created_at, last.id)

        total_stmt = select(func.count()).select_from(CustomerNote).join(Customer).where(
            Customer.business_id == input_data.business_id,
            CustomerNote.customer_id == input_data.customer_id,
        )
        total = self.session.execute(total_stmt).scalar_one()
        return ListCustomerNotesResult(items=items, total=total, has_next=has_next, next_cursor=next_cursor)

    # ---------------------------- Activity -------------------------------

    def list_activity(self, input_data: ListCustomerActivityInput) -> ListCustomerActivityResult:
        stmt = (
            select(CustomerActivityEvent)
            .join(Customer, Customer.id == CustomerActivityEvent.customer_id)
            .where(
                Customer.business_id == input_data.business_id,
                CustomerActivityEvent.customer_id == input_data.customer_id,
            )
            .order_by(CustomerActivityEvent.occurred_at.desc(), CustomerActivityEvent.id.desc())
        )
        stmt = self._apply_cursor(
            stmt,
            input_data.cursor,
            CustomerActivityEvent.occurred_at,
            CustomerActivityEvent.id,
        )
        rows = self.session.execute(stmt.limit(input_data.limit + 1)).scalars().all()

        has_next = len(rows) > input_data.limit
        if has_next:
            rows = rows[: input_data.limit]

        items = tuple(self._to_activity_dto(event) for event in rows)
        next_cursor = None
        if has_next and rows:
            last = rows[-1]
            next_cursor = self._encode_cursor(last.occurred_at, last.id)

        total_stmt = select(func.count()).select_from(CustomerActivityEvent).join(Customer).where(
            Customer.business_id == input_data.business_id,
            CustomerActivityEvent.customer_id == input_data.customer_id,
        )
        total = self.session.execute(total_stmt).scalar_one()
        return ListCustomerActivityResult(items=items, total=total, has_next=has_next, next_cursor=next_cursor)

    # ------------------------------ Tags ---------------------------------

    def list_tags(self, business_id: uuid.UUID) -> ListCustomerTagsResult:
        stmt = select(CustomerTag).where(CustomerTag.business_id == business_id).order_by(CustomerTag.label.asc())
        tags = tuple(
            CustomerTagDTO(id=tag.id, label=tag.label, color=tag.color)
            for tag in self.session.execute(stmt).scalars().all()
        )
        return ListCustomerTagsResult(items=tags)

    def create_tag(self, input_data: CreateCustomerTagInput) -> CustomerTagDTO:
        tag = CustomerTag(business_id=input_data.business_id, label=input_data.label, color=input_data.color)
        self.session.add(tag)
        try:
            self.session.flush()
        except IntegrityError as exc:  # pragma: no cover - database uniqueness enforcement
            raise ServiceConflictError("A tag with that label already exists") from exc
        return CustomerTagDTO(id=tag.id, label=tag.label, color=tag.color)

    def update_customer_tags(self, input_data: UpdateCustomerTagsInput) -> UpdateCustomerTagsResult:
        self._validate_tag_ids(input_data.business_id, input_data.tag_ids)
        self._replace_customer_tags(input_data.business_id, input_data.customer_id, input_data.tag_ids)
        self.session.flush()
        tags_stmt = (
            select(CustomerTag)
            .join(CustomerTagLink, CustomerTagLink.tag_id == CustomerTag.id)
            .where(
                CustomerTagLink.customer_id == input_data.customer_id,
                CustomerTag.business_id == input_data.business_id,
            )
        )
        tags = tuple(
            CustomerTagDTO(id=tag.id, label=tag.label, color=tag.color)
            for tag in self.session.execute(tags_stmt).scalars().all()
        )
        return UpdateCustomerTagsResult(customer_id=input_data.customer_id, tags=tags)

    # ----------------------------- Import --------------------------------

    def import_customers(self, input_data: CustomerImportInput) -> CustomerImportOutput:
        imported = 0
        skipped = 0
        errors: list[str] = []
        for idx, row in enumerate(input_data.rows, start=1):
            try:
                if self._is_duplicate_customer(input_data.business_id, row):
                    if input_data.skip_duplicates:
                        skipped += 1
                        continue
                created = self._create_customer_from_import(input_data.business_id, row)
                if created:
                    imported += 1
            except ServiceError as exc:
                errors.append(f"Row {idx}: {exc.message}")
            except IntegrityError as exc:  # pragma: no cover - defensive guard
                errors.append(f"Row {idx}: database error {exc}")
        self.session.flush()
        return CustomerImportOutput(imported_count=imported, skipped_count=skipped, errors=tuple(errors))

    # ------------------------------------------------------------------
    # Internal helpers

    def _build_list_query(self, input_data: ListCustomersInput):
        conversations_subq = (
            select(
                Conversation.customer_id.label("customer_id"),
                func.count(Conversation.id).label("conversations_count"),
            )
            .where(
                Conversation.business_id == input_data.business_id,
                Conversation.customer_id.is_not(None),
            )
            .group_by(Conversation.customer_id)
            .subquery()
        )

        stmt = (
            select(Customer, func.coalesce(conversations_subq.c.conversations_count, 0).label("conversations_count"))
            .outerjoin(conversations_subq, conversations_subq.c.customer_id == Customer.id)
            .where(Customer.business_id == input_data.business_id)
        )

        if input_data.search:
            term = f"%{input_data.search.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(Customer.full_name).like(term),
                    func.lower(Customer.primary_email).like(term),
                    func.lower(Customer.primary_phone).like(term),
                )
            )

        if input_data.lifecycle_stage is not None:
            stmt = stmt.where(Customer.lifecycle_stage == input_data.lifecycle_stage)

        if input_data.date_from is not None:
            stmt = stmt.where(Customer.created_at >= datetime.combine(input_data.date_from, datetime.min.time(), tzinfo=timezone.utc))

        if input_data.date_to is not None:
            end_dt = datetime.combine(input_data.date_to, datetime.max.time(), tzinfo=timezone.utc)
            stmt = stmt.where(Customer.created_at <= end_dt)

        if input_data.tags:
            for label in input_data.tags:
                exists_clause = (
                    select(CustomerTagLink.customer_id)
                    .join(CustomerTag, CustomerTag.id == CustomerTagLink.tag_id)
                    .where(
                        CustomerTagLink.customer_id == Customer.id,
                        CustomerTag.business_id == input_data.business_id,
                        func.lower(CustomerTag.label) == label.lower(),
                    )
                    .limit(1)
                )
                stmt = stmt.where(exists_clause.exists())

        stmt = self._apply_cursor(stmt, input_data.cursor, Customer.created_at, Customer.id)
        stmt = stmt.order_by(Customer.created_at.desc(), Customer.id.desc())
        return stmt

    def _count_customers(self, input_data: ListCustomersInput) -> int:
        base_input = replace(input_data, cursor=None, limit=input_data.limit)
        count_stmt = self._build_list_query(base_input).with_only_columns(Customer.id).order_by(None)
        return self.session.execute(select(func.count()).select_from(count_stmt.subquery())).scalar_one()

    def _apply_cursor(self, stmt, cursor: str | None, *order_columns):
        if not cursor:
            return stmt
        created_at, entity_id = self._decode_cursor(cursor)
        comparison = or_(
            order_columns[0] < created_at,
            and_(order_columns[0] == created_at, order_columns[1] < entity_id),
        )
        return stmt.where(comparison)

    def _encode_cursor(self, created_at: datetime, entity_id: uuid.UUID) -> str:
        return f"{created_at.isoformat()}|{entity_id}"

    def _decode_cursor(self, cursor: str) -> tuple[datetime, uuid.UUID]:
        try:
            created_str, id_str = cursor.split("|", 1)
            created_at = datetime.fromisoformat(created_str)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            entity_id = uuid.UUID(id_str)
            return created_at, entity_id
        except (ValueError, TypeError) as exc:  # pragma: no cover - defensive guard
            raise ServiceValidationError("Invalid pagination cursor") from exc

    def _ensure_email_unique(
        self,
        business_id: uuid.UUID,
        email: str | None,
        *,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        if not email:
            return
        stmt = select(Customer.id).where(
            Customer.business_id == business_id,
            func.lower(Customer.primary_email) == email.lower(),
        )
        if exclude_id is not None:
            stmt = stmt.where(Customer.id != exclude_id)
        exists_email = self.session.execute(stmt.limit(1)).scalar_one_or_none()
        if exists_email:
            raise ServiceConflictError("A customer with this email already exists")

    def _validate_tag_ids(self, business_id: uuid.UUID, tag_ids: Iterable[uuid.UUID]) -> None:
        tag_ids = list({tag_id for tag_id in tag_ids})
        if not tag_ids:
            return
        stmt = select(CustomerTag.id).where(
            CustomerTag.business_id == business_id,
            CustomerTag.id.in_(tag_ids),
        )
        existing = {tag.id for tag in self.session.execute(stmt).scalars().all()}
        missing = set(tag_ids) - existing
        if missing:
            raise ServiceValidationError("One or more tags do not exist", details={"tag_ids": [str(t) for t in missing]})

    def _replace_contact_methods(
        self,
        customer_id: uuid.UUID,
        contact_methods: Sequence[CustomerContactMethodDTO],
    ) -> None:
        self.session.execute(delete(CustomerContactMethod).where(CustomerContactMethod.customer_id == customer_id))
        for method in contact_methods:
            if not method.value:
                continue
            self.session.add(
                CustomerContactMethod(
                    customer_id=customer_id,
                    method_type=method.method_type,
                    value=method.value,
                    is_primary=method.is_primary,
                )
            )

    def _replace_customer_tags(
        self,
        business_id: uuid.UUID,
        customer_id: uuid.UUID,
        tag_ids: Sequence[uuid.UUID],
    ) -> None:
        self.session.execute(delete(CustomerTagLink).where(CustomerTagLink.customer_id == customer_id))
        if not tag_ids:
            return
        links = [CustomerTagLink(customer_id=customer_id, tag_id=tag_id) for tag_id in tag_ids]
        self.session.add_all(links)

    def _get_customer_model(self, customer_id: uuid.UUID, business_id: uuid.UUID) -> Customer:
        stmt = select(Customer).where(Customer.id == customer_id, Customer.business_id == business_id)
        customer = self.session.execute(stmt).scalar_one_or_none()
        if customer is None:
            raise ServiceNotFoundError("Customer not found")
        return customer

    def _get_note_model(
        self,
        note_id: uuid.UUID,
        customer_id: uuid.UUID,
        business_id: uuid.UUID,
    ) -> CustomerNote:
        stmt = (
            select(CustomerNote)
            .join(Customer, Customer.id == CustomerNote.customer_id)
            .where(
                CustomerNote.id == note_id,
                CustomerNote.customer_id == customer_id,
                Customer.business_id == business_id,
            )
        )
        note = self.session.execute(stmt).scalar_one_or_none()
        if note is None:
            raise ServiceNotFoundError("Note not found")
        return note

    def _build_customer_detail(
        self,
        customer_id: uuid.UUID,
        business_id: uuid.UUID,
        *,
        include_notes: bool = False,
        include_activity: bool = False,
        include_cases: bool = False,
    ) -> CustomerDetailDTO:
        stmt = (
            select(Customer)
            .options(
                selectinload(Customer.contact_methods),
                selectinload(Customer.tags).selectinload(CustomerTagLink.tag),
            )
            .where(Customer.id == customer_id, Customer.business_id == business_id)
        )
        customer = self.session.execute(stmt).scalar_one_or_none()
        if customer is None:
            raise ServiceNotFoundError("Customer not found")

        contacts = tuple(
            CustomerContactMethodDTO(
                method_type=method.method_type,
                value=method.value,
                is_primary=method.is_primary,
            )
            for method in customer.contact_methods
        )

        tags = tuple(
            CustomerTagDTO(id=link.tag.id, label=link.tag.label, color=link.tag.color)
            for link in customer.tags
            if link.tag is not None
        )

        cases_open: tuple[CustomerCaseLinkDTO, ...] = tuple()
        cases_resolved: tuple[CustomerCaseLinkDTO, ...] = tuple()
        if include_cases:
            cases_open = self._fetch_case_links(customer_id, business_id, {CaseStatus.OPEN, CaseStatus.PENDING_CUSTOMER, CaseStatus.ESCALATED})
            cases_resolved = self._fetch_case_links(customer_id, business_id, {CaseStatus.RESOLVED})

        notes: tuple[CustomerNoteDTO, ...] = tuple()
        if include_notes:
            notes_stmt = (
                select(CustomerNote)
                .join(Customer, Customer.id == CustomerNote.customer_id)
                .where(Customer.business_id == business_id, CustomerNote.customer_id == customer_id)
                .order_by(CustomerNote.created_at.desc())
                .limit(50)
            )
            notes = tuple(self._to_note_dto(note) for note in self.session.execute(notes_stmt).scalars().all())

        activity: tuple[CustomerActivityEventDTO, ...] = tuple()
        if include_activity:
            activity_stmt = (
                select(CustomerActivityEvent)
                .join(Customer, Customer.id == CustomerActivityEvent.customer_id)
                .where(Customer.business_id == business_id, CustomerActivityEvent.customer_id == customer_id)
                .order_by(CustomerActivityEvent.occurred_at.desc())
                .limit(100)
            )
            activity = tuple(
                self._to_activity_dto(event)
                for event in self.session.execute(activity_stmt).scalars().all()
            )

        stats = self._compute_stats(customer_id, business_id)

        return CustomerDetailDTO(
            id=customer.id,
            business_id=customer.business_id,
            full_name=customer.full_name,
            primary_email=customer.primary_email,
            primary_phone=customer.primary_phone,
            country=customer.country,
            lifecycle_stage=customer.lifecycle_stage,
            satisfaction_score=customer.satisfaction_score,
            persona_tags=tuple(customer.persona_tags or []),
            last_contact_at=customer.last_contact_at,
            created_at=customer.created_at,
            updated_at=customer.updated_at,
            stats=stats,
            contacts=contacts,
            tags=tags,
            cases_open=cases_open,
            cases_resolved=cases_resolved,
            notes=notes,
            activity=activity,
        )

    def _compute_stats(self, customer_id: uuid.UUID, business_id: uuid.UUID) -> CustomerStatsDTO:
        now = datetime.now(timezone.utc)
        last_30 = now - timedelta(days=30)
        prev_30_start = last_30 - timedelta(days=30)

        base_filter = [
            Conversation.business_id == business_id,
            Conversation.customer_id == customer_id,
        ]

        total_stmt = select(func.count()).where(*base_filter)
        total = self.session.execute(total_stmt).scalar_one()

        recent_stmt = select(func.count()).where(*base_filter, Conversation.created_at >= last_30)
        recent = self.session.execute(recent_stmt).scalar_one()

        csat_avg_stmt = select(func.avg(Conversation.csat_score)).where(*base_filter, Conversation.csat_score.is_not(None))
        csat_average = self.session.execute(csat_avg_stmt).scalar_one()
        if csat_average is not None:
            csat_average = float(csat_average)

        csat_recent_stmt = select(func.avg(Conversation.csat_score)).where(
            *base_filter,
            Conversation.csat_score.is_not(None),
            Conversation.created_at >= last_30,
        )
        csat_recent = self.session.execute(csat_recent_stmt).scalar_one()
        csat_prev_stmt = select(func.avg(Conversation.csat_score)).where(
            *base_filter,
            Conversation.csat_score.is_not(None),
            Conversation.created_at >= prev_30_start,
            Conversation.created_at < last_30,
        )
        csat_previous = self.session.execute(csat_prev_stmt).scalar_one()

        csat_trend = None
        if csat_recent is not None and csat_previous is not None:
            csat_trend = float(csat_recent) - float(csat_previous)

        return CustomerStatsDTO(
            conversations_total=int(total),
            conversations_last_30_days=int(recent),
            csat_average=csat_average,
            csat_trend=csat_trend,
            expansion_opportunities=0,
        )

    def _fetch_case_links(
        self,
        customer_id: uuid.UUID,
        business_id: uuid.UUID,
        statuses: set[CaseStatus],
    ) -> tuple[CustomerCaseLinkDTO, ...]:
        stmt = (
            select(Case)
            .where(
                Case.customer_id == customer_id,
                Case.business_id == business_id,
                Case.status.in_(statuses),
            )
            .order_by(Case.opened_at.desc())
            .limit(50)
        )
        cases = self.session.execute(stmt).scalars().all()
        return tuple(
            CustomerCaseLinkDTO(
                id=case.id,
                title=case.title,
                status=case.status,
                priority=case.priority,
                opened_at=case.opened_at,
            )
            for case in cases
        )

    def _to_note_dto(self, note: CustomerNote) -> CustomerNoteDTO:
        return CustomerNoteDTO(
            id=note.id,
            customer_id=note.customer_id,
            author_user_id=note.author_user_id,
            author_agent_id=note.author_agent_id,
            visibility=note.visibility,
            body=note.body,
            pinned=note.pinned,
            created_at=note.created_at,
            updated_at=note.updated_at,
        )

    def _to_activity_dto(self, event: CustomerActivityEvent) -> CustomerActivityEventDTO:
        return CustomerActivityEventDTO(
            id=event.id,
            customer_id=event.customer_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            actor_user_id=event.actor_user_id,
            actor_agent_id=event.actor_agent_id,
            actor_customer_id=event.actor_customer_id,
            case_id=event.case_id,
            conversation_id=event.conversation_id,
            details=event.details,
        )

    def _is_duplicate_customer(self, business_id: uuid.UUID, row: CustomerImportRowInput) -> bool:
        filters = [Customer.business_id == business_id]
        if row.email:
            filters.append(func.lower(Customer.primary_email) == row.email.lower())
        elif row.phone:
            filters.append(Customer.primary_phone == row.phone)
        else:
            filters.append(func.lower(Customer.full_name) == row.full_name.lower())
        stmt = select(Customer.id).where(*filters).limit(1)
        return self.session.execute(stmt).scalar_one_or_none() is not None

    def _create_customer_from_import(
        self,
        business_id: uuid.UUID,
        row: CustomerImportRowInput,
    ) -> bool:
        lifecycle = row.lifecycle_stage or CustomerLifecycleStage.LEAD
        customer = Customer(
            business_id=business_id,
            full_name=row.full_name,
            primary_email=row.email,
            primary_phone=row.phone,
            country=row.country,
            lifecycle_stage=lifecycle,
            persona_tags=[],
        )
        self.session.add(customer)
        self.session.flush()

        if row.tags:
            tag_ids = self._ensure_tags(business_id, row.tags)
            self._replace_customer_tags(business_id, customer.id, tag_ids)

        return True

    def _ensure_tags(self, business_id: uuid.UUID, labels: Sequence[str]) -> list[uuid.UUID]:
        cleaned = [label.strip() for label in labels if label.strip()]
        if not cleaned:
            return []
        existing_stmt = select(CustomerTag).where(
            CustomerTag.business_id == business_id,
            func.lower(CustomerTag.label).in_([label.lower() for label in cleaned]),
        )
        existing = {tag.label.lower(): tag for tag in self.session.execute(existing_stmt).scalars().all()}
        tag_ids: list[uuid.UUID] = []
        for label in cleaned:
            tag = existing.get(label.lower())
            if tag is None:
                tag = CustomerTag(business_id=business_id, label=label)
                self.session.add(tag)
                self.session.flush()
                existing[label.lower()] = tag
            tag_ids.append(tag.id)
        return tag_ids


__all__ = [
    "CreateCustomerInput",
    "CreateCustomerNoteInput",
    "CreateCustomerTagInput",
    "CustomerActivityEventDTO",
    "CustomerCaseLinkDTO",
    "CustomerContactMethodDTO",
    "CustomerDetailDTO",
    "CustomerImportInput",
    "CustomerImportOutput",
    "CustomerImportRowInput",
    "CustomerListItemDTO",
    "CustomerNoteDTO",
    "CustomerStatsDTO",
    "CustomerTagDTO",
    "CustomersService",
    "GetCustomerDetailInput",
    "ListCustomerActivityInput",
    "ListCustomerActivityResult",
    "ListCustomerNotesInput",
    "ListCustomerNotesResult",
    "ListCustomerTagsResult",
    "ListCustomersInput",
    "ListCustomersResult",
    "UpdateCustomerInput",
    "UpdateCustomerNoteInput",
    "UpdateCustomerTagsInput",
    "UpdateCustomerTagsResult",
]
