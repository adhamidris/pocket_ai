from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.models.registration import Agent, Business
from app.api.v1.deps import require_business_id_public as require_business_id

from app.schemas.chat_portal import (

    ChatAgentPreview,

    ChatMessage,

    ChatMessageAttachmentDescriptor,

    ChatMessageSendRequest,

    ChatMessageSendResponse,

    ChatMessagesListResponse,

    ChatPresencePingRequest,

    ChatCsatSubmissionRequest,

    ChatSessionCreateRequest,

    ChatSessionCreateResponse,

    ChatSessionState,

)

from app.models.conversations import (

    ChatVisitor,

    Conversation,

    ConversationMessageChannel,

    ConversationMessageType,

    ConversationMessageVisibility,

)

from app.services.chat_sessions import ChatSessionsService, CreateChatSessionInput

from app.services.messages import MessagesService, AddMessageInput, MessageAttachmentInput, ListMessagesInput

from app.services.conversations import ConversationsService, RecordConversationCsatInput

from app.services.errors import ServiceNotFoundError, ServiceValidationError


router = APIRouter(prefix="/portal", tags=["portal"])

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    slug = _SLUG_PATTERN.sub("-", value.strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)[:120]


def _slug_expr(col):
    """
    SQL slug normalization: lower -> non-alnum -> "-" -> collapse "-+" -> trim "-"
    Mirrors _slugify() for DB-side comparisons.
    """
    return func.btrim(
        func.regexp_replace(
            func.regexp_replace(func.lower(col), r'[^a-z0-9]+', '-', 'g'),
            r'-+', '-', 'g'
        ),
        '-'
    )


