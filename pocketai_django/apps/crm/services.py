from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence
import uuid
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.accounts.models import BusinessProfile, User
from .models import (
    CrmActivity,
    CrmActivityActor,
    CrmActivityType,
    CrmCompany,
    CrmCompanyStatus,
    CrmContact,
    CrmContactCompanyLink,
    CrmContactStatus,
    CrmDuplicateSuggestion,
    CrmDuplicateSuggestionStatus,
    CrmExternalIdentity,
    CrmFieldDefinition,
    CrmFieldTarget,
    CrmFieldValue,
    CrmMergeEvent,
    CrmNote,
    CrmRecordType,
)
from .models.custom_fields import coerce_field_value


@dataclass(frozen=True)
class PaginatedResult:
    items: Sequence[Any]
    total: int


def list_contacts(*, business_profile: BusinessProfile, search: str = "", status: str = "", limit: int = 50) -> PaginatedResult:
    qs = CrmContact.objects.filter(business_profile=business_profile).prefetch_related("company_links__company")
    if status:
        qs = qs.filter(status=status)
    if search.strip():
        text = search.strip()
        qs = qs.filter(
            Q(display_name__icontains=text)
            | Q(primary_email__icontains=text)
            | Q(primary_phone__icontains=text)
            | Q(company_links__company__name__icontains=text)
        ).distinct()
    total = qs.count()
    return PaginatedResult(items=tuple(qs.order_by("display_name")[: max(1, limit)]), total=total)


def list_companies(*, business_profile: BusinessProfile, search: str = "", status: str = "", limit: int = 50) -> PaginatedResult:
    qs = CrmCompany.objects.filter(business_profile=business_profile)
    if status:
        qs = qs.filter(status=status)
    if search.strip():
        text = search.strip()
        qs = qs.filter(Q(name__icontains=text) | Q(website__icontains=text) | Q(primary_phone__icontains=text))
    total = qs.count()
    return PaginatedResult(items=tuple(qs.order_by("name")[: max(1, limit)]), total=total)


def get_contact(*, business_profile: BusinessProfile, contact_id: uuid.UUID) -> CrmContact:
    return (
        CrmContact.objects.filter(business_profile=business_profile, id=contact_id)
        .prefetch_related("company_links__company", "field_values__field_definition", "notes", "activities", "external_identities")
        .get()
    )


def get_company(*, business_profile: BusinessProfile, company_id: uuid.UUID) -> CrmCompany:
    return (
        CrmCompany.objects.filter(business_profile=business_profile, id=company_id)
        .prefetch_related("contact_links__contact", "field_values__field_definition", "notes", "activities", "external_identities")
        .get()
    )


@transaction.atomic
def create_contact(*, business_profile: BusinessProfile, actor: User | None, payload: dict[str, Any]) -> CrmContact:
    normalized_payload = _normalize_contact_payload(payload)
    contact = CrmContact(
        business_profile=business_profile,
        owner=normalized_payload["owner"],
        display_name=normalized_payload["display_name"],
        first_name=normalized_payload["first_name"],
        last_name=normalized_payload["last_name"],
        primary_email=normalized_payload["primary_email"],
        primary_phone=normalized_payload["primary_phone"],
        title=normalized_payload["title"],
        source=normalized_payload["source"],
        tags=normalized_payload["tags"],
        metadata=normalized_payload["metadata"],
    )
    contact.full_clean()
    contact.save()
    _upsert_field_values(
        business_profile=business_profile,
        contact=contact,
        company=None,
        values=payload.get("custom_fields") or {},
        require_all_required=True,
    )
    _log_activity(business_profile, actor, CrmActivityType.CREATED, "Contact created", contact=contact)
    return contact


@transaction.atomic
def create_company(*, business_profile: BusinessProfile, actor: User | None, payload: dict[str, Any]) -> CrmCompany:
    normalized_payload = _normalize_company_payload(payload)
    company = CrmCompany(
        business_profile=business_profile,
        owner=normalized_payload["owner"],
        name=normalized_payload["name"],
        website=normalized_payload["website"],
        primary_phone=normalized_payload["primary_phone"],
        source=normalized_payload["source"],
        tags=normalized_payload["tags"],
        metadata=normalized_payload["metadata"],
    )
    company.full_clean()
    company.save()
    _upsert_field_values(
        business_profile=business_profile,
        contact=None,
        company=company,
        values=payload.get("custom_fields") or {},
        require_all_required=True,
    )
    _log_activity(business_profile, actor, CrmActivityType.CREATED, "Company created", company=company)
    return company


