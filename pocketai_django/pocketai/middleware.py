from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.urls import reverse
from django.utils import translation

from pocketai.language import (
    get_user_preferred_language,
    normalize_language_code,
    persist_user_preferred_language,
)


logger = logging.getLogger(__name__)


class FrontendAuthBoundaryMiddleware:
    """
    Keep Django admin authentication isolated from the Pocket app session.

    When a browser session is authenticated via the admin console we mark it
    with ``auth_entrypoint = "admin"``. For all non-admin requests we surface an
    anonymous user so the frontend never thinks the admin session is a logged-in
    tenant.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        admin_index = reverse("admin:index")
        # Ensure we include trailing slash for prefix comparisons.
        self.admin_prefix = admin_index if admin_index.endswith("/") else f"{admin_index}/"

    def __call__(self, request):
        entrypoint = request.session.get("auth_entrypoint")
        if request.path.startswith(self.admin_prefix):
            return self.get_response(request)

        if getattr(request, "user", None) and request.user.is_authenticated and entrypoint == "admin":
            request.user = AnonymousUser()

        return self.get_response(request)


class LanguagePreferenceMiddleware:
    """
    Support direct language switching via `?lang=ar|en` and bootstrap the
    preferred language for authenticated users when no language cookie exists.
    """

    query_param = "lang"

    def __init__(self, get_response):
        self.get_response = get_response
        self.cookie_name = getattr(settings, "LANGUAGE_COOKIE_NAME", "django_language")

    def __call__(self, request):
        selected = ""
        source = ""

        query_language = normalize_language_code(request.GET.get(self.query_param))
        if query_language:
            selected = query_language
            source = "query"
        elif request.method == "GET":
            has_cookie = bool(request.COOKIES.get(self.cookie_name))
            if not has_cookie:
                selected = get_user_preferred_language(getattr(request, "user", None))
                if selected:
                    source = "profile"

        if selected:
            translation.activate(selected)
            request.LANGUAGE_CODE = selected

        response = self.get_response(request)

        if selected:
            response.set_cookie(
                self.cookie_name,
                selected,
                max_age=getattr(settings, "LANGUAGE_COOKIE_AGE", None),
                path=getattr(settings, "LANGUAGE_COOKIE_PATH", "/"),
                domain=getattr(settings, "LANGUAGE_COOKIE_DOMAIN", None),
                secure=getattr(settings, "LANGUAGE_COOKIE_SECURE", False),
                httponly=getattr(settings, "LANGUAGE_COOKIE_HTTPONLY", False),
                samesite=getattr(settings, "LANGUAGE_COOKIE_SAMESITE", "Lax"),
            )

        if source == "query" and getattr(request.user, "is_authenticated", False):
            try:
                persist_user_preferred_language(request.user, selected)
            except Exception:  # pragma: no cover - defensive persistence fallback
                logger.exception("Failed to persist query language preference")

        return response
