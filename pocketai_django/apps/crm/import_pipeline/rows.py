from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.crm.domain.services import create_company, create_contact, update_company, update_contact
from apps.crm.import_pipeline.matching import _attach_company, _external_identity_payload, _match_company, _match_contact
from apps.crm.models import (
    CrmDuplicateSuggestion,
    CrmDuplicateSuggestionStatus,
    CrmExternalIdentity,
    CrmImportJob,
    CrmImportRowStatus,
    CrmRecordType,
)


def _process_row(*, job: CrmImportJob, row_number: int, row: dict[str, Any]) -> tuple[str, list[str], Any, Any, Any]:
    mapping = job.mapping or {}
    business = job.business_profile
    actor = job.initiated_by
    contact_payload = _extract_payload(row, mapping.get("contact") or {})
    company_payload = _extract_payload(row, mapping.get("company") or {})
    if not contact_payload and not company_payload:
        return CrmImportRowStatus.SKIPPED, ["No mapped values"], None, None, None

    company = None
    if company_payload:
        company_match = _match_company(business_profile=business, payload=company_payload)
        if company_match:
            company = update_company(business_profile=business, actor=actor, company=company_match, payload=company_payload)
            company_status = CrmImportRowStatus.UPDATED
        else:
            company = create_company(business_profile=business, actor=actor, payload=company_payload)
            company_status = CrmImportRowStatus.CREATED
    else:
        company_status = CrmImportRowStatus.SKIPPED

    if not contact_payload:
        return company_status, [], None, getattr(company, "id", None), None

    external_identity = _external_identity_payload(mapping.get("contact") or {}, row)
    contact_match, duplicate_match = _match_contact(
        business_profile=business,
        payload=contact_payload,
        external_identity=external_identity,
    )
    if duplicate_match and not contact_match:
        reasons = []
        if contact_payload.get("primary_email"):
            reasons.append("exact_email")
        if contact_payload.get("primary_phone"):
            reasons.append("exact_phone")
        CrmDuplicateSuggestion.objects.update_or_create(
            business_profile=business,
            import_job=job,
            record_type=CrmRecordType.CONTACT,
            source_row_number=row_number,
            candidate_record_id=duplicate_match.id,
            defaults={
                "record_id": None,
                "incoming_snapshot": row,
                "match_reasons": reasons or ["exact_identifier"],
                "status": CrmDuplicateSuggestionStatus.OPEN,
            },
        )
        return CrmImportRowStatus.DUPLICATE, ["Duplicate suggestion created"], None, getattr(company, "id", None), duplicate_match.id
    if contact_match:
        contact = update_contact(business_profile=business, actor=actor, contact=contact_match, payload=contact_payload)
        status = CrmImportRowStatus.UPDATED
    else:
        contact = create_contact(business_profile=business, actor=actor, payload=contact_payload)
        status = CrmImportRowStatus.CREATED
    if company:
        _attach_company(contact=contact, company=company)
    if external_identity:
        CrmExternalIdentity.objects.update_or_create(
            business_profile=business,
            source_system=external_identity["source_system"],
            source_account_ref=external_identity["source_account_ref"],
            external_object_type=external_identity["external_object_type"],
            external_id=external_identity["external_id"],
            defaults={
                "record_type": CrmRecordType.CONTACT,
                "contact": contact,
                "external_label": external_identity["external_label"],
                "last_seen_at": timezone.now(),
            },
        )
    company_external_identity = _external_identity_payload(mapping.get("company") or {}, row)
    if company and company_external_identity:
        CrmExternalIdentity.objects.update_or_create(
            business_profile=business,
            source_system=company_external_identity["source_system"],
            source_account_ref=company_external_identity["source_account_ref"],
            external_object_type=company_external_identity["external_object_type"],
            external_id=company_external_identity["external_id"],
            defaults={
                "record_type": CrmRecordType.COMPANY,
                "company": company,
                "external_label": company_external_identity["external_label"],
                "last_seen_at": timezone.now(),
            },
        )
    return status, [], contact.id, getattr(company, "id", None), None


def _extract_payload(row: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    custom_fields: dict[str, Any] = {}
    for source_column, target in (mapping.get("columns") or {}).items():
        value = row.get(source_column)
        if value in (None, ""):
            continue
        if isinstance(target, str):
            payload[target] = value
        elif isinstance(target, dict) and target.get("kind") == "custom":
            custom_fields[str(target.get("key") or "")] = value
    if custom_fields:
        payload["custom_fields"] = custom_fields
    return payload