@transaction.atomic
def update_contact(*, business_profile: BusinessProfile, actor: User | None, contact: CrmContact, payload: dict[str, Any]) -> CrmContact:
    normalized_payload = _normalize_contact_payload(payload, existing=contact)
    for field in ("display_name", "first_name", "last_name", "primary_email", "primary_phone", "title", "source", "owner", "tags", "metadata"):
        if field in normalized_payload:
            setattr(contact, field, normalized_payload[field])
    contact.full_clean()
    contact.save()
    _upsert_field_values(
        business_profile=business_profile,
        contact=contact,
        company=None,
        values=payload.get("custom_fields"),
        require_all_required="custom_fields" in payload,
    )
    _log_activity(business_profile, actor, CrmActivityType.UPDATED, "Contact updated", contact=contact)
    return contact


@transaction.atomic
def update_company(*, business_profile: BusinessProfile, actor: User | None, company: CrmCompany, payload: dict[str, Any]) -> CrmCompany:
    normalized_payload = _normalize_company_payload(payload, existing=company)
    for field in ("name", "website", "primary_phone", "source", "owner", "tags", "metadata"):
        if field in normalized_payload:
            setattr(company, field, normalized_payload[field])
    company.full_clean()
    company.save()
    _upsert_field_values(
        business_profile=business_profile,
        contact=None,
        company=company,
        values=payload.get("custom_fields"),
        require_all_required="custom_fields" in payload,
    )
    _log_activity(business_profile, actor, CrmActivityType.UPDATED, "Company updated", company=company)
    return company


def archive_contact(*, business_profile: BusinessProfile, actor: User | None, contact: CrmContact) -> CrmContact:
    if contact.status == CrmContactStatus.ARCHIVED:
        return contact
    contact.status = CrmContactStatus.ARCHIVED
    contact.archived_at = timezone.now()
    contact.save(update_fields=["status", "archived_at", "updated_at"])
    _log_activity(business_profile, actor, CrmActivityType.ARCHIVED, "Contact archived", contact=contact)
    return contact


def archive_company(*, business_profile: BusinessProfile, actor: User | None, company: CrmCompany) -> CrmCompany:
    if company.status == CrmCompanyStatus.ARCHIVED:
        return company
    company.status = CrmCompanyStatus.ARCHIVED
    company.archived_at = timezone.now()
    company.save(update_fields=["status", "archived_at", "updated_at"])
    _log_activity(business_profile, actor, CrmActivityType.ARCHIVED, "Company archived", company=company)
    return company


def restore_contact(*, business_profile: BusinessProfile, actor: User | None, contact: CrmContact) -> CrmContact:
    contact.status = CrmContactStatus.ACTIVE
    contact.archived_at = None
    contact.save(update_fields=["status", "archived_at", "updated_at"])
    _log_activity(business_profile, actor, CrmActivityType.RESTORED, "Contact restored", contact=contact)
    return contact


def restore_company(*, business_profile: BusinessProfile, actor: User | None, company: CrmCompany) -> CrmCompany:
    company.status = CrmCompanyStatus.ACTIVE
    company.archived_at = None
    company.save(update_fields=["status", "archived_at", "updated_at"])
    _log_activity(business_profile, actor, CrmActivityType.RESTORED, "Company restored", company=company)
    return company


def delete_contact(*, contact: CrmContact, actor: User | None = None) -> None:
    contact_id = contact.id
    business_profile = contact.business_profile
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.OTHER,
        "Contact hard deleted",
        detail=f"record={contact_id}",
    )
    contact.delete()


def delete_company(*, company: CrmCompany, actor: User | None = None) -> None:
    company_id = company.id
    business_profile = company.business_profile
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.OTHER,
        "Company hard deleted",
        detail=f"record={company_id}",
    )
    company.delete()


@transaction.atomic
def add_note(*, business_profile: BusinessProfile, actor: User | None, body: str, contact: CrmContact | None = None, company: CrmCompany | None = None) -> CrmNote:
    note = CrmNote.objects.create(business_profile=business_profile, contact=contact, company=company, author=actor, body=body.strip())
    _log_activity(business_profile, actor, CrmActivityType.NOTE_ADDED, "Note added", contact=contact, company=company)
    return note


