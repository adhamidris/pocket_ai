from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class McpRemoteServerInfo:
    name: str | None = None
    title: str | None = None
    version: str | None = None
    description: str | None = None
    website_url: str | None = None


@dataclass(frozen=True)
class McpRemoteTool:
    name: str
    title: str | None = None
    description: str | None = None
    input_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class McpRemoteSession:
    transport: str
    endpoint_url: str
    message_url: str | None
    session_id: str | None
    protocol_version: str
    server_info: McpRemoteServerInfo | None
