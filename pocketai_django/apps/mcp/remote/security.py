from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from .errors import McpRemoteSsrBlockedError


def _is_forbidden_ip(ip: ipaddress._BaseAddress) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_mcp_url_for_ssrf(url: str, *, action: str) -> None:
    raw = (url or "").strip()
    if not raw:
        raise McpRemoteSsrBlockedError(f"Blocked by SSRF policy during {action} (empty URL).")
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise McpRemoteSsrBlockedError(f"Blocked by SSRF policy during {action} (invalid URL).") from exc

    if parsed.scheme not in {"http", "https"}:
        raise McpRemoteSsrBlockedError(f"Blocked by SSRF policy during {action} (unsupported scheme).")
    if not parsed.netloc or not parsed.hostname:
        raise McpRemoteSsrBlockedError(f"Blocked by SSRF policy during {action} (missing hostname).")

    hostname = parsed.hostname.strip().lower().rstrip(".")
    if hostname in {"localhost"} or hostname.endswith(".local"):
        raise McpRemoteSsrBlockedError(f"Blocked by SSRF policy during {action} (hostname not allowed).")

    try:
        port = parsed.port
    except ValueError as exc:
        raise McpRemoteSsrBlockedError(f"Blocked by SSRF policy during {action} (invalid port).") from exc
    port = port or (443 if parsed.scheme == "https" else 80)

    # Block direct IP literals in private ranges and also resolve hostnames to defend against SSRF.
    try:
        ip = ipaddress.ip_address(hostname)
        if _is_forbidden_ip(ip):
            raise McpRemoteSsrBlockedError(f"Blocked by SSRF policy during {action} (private network address).")
        return
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise McpRemoteSsrBlockedError(f"Blocked by SSRF policy during {action} (hostname could not be resolved).") from exc

    resolved_ips: set[str] = set()
    for _family, _socktype, _proto, _canonname, sockaddr in infos:
        if not sockaddr:
            continue
        candidate_ip = sockaddr[0]
        if candidate_ip:
            resolved_ips.add(candidate_ip)

    if not resolved_ips:
        raise McpRemoteSsrBlockedError(f"Blocked by SSRF policy during {action} (hostname could not be resolved).")

    for candidate_ip in resolved_ips:
        try:
            ip = ipaddress.ip_address(candidate_ip)
        except ValueError:
            continue
        if _is_forbidden_ip(ip):
            raise McpRemoteSsrBlockedError(f"Blocked by SSRF policy during {action} (hostname resolves to private network).")
