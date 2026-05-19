from __future__ import annotations

import ipaddress
import json
import socket
import uuid
from http import HTTPStatus
from typing import Any
from urllib.parse import urlsplit

from django.http import HttpRequest, JsonResponse

from apps.accounts.models import BusinessProfile


def _parse_json_body(request: HttpRequest) -> tuple[dict[str, Any] | None, JsonResponse | None]:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )
    if not isinstance(payload, dict):
        return None, JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be a JSON object."},
            status=HTTPStatus.BAD_REQUEST,
        )
    return payload, None


def _resolve_business_for_request(
    request: HttpRequest,
    business_id: str | None,
) -> tuple[BusinessProfile | None, JsonResponse | None]:
    if not request.user.is_authenticated:
        return None, JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)

    candidate = business_id or request.headers.get("X-Business-Id") or request.META.get("HTTP_X_BUSINESS_ID")
    if candidate:
        try:
            business_uuid = candidate if isinstance(candidate, uuid.UUID) else uuid.UUID(str(candidate))
        except (TypeError, ValueError):
            return None, JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "business_id must be a valid UUID."},
                status=HTTPStatus.BAD_REQUEST,
            )
        try:
            business = BusinessProfile.objects.get(id=business_uuid)
        except BusinessProfile.DoesNotExist:
            return None, JsonResponse({"error": "BUSINESS_NOT_FOUND", "message": "Business profile not found."}, status=HTTPStatus.NOT_FOUND)
    else:
        business = request.user.business_profiles.order_by("-created_at").first()
        if not business:
            return None, JsonResponse(
                {"error": "BUSINESS_REQUIRED", "message": "A business_id is required to perform this action."},
                status=HTTPStatus.BAD_REQUEST,
            )

    if request.user.is_staff:
        return business, None

    if not request.user.business_profiles.filter(id=business.id).exists():
        return None, JsonResponse({"error": "FORBIDDEN", "message": "You do not have access to this business profile."}, status=HTTPStatus.FORBIDDEN)

    return business, None


def _is_forbidden_ip(ip: ipaddress._BaseAddress) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_mcp_server_url(value: str) -> tuple[str | None, str | None]:
    raw = (value or "").strip()
    if not raw:
        return None, "serverUrl is required."
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None, "serverUrl must be a valid URL."

    if parsed.scheme not in {"http", "https"}:
        return None, "serverUrl must start with http:// or https://"
    if not parsed.netloc or not parsed.hostname:
        return None, "serverUrl must include a hostname."

    hostname = parsed.hostname.strip().lower()
    if hostname in {"localhost"} or hostname.endswith(".local"):
        return None, "serverUrl hostname is not allowed."

    # Block direct IP literals in private ranges and also resolve hostnames to defend against SSRF.
    try:
        ip = ipaddress.ip_address(hostname)
        if _is_forbidden_ip(ip):
            return None, "serverUrl must not point to a private or local network address."
        return raw, None
    except ValueError:
        pass

    port = parsed.port
    try:
        infos = socket.getaddrinfo(hostname, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror:
        return None, "serverUrl hostname could not be resolved."

    resolved_ips: set[str] = set()
    for _family, _socktype, _proto, _canonname, sockaddr in infos:
        if not sockaddr:
            continue
        candidate_ip = sockaddr[0]
        if not candidate_ip:
            continue
        resolved_ips.add(candidate_ip)

    if not resolved_ips:
        return None, "serverUrl hostname could not be resolved."

    for candidate_ip in resolved_ips:
        try:
            ip = ipaddress.ip_address(candidate_ip)
        except ValueError:
            continue
        if _is_forbidden_ip(ip):
            return None, "serverUrl must not resolve to a private or local network address."

    return raw, None
