from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.conf import settings


def _language_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for code, _label in getattr(settings, "LANGUAGES", ()):
        canonical = str(code or "").strip()
        normalized = canonical.lower()
        if not normalized:
            continue
        mapping[normalized] = canonical
        base = normalized.split("-", 1)[0]
        mapping.setdefault(base, canonical)
    return mapping


def normalize_language_code(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    if not raw:
        return ""
    language_map = _language_map()
    if raw in language_map:
        return language_map[raw]
    base = raw.split("-", 1)[0]
    return language_map.get(base, "")


def _primary_business_for_user(user: Any):
    if not getattr(user, "is_authenticated", False):
        return None
    manager = getattr(user, "business_profiles", None)
    if manager is None:
        return None
    return manager.order_by("-created_at").first()


def get_user_preferred_language(user: Any) -> str:
    business = _primary_business_for_user(user)
    if business is None:
        return ""

    metadata = business.metadata if isinstance(business.metadata, Mapping) else {}
    preferences = metadata.get("preferences") if isinstance(metadata.get("preferences"), Mapping) else {}
    candidates = (
        preferences.get("language"),
        metadata.get("language"),
        metadata.get("locale"),
    )
    for candidate in candidates:
        normalized = normalize_language_code(candidate)
        if normalized:
            return normalized
    return ""


def persist_user_preferred_language(user: Any, language_code: str) -> bool:
    normalized = normalize_language_code(language_code)
    if not normalized:
        return False

    business = _primary_business_for_user(user)
    if business is None:
        return False

    metadata = dict(business.metadata) if isinstance(business.metadata, Mapping) else {}
    preferences = dict(metadata.get("preferences")) if isinstance(metadata.get("preferences"), Mapping) else {}
    if preferences.get("language") == normalized:
        return False

    preferences["language"] = normalized
    metadata["preferences"] = preferences
    business.metadata = metadata
    business.save(update_fields=["metadata", "updated_at"])
    return True
