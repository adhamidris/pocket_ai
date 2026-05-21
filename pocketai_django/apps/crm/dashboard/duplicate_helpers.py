from __future__ import annotations

import uuid

from django.urls import reverse
from django.utils.translation import gettext as _

from apps.crm.models import CrmCompany, CrmContact, CrmDuplicateSuggestion, CrmRecordType


def _duplicate_compare_rows(suggestion: CrmDuplicateSuggestion, candidate) -> list[dict[str, object]]:
    if suggestion.record_type == CrmRecordType.CONTACT:
        rows = [
            {"label": _("Display name"), "incoming": suggestion.incoming_snapshot.get("Name") or suggestion.incoming_snapshot.get("Full Name") or suggestion.incoming_snapshot.get("Display Name") or "", "candidate": candidate.display_name if candidate else ""},
            {"label": _("Primary email"), "incoming": suggestion.incoming_snapshot.get("Email") or suggestion.incoming_snapshot.get("Primary Email") or "", "candidate": getattr(candidate, "primary_email", "") if candidate else ""},
            {"label": _("Primary phone"), "incoming": suggestion.incoming_snapshot.get("Phone") or suggestion.incoming_snapshot.get("Mobile") or suggestion.incoming_snapshot.get("Phone Number") or "", "candidate": getattr(candidate, "primary_phone", "") if candidate else ""},
            {"label": _("Role / title"), "incoming": suggestion.incoming_snapshot.get("Title") or "", "candidate": getattr(candidate, "title", "") if candidate else ""},
            {"label": _("Source"), "incoming": suggestion.incoming_snapshot.get("Source") or "", "candidate": getattr(candidate, "source", "") if candidate else ""},
        ]
    else:
        rows = [
            {"label": _("Company name"), "incoming": suggestion.incoming_snapshot.get("Company") or suggestion.incoming_snapshot.get("Company Name") or suggestion.incoming_snapshot.get("Account") or "", "candidate": candidate.name if candidate else ""},
            {"label": _("Website"), "incoming": suggestion.incoming_snapshot.get("Website") or suggestion.incoming_snapshot.get("Domain") or "", "candidate": getattr(candidate, "website", "") if candidate else ""},
            {"label": _("Primary phone"), "incoming": suggestion.incoming_snapshot.get("Company Phone") or suggestion.incoming_snapshot.get("Phone") or "", "candidate": getattr(candidate, "primary_phone", "") if candidate else ""},
            {"label": _("Source"), "incoming": suggestion.incoming_snapshot.get("Source") or "", "candidate": getattr(candidate, "source", "") if candidate else ""},
        ]
    return [row for row in rows if row["incoming"] or row["candidate"]]


def _resolve_duplicate_record(*, business, record_type: str, record_id):
    if not record_id:
        return None
    try:
        record_uuid = uuid.UUID(str(record_id))
    except (TypeError, ValueError, AttributeError):
        return None
    if record_type == CrmRecordType.CONTACT:
        return CrmContact.objects.filter(business_profile=business, id=record_uuid).first()
    return CrmCompany.objects.filter(business_profile=business, id=record_uuid).first()


def _serialize_duplicate_queue_item(*, business, suggestion: CrmDuplicateSuggestion) -> dict[str, object]:
    candidate = _resolve_duplicate_record(business=business, record_type=suggestion.record_type, record_id=suggestion.candidate_record_id)
    linked_record = _resolve_duplicate_record(business=business, record_type=suggestion.record_type, record_id=suggestion.record_id)
    if suggestion.record_type == CrmRecordType.CONTACT:
        candidate_url = (
            reverse("frontend:dashboard-crm-contact-detail", kwargs={"contact_id": candidate.id})
            if candidate
            else ""
        )
        linked_url = (
            reverse("frontend:dashboard-crm-contact-detail", kwargs={"contact_id": linked_record.id})
            if linked_record
            else ""
        )
        title = _("Contact duplicate suggestion")
    else:
        candidate_url = (
            reverse("frontend:dashboard-crm-company-detail", kwargs={"company_id": candidate.id})
            if candidate
            else ""
        )
        linked_url = (
            reverse("frontend:dashboard-crm-company-detail", kwargs={"company_id": linked_record.id})
            if linked_record
            else ""
        )
        title = _("Company duplicate suggestion")
    return {
        "id": str(suggestion.id),
        "title": title,
        "record_type": suggestion.record_type,
        "status": suggestion.status,
        "status_label": suggestion.get_status_display(),
        "created_at": suggestion.created_at,
        "resolved_at": suggestion.resolved_at,
        "resolution_note": suggestion.resolution_note,
        "source_row_number": suggestion.source_row_number,
        "match_reasons": list(suggestion.match_reasons or []),
        "incoming_snapshot_items": list((suggestion.incoming_snapshot or {}).items()),
        "candidate": candidate,
        "candidate_url": candidate_url,
        "linked_record": linked_record,
        "linked_record_url": linked_url,
        "compare_rows": _duplicate_compare_rows(suggestion, candidate),
        "can_merge": linked_record is not None,
    }
