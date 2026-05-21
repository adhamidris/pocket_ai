from __future__ import annotations

from typing import Any
import uuid

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import BusinessProfile, User
from apps.crm.domain.shared import (
    PaginatedResult,
    _log_activity,
    _normalize_contact_payload,
    _upsert_field_values,
    _validate_merge_records,
)
from apps.crm.models import (
    CrmActivity,
    CrmActivityType,
    CrmContact,
    CrmContactStatus,
    CrmDuplicateSuggestion,
    CrmDuplicateSuggestionStatus,
    CrmExternalIdentity,
    CrmFieldValue,
    CrmMergeEvent,
    CrmNote,
    CrmRecordType,
)


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

def get_contact(*, business_profile: BusinessProfile, contact_id: uuid.UUID) -> CrmContact:
    return (
        CrmContact.objects.filter(business_profile=business_profile, id=contact_id)
        .prefetch_related("company_links__company", "field_values__field_definition", "notes", "activities", "external_identities")
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


def archive_contact(*, business_profile: BusinessProfile, actor: User | None, contact: CrmContact) -> CrmContact:
    if contact.status == CrmContactStatus.ARCHIVED:
        return contact
    contact.status = CrmContactStatus.ARCHIVED
    contact.archived_at = timezone.now()
    contact.save(update_fields=["status", "archived_at", "updated_at"])
    _log_activity(business_profile, actor, CrmActivityType.ARCHIVED, "Contact archived", contact=contact)
    return contact

def restore_contact(*, business_profile: BusinessProfile, actor: User | None, contact: CrmContact) -> CrmContact:
    contact.status = CrmContactStatus.ACTIVE
    contact.archived_at = None
    contact.save(update_fields=["status", "archived_at", "updated_at"])
    _log_activity(business_profile, actor, CrmActivityType.RESTORED, "Contact restored", contact=contact)
    return contact

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
