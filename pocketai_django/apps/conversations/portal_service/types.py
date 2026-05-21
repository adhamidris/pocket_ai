from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta
from typing import Sequence


class PortalNotFoundError(Exception):
    """Raised when portal business/agent/session cannot be found."""


class PortalValidationError(ValueError):
    """Raised when incoming payload fails validation."""


class PortalAuthorizationError(PermissionError):
    """Raised when an authenticated user cannot access a portal resource."""


@dataclasses.dataclass(frozen=True, slots=True)
class PortalAgentSummary:
    id: uuid.UUID
    name: str
    role: str
    slug: str
    shareable_path: str


@dataclasses.dataclass(frozen=True, slots=True)
class PortalBusinessSummary:
    id: uuid.UUID
    name: str
    slug: str


@dataclasses.dataclass(frozen=True, slots=True)
class PortalMessage:
    id: uuid.UUID
    sender: str
    body: str
    sent_at: datetime
    metadata: dict
    content_blocks: list[dict[str, object]]


@dataclasses.dataclass(frozen=True, slots=True)
class PortalSessionState:
    conversation_id: uuid.UUID
    session_token: str
    status: str
    started_at: datetime
    expires_at: datetime | None
    session_type: str = "chat"
    custom_assistant_id: uuid.UUID | None = None
    custom_assistant_name: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class PortalSessionBootstrap:
    business: PortalBusinessSummary
    agent: PortalAgentSummary
    session: PortalSessionState
    messages: Sequence[PortalMessage]


DEFAULT_SESSION_TTL: timedelta | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class PortalSessionSummary:
    """Lightweight session info for session history list."""
    conversation_id: uuid.UUID
    session_token: str
    title: str
    started_at: datetime
    last_activity_at: datetime
    status: str
    message_count: int
    preview: str
    session_type: str = "chat"
    custom_assistant_id: uuid.UUID | None = None
    custom_assistant_name: str = ""
    custom_assistant_agent_name: str = ""
