from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Mapping
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import AgentProfile, BusinessProfile, KnowledgeUpload, RegistrationSession, User


class RegistrationError(Exception):
    """Base error for registration failures."""


class EmailAlreadyRegistered(RegistrationError):
    """Raised when attempting to register with an existing email."""


@dataclass(slots=True)
class RegistrationResult:
    user: User
    session: RegistrationSession


class BusinessProfileError(Exception):
    """Raised when business profile validation fails."""


@dataclass(slots=True)
class BusinessProfileResult:
    profile: BusinessProfile
    session: RegistrationSession


class AgentProfileError(Exception):
    """Raised when agent configuration validation fails."""


@dataclass(slots=True)
class AgentProfileResult:
    profile: AgentProfile
    session: RegistrationSession


class KnowledgeUploadError(Exception):
    """Raised when knowledge upload validation fails."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


@dataclass(slots=True)
class KnowledgeUploadResult:
    business: BusinessProfile
    session: RegistrationSession
    uploads: list[KnowledgeUpload]


def start_registration(*, first_name: str, email: str, password: str) -> RegistrationResult:
    """Create a user and onboarding session for the registration wizard."""
    normalized_first_name = first_name.strip()
    if not normalized_first_name:
        raise RegistrationError("First name is required.")

    normalized_email = User.objects.normalize_email(email).strip()
    if not normalized_email:
        raise RegistrationError("Valid email is required.")

    with transaction.atomic():
        if User.objects.filter(email__iexact=normalized_email).exists():
            raise EmailAlreadyRegistered("This email address is already registered.")

        user = User.objects.create_user(
            email=normalized_email,
            password=password,
            first_name=normalized_first_name,
            status="pending",
        )

        session = RegistrationSession.objects.create(
            user=user,
            current_step="form",
            steps_completed=1,
            total_steps=4,
            last_activity_at=timezone.now(),
        )

    return RegistrationResult(user=user, session=session)


def _sanitize_list(values: Iterable[str] | None) -> list[str]:
    if not values:
        return []
    unique = []
    for value in values:
        if not isinstance(value, str):
            continue
        trimmed = value.strip()
        if trimmed and trimmed not in unique:
            unique.append(trimmed)
        if len(unique) >= 32:
            break
    return unique


def upsert_business_profile(
    *,
    session_id: str,
    name: str,
    industry: str,
    industry_key: str | None = None,
    line_of_business: Iterable[str] | None = None,
    line_of_business_custom: Iterable[str] | None = None,
    country: str | None = None,
    website: str | None = None,
) -> BusinessProfileResult:
    """Create or update the business profile linked to a registration session."""
    business_name = (name or "").strip()
    if len(business_name) < 2:
        raise BusinessProfileError("Business name must be at least 2 characters.")

    industry_value = (industry or "").strip()
    if not industry_value:
        raise BusinessProfileError("Select an industry to continue.")

    normalized_country = (country or "").strip()
    normalized_industry_key = (industry_key or "").strip()
    sanitized_lob = _sanitize_list(line_of_business)
    sanitized_lob_custom = _sanitize_list(line_of_business_custom)

    normalized_website = (website or "").strip()
    if normalized_website:
        validator = URLValidator()
        try:
            validator(normalized_website)
        except ValidationError as exc:
            raise BusinessProfileError("Enter a valid website URL.") from exc

    with transaction.atomic():
        try:
            session = (
                RegistrationSession.objects.select_for_update()
                .select_related("user")
                .get(id=session_id)
            )
        except RegistrationSession.DoesNotExist as exc:  # pragma: no cover - defensive
            raise BusinessProfileError("Registration session not found.") from exc

        profile_defaults = {
            "user": session.user,
            "name": business_name,
            "industry": industry_value,
            "industry_key": normalized_industry_key,
            "line_of_business": sanitized_lob,
            "line_of_business_custom": sanitized_lob_custom,
            "country": normalized_country,
            "website": normalized_website,
            "status": "pending",
        }

        profile, created = BusinessProfile.objects.select_for_update().get_or_create(
            registration_session=session,
            defaults=profile_defaults,
        )
        if not created:
            profile.name = business_name
            profile.industry = industry_value
            profile.industry_key = normalized_industry_key
            profile.line_of_business = sanitized_lob
            profile.line_of_business_custom = sanitized_lob_custom
            profile.country = normalized_country
            profile.website = normalized_website
            if profile.status == "draft":
                profile.status = "pending"
            profile.save()

        session.current_step = "business"
        session.steps_completed = max(session.steps_completed, 2)
        session.last_activity_at = timezone.now()
        session.save(update_fields=["current_step", "steps_completed", "last_activity_at", "updated_at"])

    return BusinessProfileResult(profile=profile, session=session)


def configure_agent_profile(
    *,
    business_id: str,
    name: str,
    role: str | None = None,
    tone: str | None = None,
    traits: Iterable[str] | None = None,
    escalation_rule: str | None = None,
) -> AgentProfileResult:
    """Create or update the agent profile for a business."""
    agent_name = (name or "").strip()
    if len(agent_name) < 2:
        raise AgentProfileError("Agent name must be at least 2 characters.")

    sanitized_traits = _sanitize_list(traits)[:12]

    with transaction.atomic():
        try:
            business = (
                BusinessProfile.objects.select_for_update()
                .select_related("user", "registration_session")
                .get(id=business_id)
            )
        except BusinessProfile.DoesNotExist as exc:  # pragma: no cover - defensive safety
            raise AgentProfileError("Business profile not found.") from exc

        session = business.registration_session
        session.current_step = "agent"
        session.steps_completed = max(session.steps_completed, 3)
        session.last_activity_at = timezone.now()
        session.save(update_fields=["current_step", "steps_completed", "last_activity_at", "updated_at"])

        defaults = {
            "user": business.user,
            "name": agent_name,
            "role": (role or "").strip(),
            "tone": (tone or "").strip(),
            "traits": sanitized_traits,
            "escalation_rule": (escalation_rule or "").strip(),
            "status": "review",
        }

        profile, created = AgentProfile.objects.select_for_update().get_or_create(
            business_profile=business,
            defaults=defaults,
        )
        if not created:
            profile.name = agent_name
            profile.role = defaults["role"]
            profile.tone = defaults["tone"]
            profile.traits = sanitized_traits
            profile.escalation_rule = defaults["escalation_rule"]
            if profile.status == "draft":
                profile.status = "review"
            profile.save()

    return AgentProfileResult(profile=profile, session=session)


def _normalize_resource_type(value: str) -> str:
    normalized = (value or "").strip().lower()
    if not normalized:
        return ""
    return normalized.replace(" ", "_")


def _display_resource_label(resource_type: str) -> str:
    label = resource_type.replace("_", " ").strip()
    if not label:
        return "resource"
    return label.title()


def _build_upload_slug(resource_type: str, url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    base = slugify(f"{resource_type}-{digest}")
    return base[:160]


def _infer_source_name(url: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.netloc or ""
    if hostname:
        return hostname[:255]
    path = parsed.path.strip("/") or url
    return path[:255]


def finalize_knowledge_uploads(
    *,
    business_id: str,
    selected_types: Iterable[str] | None = None,
    link_map: Mapping[str, Iterable[str]] | None = None,
    skip: bool = False,
) -> KnowledgeUploadResult:
    """
    Persist knowledge resources for registration step four.

    Fast path validations happen up front to keep transactions lean. All writes
    execute inside a single atomic block so the session and uploads stay
    consistent even under concurrent submissions.
    """

    skip_processing = bool(skip)

    raw_types = _sanitize_list(selected_types) if not skip_processing else []
    normalized_types: list[str] = []
    for value in raw_types:
        normalized = _normalize_resource_type(value)
        if normalized and normalized not in normalized_types:
            normalized_types.append(normalized)

    if not normalized_types and not skip_processing:
        raise KnowledgeUploadError(
            "Select at least one knowledge source to continue.",
            field="materials",
        )

    validator = URLValidator()
    sanitized_links: dict[str, list[str]] = {}

    link_map = link_map or {}
    normalized_link_map: dict[str, Iterable[str]] = {}
    if not skip_processing:
        for key, values in link_map.items():
            normalized_key = _normalize_resource_type(str(key))
            if not normalized_key:
                continue
            normalized_link_map[normalized_key] = values

        total_links = 0
        for resource_type in normalized_types:
            options = normalized_link_map.get(resource_type, [])
            sanitized: list[str] = []
            display_label = _display_resource_label(resource_type)
            for raw_value in options or []:
                if not isinstance(raw_value, str):
                    continue
                candidate = raw_value.strip()
                if not candidate:
                    continue
                try:
                    validator(candidate)
                except ValidationError as exc:
                    raise KnowledgeUploadError(
                        f"Enter a valid URL for {display_label}.",
                        field=resource_type,
                    ) from exc
                if candidate in sanitized:
                    continue
                sanitized.append(candidate)
                if len(sanitized) >= 20:
                    break
            if not sanitized:
                raise KnowledgeUploadError(
                    f"Add at least one link for {display_label}.",
                    field=resource_type,
                )
            sanitized_links[resource_type] = sanitized
            total_links += len(sanitized)

        if not sanitized_links or total_links == 0:
            raise KnowledgeUploadError(
                "Add at least one link for the selected knowledge sources.",
                field="materials",
            )

    with transaction.atomic():
        try:
            business = (
                BusinessProfile.objects.select_for_update()
                .select_related("user", "registration_session", "agent_profile")
                .get(id=business_id)
            )
        except BusinessProfile.DoesNotExist as exc:  # pragma: no cover - defensive path
            raise KnowledgeUploadError("Business profile not found.") from exc

        session = business.registration_session
        user = business.user

        existing_uploads = {
            (upload.resource_type, upload.url): upload
            for upload in KnowledgeUpload.objects.select_for_update().filter(
                business_profile=business
            )
        }

        uploads: list[KnowledgeUpload] = []

        if skip_processing:
            uploads = list(existing_uploads.values())
        else:
            for resource_type, urls in sanitized_links.items():
                for url in urls:
                    key = (resource_type, url)
                    upload = existing_uploads.pop(key, None)
                    slug = _build_upload_slug(resource_type, url)
                    source_name = _infer_source_name(url)
                    if upload:
                        fields_to_update: list[str] = []
                        if upload.slug != slug:
                            upload.slug = slug
                            fields_to_update.append("slug")
                        if upload.source_name != source_name:
                            upload.source_name = source_name
                            fields_to_update.append("source_name")
                        if upload.status != "pending":
                            upload.status = "pending"
                            fields_to_update.append("status")
                        if fields_to_update:
                            upload.save(update_fields=fields_to_update + ["updated_at"])
                    else:
                        upload = KnowledgeUpload.objects.create(
                            business_profile=business,
                            user=user,
                            resource_type=resource_type,
                            url=url,
                            metadata={"submitted_via": "registration"},
                            source_name=source_name,
                            slug=slug,
                            status="pending",
                        )
                    uploads.append(upload)

            # Remove uploads no longer selected to keep sources in sync.
            if existing_uploads:
                KnowledgeUpload.objects.filter(
                    id__in=[upload.id for upload in existing_uploads.values()]
                ).delete()

        # Update registration session markers.
        session.current_step = "uploads"
        session.steps_completed = max(session.steps_completed, 4)
        session.is_complete = True
        session.last_activity_at = timezone.now()
        session.save(
            update_fields=["current_step", "steps_completed", "is_complete", "last_activity_at", "updated_at"]
        )

        if user.status != "active":
            user.status = "active"
            user.save(update_fields=["status", "updated_at"])

        if business.status != "active":
            business.status = "active"
            business.save(update_fields=["status", "updated_at"])

        agent_profile = getattr(business, "agent_profile", None)
        if agent_profile and agent_profile.status != "active":
            agent_profile.status = "active"
            agent_profile.save(update_fields=["status", "updated_at"])

    uploads.sort(key=lambda item: (item.resource_type, item.created_at))
    return KnowledgeUploadResult(business=business, session=session, uploads=uploads)
