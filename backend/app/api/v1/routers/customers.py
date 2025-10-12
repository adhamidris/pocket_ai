"""Customer 360 API endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.responses import JSONResponse

from app.api.v1.deps import (
    get_current_user,
    get_customers_service,
    require_business_id,
)
from app.api.v1.schemas.customers import (
    CustomerActivityEvent,
    CustomerActivityListResponse,
    CustomerCaseLink,
    CustomerContactMethod,
    CustomerCreateRequest,
    CustomerDetail,
    CustomerDetailResponse,
    CustomerImportRequest,
    CustomerImportResult,
    CustomerListItem,
    CustomerNote,
    CustomerNoteCreateRequest,
    CustomerNoteListResponse,
    CustomerNoteUpdateRequest,
    CustomerStats,
    CustomerTag,
    CustomerTagAssignmentRequest,
    CustomerTagCreateRequest,
    CustomerTagsResponse,
    CustomerUpdateRequest,
    CustomersListQuery,
    CustomersListResponse,
)
from app.core.security import AuthenticatedUser
from app.schemas.registration import ApiErrorResponse
from app.models.customers import (
    CustomerLifecycleStage,
    CustomerNoteVisibility,
    CustomerContactMethodType,
)
from app.services import (
    CreateCustomerInput,
    CreateCustomerNoteInput,
    CreateCustomerTagInput,
    CustomerActivityEventDTO,
    CustomerCaseLinkDTO,
    CustomerContactMethodDTO,
    CustomerDetailDTO,
    CustomerImportInput,
    CustomerImportOutput,
    CustomerImportRowInput,
    CustomerListItemDTO,
    CustomerNoteDTO,
    CustomerStatsDTO,
    CustomerTagDTO,
    CustomersService,
    GetCustomerDetailInput,
    ListCustomerActivityInput,
    ListCustomerActivityResult,
    ListCustomerNotesInput,
    ListCustomerNotesResult,
    ListCustomerTagsResult,
    ListCustomersInput,
    ListCustomersResult,
    ServiceError,
    UpdateCustomerInput,
    UpdateCustomerNoteInput,
    UpdateCustomerTagsInput,
    UpdateCustomerTagsResult,
)

router = APIRouter(
    prefix="/customers",
    tags=["customers"],
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ApiErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ApiErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ApiErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ApiErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ApiErrorResponse},
    },
)


_ERROR_STATUS_MAP: dict[str, int] = {
    "validation": status.HTTP_400_BAD_REQUEST,
    "conflict": status.HTTP_409_CONFLICT,
    "not_found": status.HTTP_404_NOT_FOUND,
    "forbidden": status.HTTP_403_FORBIDDEN,
    "db_timeout": status.HTTP_504_GATEWAY_TIMEOUT,
}


def _service_error_response(exc: ServiceError) -> JSONResponse:
    status_code = _ERROR_STATUS_MAP.get(exc.code, status.HTTP_500_INTERNAL_SERVER_ERROR)
    return JSONResponse(status_code=status_code, content=exc.to_payload())


@router.get("", response_model=CustomersListResponse, summary="List customers")
async def list_customers_endpoint(
    query: CustomersListQuery = Depends(),
    business_id: UUID = Depends(require_business_id),
    current_user: AuthenticatedUser = Depends(get_current_user),
    service: CustomersService = Depends(get_customers_service),
) -> CustomersListResponse:
    lifecycle = (
        CustomerLifecycleStage(query.lifecycle_stage)
        if query.lifecycle_stage is not None
        else None
    )
    input_data = ListCustomersInput(
        business_id=business_id,
        search=query.search.strip() if query.search else None,
        lifecycle_stage=lifecycle,
        tags=query.tags,
        date_from=query.date_from,
        date_to=query.date_to,
        limit=query.limit,
        cursor=query.cursor,
    )
    try:
        result = service.list_customers(input_data)
    except ServiceError as exc:  # pragma: no cover - forward service errors
        return _service_error_response(exc)
    return CustomersListResponse(
        items=[_map_customer_list_item(item) for item in result.items],
        total=result.total,
        has_next=result.has_next,
        next_cursor=result.next_cursor,
    )


@router.post(
    "",
    response_model=CustomerDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a customer",
)
async def create_customer_endpoint(
    payload: CustomerCreateRequest,
    business_id: UUID = Depends(require_business_id),
    current_user: AuthenticatedUser = Depends(get_current_user),
    service: CustomersService = Depends(get_customers_service),
) -> CustomerDetailResponse:
    contact_methods = [
        CustomerContactMethodDTO(
            method_type=CustomerContactMethodType(method.type),
            value=method.value.strip(),
            is_primary=method.is_primary,
        )
        for method in payload.contact_methods
    ]
    persona_tags = [tag.strip() for tag in payload.persona_tags if tag.strip()]
    input_data = CreateCustomerInput(
        business_id=business_id,
        full_name=payload.full_name.strip(),
        primary_email=payload.primary_email,
        primary_phone=payload.primary_phone,
        country=payload.country,
        lifecycle_stage=CustomerLifecycleStage(payload.lifecycle_stage),
        satisfaction_score=payload.satisfaction_score,
        persona_tags=persona_tags,
        contact_methods=contact_methods,
        tag_ids=payload.tag_ids,
    )
    try:
        detail = service.create_customer(input_data)
    except ServiceError as exc:  # pragma: no cover
        return _service_error_response(exc)
    return CustomerDetailResponse(customer=_map_customer_detail(detail))


@router.get(
    "/{customer_id}",
    response_model=CustomerDetailResponse,
    summary="Get customer detail",
)
async def get_customer_detail_endpoint(
    customer_id: UUID = Path(..., description="Customer identifier"),
    include: list[str] = Query(default=[]),
    business_id: UUID = Depends(require_business_id),
    current_user: AuthenticatedUser = Depends(get_current_user),
    service: CustomersService = Depends(get_customers_service),
) -> CustomerDetailResponse:
    include_notes = "notes" in include
    include_activity = "activity" in include
    include_cases = "cases" in include
    input_data = GetCustomerDetailInput(
        business_id=business_id,
        customer_id=customer_id,
        include_notes=include_notes,
        include_activity=include_activity,
        include_cases=include_cases,
    )
    try:
        detail = service.get_customer_detail(input_data)
    except ServiceError as exc:  # pragma: no cover
        return _service_error_response(exc)
    return CustomerDetailResponse(customer=_map_customer_detail(detail))


@router.patch(
    "/{customer_id}",
    response_model=CustomerDetailResponse,
    summary="Update customer",
)
async def update_customer_endpoint(
    customer_id: UUID,
    payload: CustomerUpdateRequest,
    business_id: UUID = Depends(require_business_id),
    current_user: AuthenticatedUser = Depends(get_current_user),
    service: CustomersService = Depends(get_customers_service),
) -> CustomerDetailResponse:
    contact_methods = (
        [
            CustomerContactMethodDTO(
                method_type=CustomerContactMethodType(method.type),
                value=method.value.strip(),
                is_primary=method.is_primary,
            )
            for method in payload.contact_methods or []
        ]
        if payload.contact_methods is not None
        else None
    )
    persona_tags = None
    if payload.persona_tags is not None:
        persona_tags = [tag.strip() for tag in payload.persona_tags if tag.strip()]
    lifecycle = (
        CustomerLifecycleStage(payload.lifecycle_stage)
        if payload.lifecycle_stage is not None
        else None
    )
    input_data = UpdateCustomerInput(
        business_id=business_id,
        customer_id=customer_id,
        full_name=payload.full_name.strip() if payload.full_name else None,
        primary_email=payload.primary_email,
        primary_phone=payload.primary_phone,
        country=payload.country,
        lifecycle_stage=lifecycle,
        satisfaction_score=payload.satisfaction_score,
        persona_tags=persona_tags,
        contact_methods=contact_methods,
        tag_ids=payload.tag_ids,
    )
    try:
        detail = service.update_customer(input_data)
    except ServiceError as exc:  # pragma: no cover
        return _service_error_response(exc)
    return CustomerDetailResponse(customer=_map_customer_detail(detail))


@router.get(
    "/{customer_id}/activity",
    response_model=CustomerActivityListResponse,
    summary="List customer activity",
)
async def list_customer_activity_endpoint(
    customer_id: UUID,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=120),
    business_id: UUID = Depends(require_business_id),
    current_user: AuthenticatedUser = Depends(get_current_user),
    service: CustomersService = Depends(get_customers_service),
) -> CustomerActivityListResponse:
    input_data = ListCustomerActivityInput(
        business_id=business_id,
        customer_id=customer_id,
        limit=limit,
        cursor=cursor,
    )
    try:
        result = service.list_activity(input_data)
    except ServiceError as exc:  # pragma: no cover
        return _service_error_response(exc)
    return CustomerActivityListResponse(
        items=[_map_activity_event(item) for item in result.items],
        total=result.total,
        has_next=result.has_next,
        next_cursor=result.next_cursor,
    )


@router.get(
    "/{customer_id}/notes",
    response_model=CustomerNoteListResponse,
    summary="List customer notes",
)
async def list_customer_notes_endpoint(
    customer_id: UUID,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=120),
    business_id: UUID = Depends(require_business_id),
    current_user: AuthenticatedUser = Depends(get_current_user),
    service: CustomersService = Depends(get_customers_service),
) -> CustomerNoteListResponse:
    input_data = ListCustomerNotesInput(
        business_id=business_id,
        customer_id=customer_id,
        limit=limit,
        cursor=cursor,
    )
    try:
        result = service.list_notes(input_data)
    except ServiceError as exc:  # pragma: no cover
        return _service_error_response(exc)
    return CustomerNoteListResponse(
        items=[_map_note(item) for item in result.items],
        total=result.total,
        has_next=result.has_next,
        next_cursor=result.next_cursor,
    )


@router.post(
    "/{customer_id}/notes",
    response_model=CustomerNote,
    status_code=status.HTTP_201_CREATED,
    summary="Create customer note",
)
async def create_customer_note_endpoint(
    customer_id: UUID,
    payload: CustomerNoteCreateRequest,
    business_id: UUID = Depends(require_business_id),
    current_user: AuthenticatedUser = Depends(get_current_user),
    service: CustomersService = Depends(get_customers_service),
) -> CustomerNote:
    input_data = CreateCustomerNoteInput(
        business_id=business_id,
        customer_id=customer_id,
        author_user_id=current_user.user_id,
        author_agent_id=None,
        visibility=CustomerNoteVisibility(payload.visibility),
        body=payload.body.strip(),
        pinned=payload.pinned,
    )
    try:
        note = service.create_note(input_data)
    except ServiceError as exc:  # pragma: no cover
        return _service_error_response(exc)
    return _map_note(note)


@router.patch(
    "/{customer_id}/notes/{note_id}",
    response_model=CustomerNote,
    summary="Update customer note",
)
async def update_customer_note_endpoint(
    customer_id: UUID,
    note_id: UUID,
    payload: CustomerNoteUpdateRequest,
    business_id: UUID = Depends(require_business_id),
    current_user: AuthenticatedUser = Depends(get_current_user),
    service: CustomersService = Depends(get_customers_service),
) -> CustomerNote:
    visibility = CustomerNoteVisibility(payload.visibility) if payload.visibility else None
    input_data = UpdateCustomerNoteInput(
        business_id=business_id,
        customer_id=customer_id,
        note_id=note_id,
        body=payload.body.strip() if payload.body else None,
        visibility=visibility,
        pinned=payload.pinned,
    )
    try:
        note = service.update_note(input_data)
    except ServiceError as exc:  # pragma: no cover
        return _service_error_response(exc)
    return _map_note(note)


@router.post(
    "/import",
    response_model=CustomerImportResult,
    summary="Bulk import customers",
)
async def import_customers_endpoint(
    payload: CustomerImportRequest,
    business_id: UUID = Depends(require_business_id),
    current_user: AuthenticatedUser = Depends(get_current_user),
    service: CustomersService = Depends(get_customers_service),
) -> CustomerImportResult:
    rows = [
        CustomerImportRowInput(
            full_name=row.full_name.strip(),
            email=row.email,
            phone=row.phone,
            country=row.country,
            lifecycle_stage=(
                CustomerLifecycleStage(row.lifecycle_stage)
                if row.lifecycle_stage
                else None
            ),
            tags=[tag.strip() for tag in row.tags if tag.strip()],
        )
        for row in payload.rows
    ]
    input_data = CustomerImportInput(
        business_id=business_id,
        rows=rows,
        skip_duplicates=payload.skip_duplicates,
    )
    try:
        result = service.import_customers(input_data)
    except ServiceError as exc:  # pragma: no cover
        return _service_error_response(exc)
    return CustomerImportResult(
        imported_count=result.imported_count,
        skipped_count=result.skipped_count,
        errors=list(result.errors),
    )


@router.get(
    "/tags",
    response_model=CustomerTagsResponse,
    summary="List customer tags",
)
async def list_customer_tags_endpoint(
    business_id: UUID = Depends(require_business_id),
    current_user: AuthenticatedUser = Depends(get_current_user),
    service: CustomersService = Depends(get_customers_service),
) -> CustomerTagsResponse:
    try:
        result = service.list_tags(business_id)
    except ServiceError as exc:  # pragma: no cover
        return _service_error_response(exc)
    return CustomerTagsResponse(items=[_map_tag(tag) for tag in result.items])


@router.post(
    "/tags",
    response_model=CustomerTag,
    status_code=status.HTTP_201_CREATED,
    summary="Create a customer tag",
)
async def create_customer_tag_endpoint(
    payload: CustomerTagCreateRequest,
    business_id: UUID = Depends(require_business_id),
    current_user: AuthenticatedUser = Depends(get_current_user),
    service: CustomersService = Depends(get_customers_service),
) -> CustomerTag:
    input_data = CreateCustomerTagInput(
        business_id=business_id,
        label=payload.label.strip(),
        color=payload.color,
    )
    try:
        tag = service.create_tag(input_data)
    except ServiceError as exc:  # pragma: no cover
        return _service_error_response(exc)
    return _map_tag(tag)


@router.put(
    "/{customer_id}/tags",
    response_model=CustomerTagsResponse,
    summary="Assign tags to customer",
)
async def update_customer_tags_endpoint(
    customer_id: UUID,
    payload: CustomerTagAssignmentRequest,
    business_id: UUID = Depends(require_business_id),
    current_user: AuthenticatedUser = Depends(get_current_user),
    service: CustomersService = Depends(get_customers_service),
) -> CustomerTagsResponse:
    input_data = UpdateCustomerTagsInput(
        business_id=business_id,
        customer_id=customer_id,
        tag_ids=list(payload.tag_ids),
    )
    try:
        result = service.update_customer_tags(input_data)
    except ServiceError as exc:  # pragma: no cover
        return _service_error_response(exc)
    return CustomerTagsResponse(items=[_map_tag(tag) for tag in result.tags])


# ---------------------------------------------------------------------------
# Mapping helpers


def _map_customer_list_item(dto: CustomerListItemDTO) -> CustomerListItem:
    return CustomerListItem(
        id=dto.id,
        full_name=dto.full_name,
        primary_email=dto.primary_email,
        conversations_count=dto.conversations_count,
        satisfaction_score=dto.satisfaction_score,
        last_contact_at=dto.last_contact_at,
        lifecycle_stage=dto.lifecycle_stage.value,
    )


def _map_customer_detail(dto: CustomerDetailDTO) -> CustomerDetail:
    return CustomerDetail(
        id=dto.id,
        business_id=dto.business_id,
        full_name=dto.full_name,
        primary_email=dto.primary_email,
        primary_phone=dto.primary_phone,
        country=dto.country,
        lifecycle_stage=dto.lifecycle_stage.value,
        satisfaction_score=dto.satisfaction_score,
        persona_tags=list(dto.persona_tags),
        last_contact_at=dto.last_contact_at,
        created_at=dto.created_at,
        updated_at=dto.updated_at,
        stats=_map_stats(dto.stats),
        contacts=[_map_contact_method(method) for method in dto.contacts],
        tags=[_map_tag(tag) for tag in dto.tags],
        cases_open=[_map_case_link(case) for case in dto.cases_open],
        cases_resolved=[_map_case_link(case) for case in dto.cases_resolved],
        notes=[_map_note(note) for note in dto.notes],
        activity=[_map_activity_event(event) for event in dto.activity],
    )


def _map_stats(dto: CustomerStatsDTO) -> CustomerStats:
    return CustomerStats(
        conversations_total=dto.conversations_total,
        conversations_last_30_days=dto.conversations_last_30_days,
        csat_average=dto.csat_average,
        csat_trend=dto.csat_trend,
        expansion_opportunities=dto.expansion_opportunities,
    )


def _map_contact_method(dto: CustomerContactMethodDTO) -> CustomerContactMethod:
    return CustomerContactMethod(
        type=dto.method_type.value,
        value=dto.value,
        is_primary=dto.is_primary,
    )


def _map_tag(dto: CustomerTagDTO) -> CustomerTag:
    return CustomerTag(id=dto.id, label=dto.label, color=dto.color)


def _map_note(dto: CustomerNoteDTO) -> CustomerNote:
    return CustomerNote(
        id=dto.id,
        customer_id=dto.customer_id,
        author_user_id=dto.author_user_id,
        author_agent_id=dto.author_agent_id,
        visibility=dto.visibility.value,
        body=dto.body,
        pinned=dto.pinned,
        created_at=dto.created_at,
        updated_at=dto.updated_at,
    )


def _map_case_link(dto: CustomerCaseLinkDTO) -> CustomerCaseLink:
    return CustomerCaseLink(
        id=dto.id,
        title=dto.title,
        status=dto.status.value,
        priority=dto.priority.value,
        opened_at=dto.opened_at,
    )


def _map_activity_event(dto: CustomerActivityEventDTO) -> CustomerActivityEvent:
    return CustomerActivityEvent(
        id=dto.id,
        customer_id=dto.customer_id,
        event_type=dto.event_type.value,
        occurred_at=dto.occurred_at,
        actor_user_id=dto.actor_user_id,
        actor_agent_id=dto.actor_agent_id,
        actor_customer_id=dto.actor_customer_id,
        case_id=dto.case_id,
        conversation_id=dto.conversation_id,
        details=dto.details,
    )