@transaction.atomic
def merge_contacts(*, business_profile: BusinessProfile, actor: User | None, survivor: CrmContact, merged: CrmContact) -> CrmContact:
    _validate_merge_records(business_profile=business_profile, survivor=survivor, merged=merged)
    if survivor.id == merged.id:
        return survivor
    CrmExternalIdentity.objects.filter(contact=merged).update(contact=survivor, updated_at=timezone.now())
    CrmNote.objects.filter(contact=merged).update(contact=survivor, updated_at=timezone.now())
    CrmActivity.objects.filter(contact=merged).update(contact=survivor)
    CrmFieldValue.objects.filter(contact=merged).exclude(field_definition__in=survivor.field_values.values("field_definition")).update(contact=survivor, updated_at=timezone.now())
    merged.status = CrmContactStatus.MERGED
    merged.save(update_fields=["status", "updated_at"])
    CrmMergeEvent.objects.create(business_profile=business_profile, record_type=CrmRecordType.CONTACT, survivor_record_id=survivor.id, merged_record_id=merged.id, performed_by=actor)
    CrmDuplicateSuggestion.objects.filter(business_profile=business_profile, record_type=CrmRecordType.CONTACT).filter(Q(record_id=merged.id) | Q(candidate_record_id=merged.id)).update(status=CrmDuplicateSuggestionStatus.MERGED, resolved_at=timezone.now())
    _log_activity(business_profile, actor, CrmActivityType.MERGED, "Contact merged", contact=survivor, detail=f"Merged record {merged.id}")
    return survivor


@transaction.atomic
def merge_companies(*, business_profile: BusinessProfile, actor: User | None, survivor: CrmCompany, merged: CrmCompany) -> CrmCompany:
    _validate_merge_records(business_profile=business_profile, survivor=survivor, merged=merged)
    if survivor.id == merged.id:
        return survivor
    CrmExternalIdentity.objects.filter(company=merged).update(company=survivor, updated_at=timezone.now())
    CrmNote.objects.filter(company=merged).update(company=survivor, updated_at=timezone.now())
    CrmActivity.objects.filter(company=merged).update(company=survivor)
    CrmFieldValue.objects.filter(company=merged).exclude(field_definition__in=survivor.field_values.values("field_definition")).update(company=survivor, updated_at=timezone.now())
    CrmContactCompanyLink.objects.filter(company=merged).update(company=survivor, updated_at=timezone.now())
    merged.status = CrmCompanyStatus.MERGED
    merged.save(update_fields=["status", "updated_at"])
    CrmMergeEvent.objects.create(business_profile=business_profile, record_type=CrmRecordType.COMPANY, survivor_record_id=survivor.id, merged_record_id=merged.id, performed_by=actor)
    CrmDuplicateSuggestion.objects.filter(business_profile=business_profile, record_type=CrmRecordType.COMPANY).filter(Q(record_id=merged.id) | Q(candidate_record_id=merged.id)).update(status=CrmDuplicateSuggestionStatus.MERGED, resolved_at=timezone.now())
    _log_activity(business_profile, actor, CrmActivityType.MERGED, "Company merged", company=survivor, detail=f"Merged record {merged.id}")
    return survivor


def list_field_definitions(*, business_profile: BusinessProfile, target_object: str | None = None) -> Sequence[CrmFieldDefinition]:
    qs = CrmFieldDefinition.objects.filter(business_profile=business_profile, archived=False)
    if target_object:
        qs = qs.filter(target_object=target_object)
    return tuple(qs.order_by("label"))


def get_field_definition(*, business_profile: BusinessProfile, field_definition_id: uuid.UUID) -> CrmFieldDefinition:
    return CrmFieldDefinition.objects.get(business_profile=business_profile, id=field_definition_id)


def upsert_field_definition(*, business_profile: BusinessProfile, payload: dict[str, Any], actor: User | None = None) -> CrmFieldDefinition:
    target_object = payload.get("target_object") or payload.get("targetObject") or CrmFieldTarget.CONTACT
    key = payload.get("key") or payload.get("label") or ""
    definition, created = CrmFieldDefinition.objects.get_or_create(
        business_profile=business_profile,
        target_object=target_object,
        key=key,
        defaults={"label": str(payload.get("label") or key).strip(), "field_type": payload.get("field_type") or payload.get("fieldType") or ""},
    )
    definition.label = str(payload.get("label") or key).strip()
    definition.field_type = payload.get("field_type") or payload.get("fieldType")
    definition.required = bool(payload.get("required"))
    definition.searchable = bool(payload.get("searchable"))
    definition.filterable = bool(payload.get("filterable"))
    definition.pii = bool(payload.get("pii"))
    definition.options = list(payload.get("options") or [])
    definition.schema = payload.get("schema") if isinstance(payload.get("schema"), dict) else {}
    definition.full_clean()
    definition.save()
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.OTHER,
        "Field definition created" if created else "Field definition updated",
        detail=f"{definition.target_object}:{definition.key}",
    )
    return definition


