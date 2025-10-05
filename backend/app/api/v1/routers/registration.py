"""Router exposing registration wizard endpoints."""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Body, Depends, Header, Path, Request, status
from fastapi.responses import JSONResponse

from app.api.v1.deps import (
    enforce_registration_agent_rate_limit,
    enforce_registration_business_rate_limit,
    enforce_registration_complete_rate_limit,
    enforce_registration_session_rate_limit,
    enforce_registration_uploads_rate_limit,
    get_current_user,
    get_registration_service,
    require_owner_or_admin,
    require_registration_captcha,
)
from app.api.v1.schemas.registration import (
    AgentConfigRequest,
    AgentConfigResponse,
    AgentSummary,
    BusinessProfileRequest,
    BusinessProfileResponse,
    BusinessSummary,
    CompletionResponse,
    SessionProgress,
    StartRegistrationRequest,
    StartRegistrationResponse,
    StartRegistrationUser,
    UploadLinksModel,
    UploadLinksResponse,
)
from app.core.security import AuthenticatedUser
from app.schemas.registration import ApiErrorResponse
from app.repositories.dtos import RegistrationSessionRecord
from app.services import (
    AttachUploadLinksInput,
    AttachUploadLinksResult,
    CompleteRegistrationInput,
    CompletionStatus,
    ConfigureAgentInput,
    ConfigureAgentResult,
    RegistrationService,
    StartRegistrationInput,
    StartRegistrationResult,
    UpsertBusinessInput,
    UpsertBusinessResult,
)
from app.services.errors import ServiceError


router = APIRouter(
    prefix="/registration",
    tags=["registration"],
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ApiErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ApiErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ApiErrorResponse},
        status.HTTP_410_GONE: {"model": ApiErrorResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ApiErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ApiErrorResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ApiErrorResponse},
    },
)


ERROR_STATUS_MAP: dict[str, int] = {
    "validation": status.HTTP_400_BAD_REQUEST,
    "conflict": status.HTTP_409_CONFLICT,
    "not_found": status.HTTP_404_NOT_FOUND,
    "expired": status.HTTP_410_GONE,
    "forbidden": status.HTTP_403_FORBIDDEN,
    "db_timeout": status.HTTP_504_GATEWAY_TIMEOUT,
}


def _service_error_response(exc: ServiceError) -> JSONResponse:
    status_code = ERROR_STATUS_MAP.get(exc.code, status.HTTP_500_INTERNAL_SERVER_ERROR)
    return JSONResponse(status_code=status_code, content=exc.to_payload())


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    summary="Start a registration session",
    response_model=StartRegistrationResponse,
    # Temporarily remove dependencies for debugging
    # dependencies=[
    #     Depends(enforce_registration_session_rate_limit),
    #     Depends(require_registration_captcha),
    # ],
)
async def start_registration_endpoint(
    payload: StartRegistrationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    service: RegistrationService = Depends(get_registration_service),
) -> StartRegistrationResponse:
    input_data = StartRegistrationInput(
        first_name=payload.first_name.strip(),
        email=payload.email,
        password_hash=payload.password,
        auth_provider="password" if payload.password else "google",
        idempotency_key=idempotency_key,
    )
    try:
        result = service.start_registration(input_data)
    except ServiceError as exc:
        return _service_error_response(exc)
    return _map_start_registration(result)


