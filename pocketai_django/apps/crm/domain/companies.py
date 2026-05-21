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
    _normalize_company_payload,
    _upsert_field_values,
    _validate_merge_records,
)
from apps.crm.models import (
    CrmActivity,
    CrmActivityType,
    CrmCompany,
    CrmCompanyStatus,
    CrmContactCompanyLink,
    CrmDuplicateSuggestion,
    CrmDuplicateSuggestionStatus,
    CrmExternalIdentity,
    CrmFieldValue,
    CrmMergeEvent,
    CrmNote,
    CrmRecordType,
)


def list_companies(*, business_profile: BusinessProfile, search: str = "", status: str = "", limit: int = 50) -> PaginatedResult:
    qs = CrmCompany.objects.filter(business_profile=business_profile)
    if status:
        qs = qs.filter(status=status)
    if search.strip():
        text = search.strip()
        qs = qs.filter(Q(name__icontains=text) | Q(website__icontains=text) | Q(primary_phone__icontains=text))
    total = qs.count()
    return PaginatedResult(items=tuple(qs.order_by("name")[: max(1, limit)]), total=total)





def get_company(*, business_profile: BusinessProfile, company_id: uuid.UUID) -> CrmCompany:
    return (
        CrmCompany.objects.filter(business_profile=business_profile, id=company_id)
        .prefetch_related("contact_links__contact", "field_values__field_definition", "notes", "activities", "external_identities")
        .get()
    )



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




def archive_company(*, business_profile: BusinessProfile, actor: User | None, company: CrmCompany) -> CrmCompany:
    if company.status == CrmCompanyStatus.ARCHIVED:
        return company
    company.status = CrmCompanyStatus.ARCHIVED
    company.archived_at = timezone.now()
    company.save(update_fields=["status", "archived_at", "updated_at"])
    _log_activity(business_profile, actor, CrmActivityType.ARCHIVED, "Company archived", company=company)
    return company





def restore_company(*, business_profile: BusinessProfile, actor: User | None, company: CrmCompany) -> CrmCompany:
    company.status = CrmCompanyStatus.ACTIVE
    company.archived_at = None
    company.save(update_fields=["status", "archived_at", "updated_at"])
    _log_activity(business_profile, actor, CrmActivityType.RESTORED, "Company restored", company=company)
    return company





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
