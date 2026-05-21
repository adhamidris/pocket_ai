from __future__ import annotations

from typing import Sequence
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import BusinessProfile, User
from apps.crm.domain.companies import get_company, merge_companies
from apps.crm.domain.contacts import get_contact, merge_contacts
from apps.crm.domain.shared import _log_activity, _validate_duplicate_suggestion_access
from apps.crm.models import CrmActivityType, CrmDuplicateSuggestion, CrmDuplicateSuggestionStatus, CrmRecordType


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
