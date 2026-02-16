from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse
from django.views.i18n import set_language as django_set_language

from pocketai.language import normalize_language_code, persist_user_preferred_language


logger = logging.getLogger(__name__)


def set_language(request: HttpRequest) -> HttpResponse:
    """
    Wrap Django's set_language view so authenticated language preference is
    also persisted in tenant metadata.
    """

    requested = normalize_language_code(request.POST.get("language") or request.GET.get("language"))
    response = django_set_language(request)
    if requested and getattr(request.user, "is_authenticated", False):
        try:
            persist_user_preferred_language(request.user, requested)
        except Exception:  # pragma: no cover - defensive persistence fallback
            logger.exception("Failed to persist user language preference")
    return response