@router.put(
    "/sessions/{registration_id}/business",
    status_code=status.HTTP_200_OK,
    summary="Upsert business profile",
    response_model=BusinessProfileResponse,
    dependencies=[Depends(enforce_registration_business_rate_limit)],
)
async def upsert_business_profile_endpoint(
    registration_id: uuid.UUID = Path(..., description="Registration session identifier"),
    payload: BusinessProfileRequest = Body(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    service: RegistrationService = Depends(get_registration_service),
) -> BusinessProfileResponse:
    user_id = current_user.user_id
    input_data = UpsertBusinessInput(
        registration_id=registration_id,
        user_id=user_id,
        business_name=payload.business_name.strip(),
        industry_label=payload.industry,
        specify_industry=payload.specify_industry,
        line_of_business=payload.line_of_business,
        line_of_business_custom=payload.line_of_business_custom,
        country=payload.country,
        website=str(payload.website) if payload.website else None,
        idempotency_key=idempotency_key,
    )
    try:
        result = service.upsert_business_profile(input_data)
    except ServiceError as exc:
        return _service_error_response(exc)
    return _map_business_profile(result)


@router.put(
    "/businesses/{business_id}/agent",
    status_code=status.HTTP_200_OK,
    summary="Configure agent",
    response_model=AgentConfigResponse,
    dependencies=[Depends(enforce_registration_agent_rate_limit)],
)
async def configure_agent_endpoint(
    business_id: uuid.UUID = Path(..., description="Business identifier"),
    payload: AgentConfigRequest = Body(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: AuthenticatedUser = Depends(require_owner_or_admin()),
    service: RegistrationService = Depends(get_registration_service),
) -> AgentConfigResponse:
    user_id = current_user.user_id
    input_data = ConfigureAgentInput(
        business_id=business_id,
        user_id=user_id,
        agent_name=payload.agent_name,
        agent_title=payload.agent_title,
        agent_tone=payload.agent_tone,
        agent_traits=payload.agent_traits,
        agent_escalation=payload.agent_escalation,
        idempotency_key=idempotency_key,
    )
    try:
        result = service.configure_agent(input_data)
    except ServiceError as exc:
        return _service_error_response(exc)
    return _map_agent_config(result)


@router.post(
    "/businesses/{business_id}/uploads",
    status_code=status.HTTP_200_OK,
    summary="Attach knowledge links",
    response_model=UploadLinksResponse,
    dependencies=[Depends(enforce_registration_uploads_rate_limit)],
)
async def attach_uploads_endpoint(
    business_id: uuid.UUID = Path(..., description="Business identifier"),
    payload: UploadLinksModel = Body(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: AuthenticatedUser = Depends(require_owner_or_admin()),
    service: RegistrationService = Depends(get_registration_service),
) -> UploadLinksResponse:
    user_id = current_user.user_id
    links = {category: [str(url) for url in urls] for category, urls in payload.links.items()}
    input_data = AttachUploadLinksInput(
        business_id=business_id,
        user_id=user_id,
        links=links,
        language=payload.language,
        idempotency_key=idempotency_key,
    )
    try:
        result = service.attach_upload_links(input_data)
    except ServiceError as exc:
        return _service_error_response(exc)
    return _map_upload_links(result)


@router.post(
    "/sessions/{registration_id}/complete",
    status_code=status.HTTP_200_OK,
    summary="Complete registration",
    response_model=CompletionResponse,
    dependencies=[Depends(enforce_registration_complete_rate_limit)],
)
async def complete_registration_endpoint(
    registration_id: uuid.UUID = Path(..., description="Registration session identifier"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    service: RegistrationService = Depends(get_registration_service),
) -> CompletionResponse:
    user_id = current_user.user_id
    try:
        result = service.complete_registration(
            CompleteRegistrationInput(registration_id=registration_id, user_id=user_id)
        )
    except ServiceError as exc:
        return _service_error_response(exc)
    return _map_completion(result)


# Helpers -------------------------------------------------------------------


def _map_start_registration(result: StartRegistrationResult) -> StartRegistrationResponse:
    return StartRegistrationResponse(
        registration_id=result.registration.id,
        user=StartRegistrationUser(
            id=result.user.id,
            email=result.user.email,
            first_name=result.user.first_name,
        ),
        next_step=result.next_step,
    )


def _map_business_profile(result: UpsertBusinessResult) -> BusinessProfileResponse:
    business_summary = BusinessSummary(
        id=result.business.id,
        name=result.business.name,
        industry_code=result.business.industry_code,
    )
    session = _session_progress(result.session)
    return BusinessProfileResponse(business=business_summary, niches=list(result.niches), session=session)


def _map_agent_config(result: ConfigureAgentResult) -> AgentConfigResponse:
    agent_summary = None
    if result.agent is not None:
        agent_summary = AgentSummary(
            id=result.agent.agent.id,
            name=result.agent.agent.name,
            role=result.agent.agent.role.value,
            tone=result.agent.agent.tone.value,
            traits=[trait.trait_code.value for trait in result.agent.traits],
            escalation_rule=result.agent.agent.escalation_rule.value,
        )
    return AgentConfigResponse(agent=agent_summary, session=_session_progress(result.session))


def _map_upload_links(result: AttachUploadLinksResult) -> UploadLinksResponse:
    return UploadLinksResponse(
        created=dict(result.created_counts),
        duplicates=result.duplicate_count,
        session=_session_progress(result.session),
    )


def _map_completion(result: CompletionStatus) -> CompletionResponse:
    return CompletionResponse(session=_session_progress(result.session), progress=dict(result.progress))


def _session_progress(record: RegistrationSessionRecord) -> SessionProgress:
    return SessionProgress(
        id=record.id,
        current_step=record.current_step.value if hasattr(record.current_step, "value") else record.current_step,
        steps_completed=record.steps_completed,
        total_steps=record.total_steps,
    )