def update_field_definition(*, business_profile: BusinessProfile, definition: CrmFieldDefinition, payload: dict[str, Any], actor: User | None = None) -> CrmFieldDefinition:
    if definition.business_profile_id != business_profile.id:
        raise ValidationError("Field definition belongs to a different business.")
    target_object = payload.get("target_object") or payload.get("targetObject")
    field_type = payload.get("field_type") or payload.get("fieldType")
    if target_object:
        definition.target_object = target_object
    if field_type:
        definition.field_type = field_type
    if "key" in payload:
        definition.key = str(payload.get("key") or "").strip()
    if "label" in payload:
        definition.label = str(payload.get("label") or "").strip()
    for flag in ("required", "searchable", "filterable", "pii"):
        if flag in payload:
            setattr(definition, flag, bool(payload.get(flag)))
    if "options" in payload:
        definition.options = list(payload.get("options") or [])
    if "schema" in payload:
        definition.schema = payload.get("schema") if isinstance(payload.get("schema"), dict) else {}
    definition.full_clean()
    definition.save()
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.OTHER,
        "Field definition updated",
        detail=f"{definition.target_object}:{definition.key}",
    )
    return definition


def archive_field_definition(*, business_profile: BusinessProfile, actor: User | None, definition: CrmFieldDefinition) -> CrmFieldDefinition:
    if definition.business_profile_id != business_profile.id:
        raise ValidationError("Field definition belongs to a different business.")
    if definition.archived:
        return definition
    definition.archived = True
    definition.save(update_fields=["archived", "updated_at"])
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.OTHER,
        "Field definition archived",
        detail=f"{definition.target_object}:{definition.key}",
    )
    return definition


def restore_field_definition(*, business_profile: BusinessProfile, actor: User | None, definition: CrmFieldDefinition) -> CrmFieldDefinition:
    if definition.business_profile_id != business_profile.id:
        raise ValidationError("Field definition belongs to a different business.")
    if not definition.archived:
        return definition
    definition.archived = False
    definition.full_clean()
    definition.save(update_fields=["archived", "updated_at"])
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.OTHER,
        "Field definition restored",
        detail=f"{definition.target_object}:{definition.key}",
    )
    return definition


def list_duplicate_suggestions(*, business_profile: BusinessProfile, status: str | None = None, limit: int = 100) -> Sequence[CrmDuplicateSuggestion]:
    qs = CrmDuplicateSuggestion.objects.filter(business_profile=business_profile)
    if status:
        qs = qs.filter(status=status)
    return tuple(qs.order_by("-created_at")[: max(1, limit)])


def get_duplicate_suggestion(*, business_profile: BusinessProfile, suggestion_id: uuid.UUID) -> CrmDuplicateSuggestion:
    return CrmDuplicateSuggestion.objects.get(business_profile=business_profile, id=suggestion_id)


@transaction.atomic
def ignore_duplicate_suggestion(
    *,
    business_profile: BusinessProfile,
    actor: User | None,
    suggestion: CrmDuplicateSuggestion,
    resolution_note: str = "",
) -> CrmDuplicateSuggestion:
    _validate_duplicate_suggestion_access(business_profile=business_profile, suggestion=suggestion)
    suggestion.status = CrmDuplicateSuggestionStatus.IGNORED
    suggestion.resolution_note = resolution_note.strip()
    suggestion.resolved_at = timezone.now()
    suggestion.save(update_fields=["status", "resolution_note", "resolved_at"])
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.OTHER,
        "Duplicate suggestion ignored",
        detail=f"suggestion={suggestion.id}",
    )
    return suggestion