@router.get(
    "/resolve/{business_slug}/{agent_slug}",
    summary="Resolve business and agent slugs for public portal",
)
async def resolve_portal_handle(
    business_slug: str = Path(..., min_length=1, max_length=120),
    agent_slug: str = Path(..., min_length=1, max_length=120),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Returns:
      {
        "business_id": UUID,
        "agent_handle": str,        # equals agent_slug
        "agent": {
          "id": UUID,
          "name": str,
          "role": str,
          "avatar_url": str|null,
          "business_name": str|null
        }
      }
    """

    # 1) Collect all businesses whose slugified name equals business_slug
    business_stmt = (
        select(Business)
        .where(_slug_expr(Business.name) == business_slug.lower())
        .order_by(Business.created_at.asc(), Business.id.asc())
    )
    candidates = db.execute(business_stmt).scalars().all()
    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Business not found"},
        )

    # 2) For each candidate business, try by agent public_slug (case-insensitive),
    #    then fallback to slugified Agent.name. Pick the first match.
    business = None
    agent = None

    for b in candidates:
        agent_stmt = (
            select(Agent)
            .where(
                Agent.business_id == b.id,
                func.lower(Agent.public_slug) == agent_slug.lower(),
            )
            .order_by(Agent.created_at.asc(), Agent.id.asc())
            .limit(1)
        )
        agent = db.execute(agent_stmt).scalars().first()

        if agent is None:
            agent_fallback_stmt = (
                select(Agent)
                .where(
                    Agent.business_id == b.id,
                    _slug_expr(Agent.name) == agent_slug.lower(),
                )
                .order_by(Agent.created_at.asc(), Agent.id.asc())
                .limit(1)
            )
            agent = db.execute(agent_fallback_stmt).scalars().first()

        if agent is not None:
            business = b
            break

    if business is None or agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Agent not found"},
        )

    return {
        "business_id": str(business.id),
        "agent_handle": agent.public_slug or agent_slug,
        "agent": {
            "id": str(agent.id),
            "name": agent.name,
            "role": agent.role.value if hasattr(agent.role, "value") else str(agent.role),
            "avatar_url": agent.avatar_url,
            "business_name": business.name,
        },
    }

def _get_agent_by_handle(db: Session, *, business_id, agent_handle: str) -> Agent | None:
    """
    Resolve an agent by public_slug (case-insensitive), then fallback to exact name (lower).
    Mirrors the logic used in /portal/resolve for public_slug preference.
    """
    stmt = (
        select(Agent)
        .where(
            Agent.business_id == business_id,
            func.lower(Agent.public_slug) == agent_handle.lower(),
        )
        .order_by(Agent.created_at.asc(), Agent.id.asc())
        .limit(1)
    )
    agent = db.execute(stmt).scalars().first()
    if agent is not None:
        return agent

    # Fallback: exact lowercase name match (simple approximation)
    stmt2 = (
        select(Agent)
        .where(
            Agent.business_id == business_id,
            func.lower(Agent.name) == agent_handle.lower(),
        )
        .order_by(Agent.created_at.asc(), Agent.id.asc())
        .limit(1)
    )
    return db.execute(stmt2).scalars().first()


def _get_active_conversation_for_session(
    db: Session, *, business_id, session_token: str
) -> Conversation:
    """
    Find the most recent conversation for a visitor identified by session_token within a business.
    """
    visitor = (
        db.execute(
            select(ChatVisitor).where(
                ChatVisitor.business_id == business_id,
                ChatVisitor.session_token == session_token,
            )
        )
        .scalars()
        .first()
    )
    if visitor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Session not found"},
        )

    conversation = (
        db.execute(
            select(Conversation)
            .where(
                Conversation.business_id == business_id,
                Conversation.visitor_id == visitor.id,
            )
            .order_by(Conversation.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Active conversation not found"},
        )
    return conversation


def _map_agent_preview(agent: Agent, business_name: str | None) -> ChatAgentPreview:
    return ChatAgentPreview(
        id=agent.id,
        name=agent.name,
        role=agent.role.value if hasattr(agent.role, "value") else str(agent.role),
        avatar_url=agent.avatar_url,
        business_name=business_name,
    )


@router.post(
    "/sessions",
    response_model=ChatSessionCreateResponse,
    summary="Create or refresh a portal chat session",
)
def create_or_refresh_session_endpoint(
    payload: ChatSessionCreateRequest,
    business_id=Depends(require_business_id),
    db: Session = Depends(get_db),
) -> ChatSessionCreateResponse:
    if not payload.agent_handle:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "validation_error", "message": "agent_handle is required"},
        )

    agent = _get_agent_by_handle(db, business_id=business_id, agent_handle=payload.agent_handle)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Agent not found"},
        )

    service = ChatSessionsService(session=db)
    try:
        result = service.create_or_refresh_session(
            CreateChatSessionInput(
                business_id=business_id,
                agent_id=agent.id,
                channel=payload.channel,
                locale=payload.locale,
                landing_page=payload.landing_page,
                fingerprint_hash=payload.fingerprint_hash,
                utm=payload.utm,
                existing_session_token=payload.existing_session_token,
            )
        )
    except ServiceValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "validation_error", "message": str(exc)},
        ) from exc
    except ServiceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": str(exc)},
        ) from exc

    session = result.session
    business_name = None
    try:
        # Avoid circular import; portal.resolve already returns business.name there.
        # If relationship is loaded, use it; else leave None.
        business_name = getattr(agent, "business", None).name if getattr(agent, "business", None) else None
    except Exception:
        business_name = None

    return ChatSessionCreateResponse(
        session=ChatSessionState(
            session_token=session.session_token,
            visitor_id=session.visitor_id,
            visitor_type=session.visitor_type,
            conversation_id=session.conversation_id,
            conversation_status=session.conversation_status,
            started_at=session.started_at,
            expires_at=session.expires_at,
        ),
        agent=_map_agent_preview(agent, business_name),
        messages=[],  # Optionally seed welcome/system messages here later
    )


def _map_message_attachment(att) -> ChatMessageAttachmentDescriptor:
    return ChatMessageAttachmentDescriptor(
        id=att.id,
        storage_asset_id=att.storage_asset_id,
        filename=getattr(att, "filename", None) or "attachment",
        content_type=getattr(att, "content_type", None),
        size_bytes=getattr(att, "size_bytes", 1),
        download_url=None,
        caption=getattr(att, "caption", None),
        metadata=None,
    )


def _map_message(dto) -> ChatMessage:
    # dto is MessagesService.ConversationMessageDTO
    return ChatMessage(
        id=dto.id,
        conversation_id=dto.conversation_id,
        message_type=dto.message_type,
        visibility=dto.visibility,
        channel=dto.channel,
        body=dto.body,
        payload=dto.payload,
        sent_at=dto.sent_at,
        author={  # matches ChatMessageAuthor
            "agent_id": dto.author_agent_id,
            "customer_id": dto.author_customer_id,
            "user_display_name": None,
        },
        attachments=tuple(_map_message_attachment(att) for att in dto.attachments),
    )


@router.get(
    "/messages",
    response_model=ChatMessagesListResponse,
    summary="List messages for a session (by session token)",
)
def list_messages_endpoint(
    session_token: str,
    cursor: str | None = None,
    limit: int = 50,
    business_id=Depends(require_business_id),
    db: Session = Depends(get_db),
) -> ChatMessagesListResponse:
    conversation = _get_active_conversation_for_session(
        db, business_id=business_id, session_token=session_token
    )
    svc = MessagesService(db)
    try:
        res = svc.list_messages(
            ListMessagesInput(
                business_id=business_id,
                conversation_id=conversation.id,
                limit=limit,
                cursor=cursor,
            )
        )
    except ServiceValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "validation_error", "message": str(exc)},
        ) from exc
    return ChatMessagesListResponse(
        messages=tuple(_map_message(it) for it in res.items),
        next_cursor=res.next_cursor,
        has_more=res.has_more,
    )


@router.post(
    "/messages",
    response_model=ChatMessageSendResponse,
    summary="Send a visitor-authored message",
)
def send_message_endpoint(
    payload: ChatMessageSendRequest,
    business_id=Depends(require_business_id),
    db: Session = Depends(get_db),
) -> ChatMessageSendResponse:
    conversation = _get_active_conversation_for_session(
        db, business_id=business_id, session_token=payload.session_token
    )
    svc = MessagesService(db)
    try:
        res = svc.add_message(
            AddMessageInput(
                business_id=business_id,
                conversation_id=conversation.id,
                message_type=ConversationMessageType.CUSTOMER,
                visibility=ConversationMessageVisibility.PUBLIC,
                channel=payload.channel or ConversationMessageChannel.TEXT,
                body=payload.body,
                payload=payload.payload,
                author_customer_id=conversation.customer_id,
                attachments=tuple(
                    MessageAttachmentInput(storage_asset_id=att.storage_asset_id, caption=att.caption)
                    for att in (payload.attachments or ())
                ),
            )
        )
    except (ServiceValidationError, ServiceNotFoundError) as exc:
        code = "not_found" if isinstance(exc, ServiceNotFoundError) else "validation_error"
        http = status.HTTP_404_NOT_FOUND if isinstance(exc, ServiceNotFoundError) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=http, detail={"code": code, "message": str(exc)}) from exc

    return ChatMessageSendResponse(
        message=_map_message(res.message),
        follow_up_messages=(),  # placeholder for any agent/system follow-ups
    )


@router.post(
    "/csat",
    summary="Submit CSAT for the current conversation (by session token)",
)
def submit_csat_endpoint(
    payload: ChatCsatSubmissionRequest,
    business_id=Depends(require_business_id),
    db: Session = Depends(get_db),
):
    conversation = _get_active_conversation_for_session(
        db, business_id=business_id, session_token=payload.session_token
    )
    svc = ConversationsService(db)
    try:
        result = svc.record_conversation_csat(
            RecordConversationCsatInput(
                business_id=business_id,
                conversation_id=conversation.id,
                score=payload.score,
                comment=payload.comment,
                recorded_at=None,
            )
        )
    except (ServiceValidationError, ServiceNotFoundError) as exc:
        code = "not_found" if isinstance(exc, ServiceNotFoundError) else "validation_error"
        http = status.HTTP_404_NOT_FOUND if isinstance(exc, ServiceNotFoundError) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=http, detail={"code": code, "message": str(exc)}) from exc

    return {"conversation_id": str(result.conversation_id), "recorded_at": result.recorded_at}
