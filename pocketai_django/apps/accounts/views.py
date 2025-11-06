from __future__ import annotations

import json
from typing import Any, Mapping

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods, require_POST


def _wants_json(request: HttpRequest) -> bool:
    """Detect whether the client expects a JSON response."""

    content_type = request.headers.get("Content-Type") or ""
    accept = request.headers.get("Accept") or ""
    x_requested_with = request.headers.get("X-Requested-With") or ""
    if "application/json" in content_type.lower():
        return True
    if "application/json" in accept.lower():
        return True
    return x_requested_with.lower() == "xmlhttprequest"


def _parse_payload(request: HttpRequest) -> Mapping[str, Any]:
    """Extract incoming form or JSON payload into a mapping interface."""

    content_type = request.headers.get("Content-Type") or ""
    if "application/json" in content_type.lower():
        try:
            raw = request.body.decode("utf-8") or "{}"
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        if isinstance(data, dict):
            return data
        return {}
    return request.POST


def _default_login_redirect() -> str:
    return getattr(settings, "LOGIN_REDIRECT_URL", "/")


def _default_logout_redirect() -> str:
    return getattr(settings, "LOGOUT_REDIRECT_URL", "/")


def _safe_redirect(request: HttpRequest, candidate: str | None, fallback: str) -> str:
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback


@csrf_protect
@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    """
    Authenticate the user using email and password credentials.

    Supports JSON and form-encoded submissions. For GET requests the
    server-rendered login page is returned so that redirects from
    `login_required` can provide a complete UX without JavaScript.
    """

    next_param = request.GET.get("next") or ""
    fallback_redirect = _default_login_redirect()

    if request.user.is_authenticated:
        redirect_to = _safe_redirect(request, next_param, fallback_redirect)
        return redirect(redirect_to)

    if request.method == "GET":
        context = {
            "next": next_param,
            "prefill_email": request.GET.get("email") or "",
        }
        return render(request, "frontend/login.html", context)

    data = _parse_payload(request)
    email = str(data.get("email") or "").strip()
    password = str(data.get("password") or "")
    redirect_candidate = data.get("next") or next_param
    redirect_to = _safe_redirect(request, str(redirect_candidate or ""), fallback_redirect)
    wants_json = _wants_json(request)

    if not email or not password:
        message = "Email and password are required."
        if wants_json:
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": message},
                status=400,
            )
        messages.error(request, message)
        context = {"next": redirect_candidate or "", "prefill_email": email}
        return render(request, "frontend/login.html", context, status=400)

    user = authenticate(request, username=email, password=password)
    if user is None:
        message = "Invalid email or password."
        if wants_json:
            return JsonResponse(
                {"error": "INVALID_CREDENTIALS", "message": message},
                status=400,
            )
        messages.error(request, message)
        context = {"next": redirect_candidate or "", "prefill_email": email}
        return render(request, "frontend/login.html", context, status=400)

    if not user.is_active:
        message = "This account is inactive. Contact support for help."
        if wants_json:
            return JsonResponse(
                {"error": "ACCOUNT_INACTIVE", "message": message},
                status=403,
            )
        messages.error(request, message)
        context = {"next": redirect_candidate or "", "prefill_email": email}
        return render(request, "frontend/login.html", context, status=403)

    login(request, user)
    request.session["auth_entrypoint"] = "app"

    if wants_json:
        return JsonResponse(
            {
                "status": "ok",
                "redirect": redirect_to,
                "user": {
                    "id": str(getattr(user, "public_id", "")) or str(user.pk),
                    "email": user.get_username(),
                    "displayName": user.get_short_name() or user.get_username(),
                },
            }
        )

    return HttpResponseRedirect(redirect_to)


@csrf_protect
@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """
    Terminate the authenticated session.

    Supports both synchronous form submissions and JSON requests from
    interactive components (e.g. navbar dropdowns).
    """

    data = _parse_payload(request)
    redirect_candidate = data.get("next") or request.GET.get("next")
    fallback_redirect = _default_logout_redirect()
    redirect_to = _safe_redirect(request, str(redirect_candidate or ""), fallback_redirect)
    wants_json = _wants_json(request)

    logout(request)

    if wants_json:
        return JsonResponse({"status": "ok", "redirect": redirect_to})

    return HttpResponseRedirect(redirect_to)
