from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.accounts.models import BusinessProfile, User
from apps.crm.models import (
    CrmActivity,
    CrmActivityActor,
    CrmCompany,
    CrmCompanyStatus,
    CrmContact,
    CrmContactCompanyLink,
    CrmContactStatus,
    CrmDuplicateSuggestion,
    CrmFieldDefinition,
    CrmFieldTarget,
    CrmFieldValue,
)
from apps.crm.models.custom_fields import coerce_field_value


@dataclass(frozen=True)
class PaginatedResult:
    items: Sequence[Any]
    total: int

def _upsert_field_values(
    *,
    business_profile: BusinessProfile,
    contact: CrmContact | None,
    company: CrmCompany | None,
    values: dict[str, Any] | None,
    require_all_required: bool = False,
) -> None:
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ValidationError({"custom_fields": "Custom fields must be a JSON object."})
    if not contact and not company:
        return
    target = CrmFieldTarget.CONTACT if contact else CrmFieldTarget.COMPANY
    definitions = {
        item.key: item
        for item in CrmFieldDefinition.objects.filter(business_profile=business_profile, target_object=target, archived=False)
    }
    unknown_keys = sorted(str(key).strip() for key in values.keys() if str(key).strip() not in definitions)
    if unknown_keys:
        raise ValidationError({"custom_fields": f"Unknown custom fields: {', '.join(unknown_keys)}"})
    existing_values = {}
    if contact:
        existing_values = {
            item.field_definition.key: item.value_json
            for item in CrmFieldValue.objects.filter(business_profile=business_profile, contact=contact).select_related("field_definition")
        }
    elif company:
        existing_values = {
            item.field_definition.key: item.value_json
            for item in CrmFieldValue.objects.filter(business_profile=business_profile, company=company).select_related("field_definition")
        }

    for key, value in values.items():
        definition = definitions.get(str(key).strip())
        if not definition:
            continue
        coerced_value = coerce_field_value(definition, value)
        field_value, _created = CrmFieldValue.objects.get_or_create(business_profile=business_profile, field_definition=definition, contact=contact, company=company)
        field_value.value_json = coerced_value
        field_value.full_clean()
        field_value.save()
        existing_values[definition.key] = coerced_value

    if require_all_required:
        missing_required = [
            definition.label
            for definition in definitions.values()
            if definition.required and existing_values.get(definition.key) in (None, "", [], {})
        ]
        if missing_required:
            raise ValidationError({"custom_fields": f"Missing required custom fields: {', '.join(sorted(missing_required))}"})


def _normalize_contact_payload(payload: dict[str, Any], existing: CrmContact | None = None) -> dict[str, Any]:
    first_name = _clean_text(payload.get("first_name"), fallback=existing.first_name if existing else "")
    last_name = _clean_text(payload.get("last_name"), fallback=existing.last_name if existing else "")
    primary_email = _clean_text(payload.get("primary_email"), fallback=existing.primary_email if existing else "")
    primary_phone = _clean_text(payload.get("primary_phone"), fallback=existing.primary_phone if existing else "")
    display_name = _clean_text(payload.get("display_name"), fallback=existing.display_name if existing else "")
    if not display_name:
        display_name = " ".join(part for part in (first_name, last_name) if part).strip()
    if not display_name:
        display_name = primary_email or primary_phone
    if not display_name:
        raise ValidationError({"display_name": "Contact requires a display name, email, or phone."})
    return {
        "owner": payload.get("owner") if isinstance(payload.get("owner"), User) else (existing.owner if existing else None),
        "display_name": display_name,
        "first_name": first_name,
        "last_name": last_name,
        "primary_email": primary_email,
        "primary_phone": primary_phone,
        "title": _clean_text(payload.get("title"), fallback=existing.title if existing else ""),
        "source": _clean_text(payload.get("source"), fallback=existing.source if existing else "manual") or "manual",
        "tags": _normalize_tags(payload.get("tags"), fallback=existing.tags if existing else []),
        "metadata": _normalize_metadata(payload.get("metadata"), fallback=existing.metadata if existing else {}),
    }


def _normalize_company_payload(payload: dict[str, Any], existing: CrmCompany | None = None) -> dict[str, Any]:
    website = _clean_text(payload.get("website"), fallback=existing.website if existing else "")
    name = _clean_text(payload.get("name"), fallback=existing.name if existing else "")
    if not name and website:
        name = _derive_company_name_from_website(website)
    if not name:
        raise ValidationError({"name": "Company requires a name or website."})
    return {
        "owner": payload.get("owner") if isinstance(payload.get("owner"), User) else (existing.owner if existing else None),
        "name": name,
        "website": website,
        "primary_phone": _clean_text(payload.get("primary_phone"), fallback=existing.primary_phone if existing else ""),
        "source": _clean_text(payload.get("source"), fallback=existing.source if existing else "manual") or "manual",
        "tags": _normalize_tags(payload.get("tags"), fallback=existing.tags if existing else []),
        "metadata": _normalize_metadata(payload.get("metadata"), fallback=existing.metadata if existing else {}),
    }