@transaction.atomic
def reopen_duplicate_suggestion(*, business_profile: BusinessProfile, actor: User | None, suggestion: CrmDuplicateSuggestion) -> CrmDuplicateSuggestion:
    _validate_duplicate_suggestion_access(business_profile=business_profile, suggestion=suggestion)
    suggestion.status = CrmDuplicateSuggestionStatus.OPEN
    suggestion.resolution_note = ""
    suggestion.resolved_at = None
    suggestion.save(update_fields=["status", "resolution_note", "resolved_at"])
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.OTHER,
        "Duplicate suggestion reopened",
        detail=f"suggestion={suggestion.id}",
    )
    return suggestion


@transaction.atomic
def merge_duplicate_suggestion(
    *,
    business_profile: BusinessProfile,
    actor: User | None,
    suggestion: CrmDuplicateSuggestion,
    merged_record_id: uuid.UUID | None = None,
    resolution_note: str = "",
) -> CrmDuplicateSuggestion:
    _validate_duplicate_suggestion_access(business_profile=business_profile, suggestion=suggestion)
    resolved_merged_id = merged_record_id or suggestion.record_id
    if resolved_merged_id is None:
        raise ValidationError({"recordId": "A mergeable record is required to resolve this duplicate."})
    if suggestion.record_type == CrmRecordType.CONTACT:
        survivor = get_contact(business_profile=business_profile, contact_id=suggestion.candidate_record_id)
        merged = get_contact(business_profile=business_profile, contact_id=resolved_merged_id)
        merge_contacts(business_profile=business_profile, actor=actor, survivor=survivor, merged=merged)
    else:
        survivor = get_company(business_profile=business_profile, company_id=suggestion.candidate_record_id)
        merged = get_company(business_profile=business_profile, company_id=resolved_merged_id)
        merge_companies(business_profile=business_profile, actor=actor, survivor=survivor, merged=merged)
    suggestion.status = CrmDuplicateSuggestionStatus.MERGED
    suggestion.resolution_note = resolution_note.strip()
    suggestion.resolved_at = timezone.now()
    suggestion.record_id = resolved_merged_id
    suggestion.save(update_fields=["status", "resolution_note", "resolved_at", "record_id"])
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.OTHER,
        "Duplicate suggestion merged",
        detail=f"suggestion={suggestion.id}",
    )
    return suggestion


@transaction.atomic
def link_contact_to_company(
    *,
    business_profile: BusinessProfile,
    actor: User | None,
    contact: CrmContact,
    company: CrmCompany,
    payload: dict[str, Any] | None = None,
) -> CrmContactCompanyLink:
    _validate_link_records(business_profile=business_profile, contact=contact, company=company)
    payload = payload or {}
    link, created = CrmContactCompanyLink.objects.get_or_create(
        business_profile=business_profile,
        contact=contact,
        company=company,
    )
    _apply_contact_company_link_payload(link=link, payload=payload, default_primary=created)
    link.full_clean()
    link.save()
    if link.is_primary:
        _unset_other_primary_links(link)
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.UPDATED if not created else CrmActivityType.CREATED,
        "Contact linked to company",
        contact=contact,
        company=company,
        detail=f"link={link.id}",
    )
    return link


@transaction.atomic
def update_contact_company_link(
    *,
    business_profile: BusinessProfile,
    actor: User | None,
    link: CrmContactCompanyLink,
    payload: dict[str, Any],
) -> CrmContactCompanyLink:
    if link.business_profile_id != business_profile.id:
        raise ValidationError("Link belongs to a different business.")
    _apply_contact_company_link_payload(link=link, payload=payload, default_primary=False)
    link.full_clean()
    link.save()
    if link.is_primary:
        _unset_other_primary_links(link)
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.UPDATED,
        "Contact-company link updated",
        contact=link.contact,
        company=link.company,
        detail=f"link={link.id}",
    )
    return link


@transaction.atomic
def unlink_contact_from_company(*, business_profile: BusinessProfile, actor: User | None, link: CrmContactCompanyLink) -> None:
    if link.business_profile_id != business_profile.id:
        raise ValidationError("Link belongs to a different business.")
    contact = link.contact
    company = link.company
    was_primary = link.is_primary
    link.delete()
    if was_primary:
        replacement = (
            CrmContactCompanyLink.objects.filter(business_profile=business_profile, contact=contact)
            .order_by("created_at")
            .first()
        )
        if replacement:
            replacement.is_primary = True
            replacement.save(update_fields=["is_primary", "updated_at"])
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.OTHER,
        "Contact unlinked from company",
        contact=contact,
        company=company,
    )


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
