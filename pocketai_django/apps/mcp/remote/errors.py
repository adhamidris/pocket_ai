from __future__ import annotations


class McpRemoteError(RuntimeError):
    pass


class McpRemoteTransportError(McpRemoteError):
    pass


class McpRemoteProtocolError(McpRemoteError):
    pass


class McpRemoteStreamableNotSupported(McpRemoteTransportError):
    """Raised when a server rejects Streamable HTTP initialize (legacy transport expected)."""


class McpRemoteHttpStatusError(McpRemoteTransportError):
    def __init__(self, message: str, *, status_code: int | None = None, retry_after: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class McpRemoteSsrBlockedError(McpRemoteTransportError):
    """Raised when an MCP network request is blocked by SSRF policy."""