def _clean_text(value: Any, *, fallback: str = "") -> str:
    if value is None:
        return fallback
    return str(value).strip()


def _normalize_tags(value: Any, *, fallback: Sequence[Any]) -> list[str]:
    if value is None:
        value = fallback
    if not isinstance(value, (list, tuple)):
        raise ValidationError({"tags": "Tags must be a list."})
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_metadata(value: Any, *, fallback: dict[str, Any]) -> dict[str, Any]:
    if value is None:
        return fallback
    if not isinstance(value, dict):
        raise ValidationError({"metadata": "Metadata must be a JSON object."})
    return value


def _derive_company_name_from_website(website: str) -> str:
    parsed = urlparse(website if "://" in website else f"https://{website}")
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise ValidationError({"website": "Website must be a valid URL."})
    return hostname.removeprefix("www.")


def _validate_merge_records(*, business_profile: BusinessProfile, survivor: CrmContact | CrmCompany, merged: CrmContact | CrmCompany) -> None:
    if survivor.business_profile_id != business_profile.id or merged.business_profile_id != business_profile.id:
        raise ValidationError("CRM merge requires records from the same business.")
    if survivor.id == merged.id:
        raise ValidationError("Cannot merge a record into itself.")
    if survivor.status in {CrmContactStatus.MERGED, CrmCompanyStatus.MERGED}:
        raise ValidationError("Survivor record cannot already be merged.")
    if merged.status in {CrmContactStatus.MERGED, CrmCompanyStatus.MERGED}:
        raise ValidationError("Merged record is already merged.")


def _validate_duplicate_suggestion_access(*, business_profile: BusinessProfile, suggestion: CrmDuplicateSuggestion) -> None:
    if suggestion.business_profile_id != business_profile.id:
        raise ValidationError("Duplicate suggestion belongs to a different business.")


def _validate_link_records(*, business_profile: BusinessProfile, contact: CrmContact, company: CrmCompany) -> None:
    if contact.business_profile_id != business_profile.id or company.business_profile_id != business_profile.id:
        raise ValidationError("Contact and company must belong to the same business.")


def _apply_contact_company_link_payload(*, link: CrmContactCompanyLink, payload: dict[str, Any], default_primary: bool) -> None:
    relationship_title = payload.get("relationship_title", payload.get("relationshipTitle"))
    if "relationship_title" in payload or "relationshipTitle" in payload:
        link.relationship_title = _clean_text(relationship_title)
    if "metadata" in payload:
        link.metadata = _normalize_metadata(payload.get("metadata"), fallback=link.metadata or {})
    if "started_at" in payload or "startedAt" in payload:
        link.started_at = _parse_optional_date(payload.get("started_at", payload.get("startedAt")), field_name="started_at")
    if "ended_at" in payload or "endedAt" in payload:
        link.ended_at = _parse_optional_date(payload.get("ended_at", payload.get("endedAt")), field_name="ended_at")
    if link.started_at and link.ended_at and link.ended_at < link.started_at:
        raise ValidationError({"ended_at": "ended_at must be on or after started_at."})
    if "is_primary" in payload or "isPrimary" in payload:
        link.is_primary = bool(payload.get("is_primary", payload.get("isPrimary")))
    elif link.pk is None:
        link.is_primary = default_primary


def _unset_other_primary_links(link: CrmContactCompanyLink) -> None:
    CrmContactCompanyLink.objects.filter(contact=link.contact, business_profile=link.business_profile, is_primary=True).exclude(id=link.id).update(
        is_primary=False,
        updated_at=timezone.now(),
    )


def _parse_optional_date(value: Any, *, field_name: str):
    if value in (None, ""):
        return None
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value
    parsed = parse_date(str(value).strip())
    if parsed is None:
        raise ValidationError({field_name: "Must be a valid ISO date."})
    return parsed


def _log_activity(business_profile: BusinessProfile, actor: User | None, activity_type: str, summary: str, *, contact: CrmContact | None = None, company: CrmCompany | None = None, detail: str = "") -> None:
    CrmActivity.objects.create(
        business_profile=business_profile,
        contact=contact,
        company=company,
        actor_user=actor,
        actor_type=CrmActivityActor.USER if actor else CrmActivityActor.SYSTEM,
        activity_type=activity_type,
        summary=summary,
        detail=detail,
    )


def log_crm_activity(
    *,
    business_profile: BusinessProfile,
    actor: User | None,
    activity_type: str,
    summary: str,
    contact: CrmContact | None = None,
    company: CrmCompany | None = None,
    detail: str = "",
    actor_type: str | None = None,
) -> None:
    CrmActivity.objects.create(
        business_profile=business_profile,
        contact=contact,
        company=company,
        actor_user=actor,
        actor_type=actor_type or (CrmActivityActor.USER if actor else CrmActivityActor.SYSTEM),
        activity_type=activity_type,
        summary=summary,
        detail=detail,
    )
