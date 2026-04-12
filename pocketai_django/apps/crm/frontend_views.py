from __future__ import annotations

import uuid
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from .flags import crm_v1_enabled
from .forms import CrmCompanyForm, CrmContactForm, CrmFieldDefinitionForm, CrmImportQueueForm, CrmImportUploadForm
from .imports import enqueue_import_job, infer_default_mapping, save_import_template, store_import_source_file
from .models import (
    CrmActivity,
    CrmCompany,
    CrmContact,
    CrmDuplicateSuggestion,
    CrmDuplicateSuggestionStatus,
    CrmFieldDefinition,
    CrmFieldTarget,
    CrmFieldType,
    CrmImportJob,
    CrmImportTemplate,
    CrmRecordType,
)
from .services import (
    archive_field_definition,
    create_company,
    create_contact,
    get_company,
    get_contact,
    get_field_definition,
    ignore_duplicate_suggestion,
    list_companies,
    list_contacts,
    list_duplicate_suggestions,
    list_field_definitions,
    merge_duplicate_suggestion,
    reopen_duplicate_suggestion,
    restore_field_definition,
    update_company,
    update_contact,
    update_field_definition,
    upsert_field_definition,
)


def _current_user_name(request: HttpRequest) -> str:
    first = (getattr(request.user, "first_name", "") or "").strip()
    return first or request.user.email


def _current_business(request: HttpRequest):
    business = request.user.business_profiles.order_by("-created_at").first()
    if business is None or not crm_v1_enabled(business):
        raise Http404(_("CRM is not enabled for this business."))
    return business


def _crm_shell_context(request: HttpRequest, business, *, active: str) -> dict[str, object]:
    open_duplicates = CrmDuplicateSuggestion.objects.filter(business_profile=business, status="open").count()
    return {
        "user_name": _current_user_name(request),
        "crm_active": active,
        "crm_business_name": business.name,
        "crm_open_duplicates": open_duplicates,
    }


def _format_file_size(num_bytes: int) -> str:
    value = float(num_bytes or 0)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "bytes":
                return _("%(value)s %(unit)s") % {"value": int(value), "unit": unit}
            return _("%(value).1f %(unit)s") % {"value": value, "unit": unit}
        value /= 1024
    return _("0 bytes")


def _mapping_target_label(target: str) -> str:
    labels = {
        "display_name": _("Display name"),
        "first_name": _("First name"),
        "last_name": _("Last name"),
        "primary_email": _("Primary email"),
        "primary_phone": _("Primary phone"),
        "title": _("Role / title"),
        "name": _("Company name"),
        "website": _("Website"),
    }
    return labels.get(target, target.replace("_", " ").title())


def _build_import_preview(source, *, mapping: dict[str, object], selected_template=None, template_name: str = "") -> dict[str, object]:
    contact_mapping = mapping.get("contact") if isinstance(mapping.get("contact"), dict) else {}
    company_mapping = mapping.get("company") if isinstance(mapping.get("company"), dict) else {}
    mapped_columns = set()
    sections: list[dict[str, object]] = []

    for key, title in (("contact", _("Contact mapping")), ("company", _("Company mapping"))):
        config = contact_mapping if key == "contact" else company_mapping
        column_map = config.get("columns") if isinstance(config, dict) else {}
        entries = []
        if isinstance(column_map, dict):
            for source_column in source.column_snapshot:
                target = column_map.get(source_column)
                if not target:
                    continue
                mapped_columns.add(source_column)
                entries.append({"source": source_column, "target": _mapping_target_label(str(target))})
        external_identity = None
        if key == "contact":
            identity = config.get("external_identity") if isinstance(config, dict) else None
            if isinstance(identity, dict) and identity.get("source_column"):
                mapped_columns.add(str(identity["source_column"]))
                external_identity = {
                    "source": str(identity["source_column"]),
                    "system": str(identity.get("source_system") or "import"),
                    "object_type": str(identity.get("external_object_type") or "contact"),
                    "label": str(identity.get("external_label") or identity["source_column"]),
                }
        sections.append(
            {
                "key": key,
                "title": title,
                "entries": entries,
                "external_identity": external_identity,
            }
        )

    unmapped_columns = [column for column in source.column_snapshot if column not in mapped_columns]
    warnings: list[str] = []
    if not sections[0]["entries"] and not sections[1]["entries"]:
        warnings.append(_("No default field mapping was detected. Consider saving a reusable template before you queue this import."))
    if unmapped_columns:
        warnings.append(
            _("Some columns are currently unmapped. They will be ignored unless you later turn them into custom fields or update the mapping template.")
        )
    if not sections[0]["external_identity"]:
        warnings.append(_("No external ID column was detected, so source-system exact-match updates are not available for this file."))
    preview_rows = [
        {
            "cells": [row.get(column, "") for column in source.column_snapshot],
        }
        for row in list(source.sample_rows or [])
    ]
    return {
        "source": source,
        "mapping": mapping,
        "selected_template": selected_template,
        "template_name": template_name,
        "sections": sections,
        "unmapped_columns": unmapped_columns,
        "warnings": warnings,
        "sample_rows": preview_rows,
        "column_count": len(source.column_snapshot or []),
        "sample_count": len(preview_rows),
        "file_size_label": _format_file_size(source.file_size_bytes),
        "file_format_label": str(source.file_format).upper(),
    }


def _serialize_import_job(job: CrmImportJob) -> dict[str, object]:
    summary = job.summary or {}
    total_processed = sum(int(summary.get(key, 0) or 0) for key in ("created", "updated", "duplicates", "failed", "skipped"))
    return {
        "id": str(job.id),
        "status": job.status,
        "status_label": job.get_status_display(),
        "source_name": job.source_file.original_filename,
        "template_name": job.template.name if job.template_id else "",
        "summary": {
            "created": int(summary.get("created", 0) or 0),
            "updated": int(summary.get("updated", 0) or 0),
            "duplicates": int(summary.get("duplicates", 0) or 0),
            "failed": int(summary.get("failed", 0) or 0),
            "skipped": int(summary.get("skipped", 0) or 0),
        },
        "total_processed": total_processed,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "created_at": job.created_at,
        "error_detail": job.error_detail,
    }


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


def _serialize_field_definition(definition: CrmFieldDefinition) -> dict[str, object]:
    field_type = definition.field_type
    if field_type in {CrmFieldType.SELECT, CrmFieldType.MULTI_SELECT}:
        config_summary = ", ".join(str(item) for item in definition.options[:4])
        if len(definition.options) > 4:
            config_summary = _("%(summary)s + %(extra)s more") % {
                "summary": config_summary,
                "extra": len(definition.options) - 4,
            }
    elif field_type in {CrmFieldType.OBJECT, CrmFieldType.OBJECT_LIST}:
        config_summary = _("%(count)s schema properties") % {"count": len(definition.schema.keys())}
    else:
        config_summary = _("Core typed field")
    return {
        "id": str(definition.id),
        "label": definition.label,
        "key": definition.key,
        "target_object": definition.target_object,
        "target_label": definition.get_target_object_display(),
        "field_type": definition.field_type,
        "field_type_label": definition.get_field_type_display(),
        "required": definition.required,
        "searchable": definition.searchable,
        "filterable": definition.filterable,
        "pii": definition.pii,
        "archived": definition.archived,
        "config_summary": config_summary,
        "updated_at": definition.updated_at,
    }


@login_required
def dashboard_crm_overview(request: HttpRequest) -> HttpResponse:
    business = _current_business(request)
    contacts_total = CrmContact.objects.filter(business_profile=business).count()
    companies_total = CrmCompany.objects.filter(business_profile=business).count()
    active_imports = CrmImportJob.objects.filter(business_profile=business, status__in=["queued", "running"]).count()
    open_duplicates = CrmDuplicateSuggestion.objects.filter(business_profile=business, status="open").count()
    recent_activities = CrmActivity.objects.filter(business_profile=business).order_by("-occurred_at", "-created_at")[:8]
    recent_contacts = CrmContact.objects.filter(business_profile=business).order_by("-created_at")[:5]
    recent_companies = CrmCompany.objects.filter(business_profile=business).order_by("-created_at")[:5]
    context = {
        **_crm_shell_context(request, business, active="crm-overview"),
        "contacts_total": contacts_total,
        "companies_total": companies_total,
        "active_imports": active_imports,
        "open_duplicates": open_duplicates,
        "recent_activities": recent_activities,
        "recent_contacts": recent_contacts,
        "recent_companies": recent_companies,
    }
    return render(request, "frontend/crm/overview.html", context)


@login_required
def dashboard_crm_contacts(request: HttpRequest) -> HttpResponse:
    business = _current_business(request)
    query = request.GET.get("q", "")
    if request.method == "POST":
        form = CrmContactForm(request.POST)
        if form.is_valid():
            contact = create_contact(business_profile=business, actor=request.user, payload=form.cleaned_data)
            return redirect("frontend:dashboard-crm-contact-detail", contact_id=contact.id)
    else:
        form = CrmContactForm()
    result = list_contacts(business_profile=business, search=query)
    context = {
        **_crm_shell_context(request, business, active="crm-contacts"),
        "contacts": result.items,
        "total": result.total,
        "form": form,
        "search_query": query,
    }
    return render(request, "frontend/crm/contacts.html", context)


@login_required
def dashboard_crm_contact_detail(request: HttpRequest, contact_id) -> HttpResponse:
    business = _current_business(request)
    contact = get_object_or_404(CrmContact, business_profile=business, id=contact_id)
    if request.method == "POST":
        form = CrmContactForm(request.POST, instance=contact)
        if form.is_valid():
            update_contact(business_profile=business, actor=request.user, contact=contact, payload=form.cleaned_data)
            return redirect("frontend:dashboard-crm-contact-detail", contact_id=contact.id)
    else:
        form = CrmContactForm(instance=contact)
    detail = get_contact(business_profile=business, contact_id=contact.id)
    context = {
        **_crm_shell_context(request, business, active="crm-contacts"),
        "contact": detail,
        "form": form,
    }
    return render(request, "frontend/crm/contact_detail.html", context)


@login_required
def dashboard_crm_companies(request: HttpRequest) -> HttpResponse:
    business = _current_business(request)
    query = request.GET.get("q", "")
    if request.method == "POST":
        form = CrmCompanyForm(request.POST)
        if form.is_valid():
            company = create_company(business_profile=business, actor=request.user, payload=form.cleaned_data)
            return redirect("frontend:dashboard-crm-company-detail", company_id=company.id)
    else:
        form = CrmCompanyForm()
    result = list_companies(business_profile=business, search=query)
    context = {
        **_crm_shell_context(request, business, active="crm-companies"),
        "companies": result.items,
        "total": result.total,
        "form": form,
        "search_query": query,
    }
    return render(request, "frontend/crm/companies.html", context)


@login_required
def dashboard_crm_company_detail(request: HttpRequest, company_id) -> HttpResponse:
    business = _current_business(request)
    company = get_object_or_404(CrmCompany, business_profile=business, id=company_id)
    if request.method == "POST":
        form = CrmCompanyForm(request.POST, instance=company)
        if form.is_valid():
            update_company(business_profile=business, actor=request.user, company=company, payload=form.cleaned_data)
            return redirect("frontend:dashboard-crm-company-detail", company_id=company.id)
    else:
        form = CrmCompanyForm(instance=company)
    detail = get_company(business_profile=business, company_id=company.id)
    context = {
        **_crm_shell_context(request, business, active="crm-companies"),
        "company": detail,
        "form": form,
    }
    return render(request, "frontend/crm/company_detail.html", context)


@login_required
def dashboard_crm_imports(request: HttpRequest) -> HttpResponse:
    business = _current_business(request)
    upload_notice = None
    import_preview = None
    upload_form = CrmImportUploadForm(business_profile=business)
    queue_form = CrmImportQueueForm()
    if request.method == "POST":
        action = (request.POST.get("action") or "preview").strip().lower()
        if action == "queue":
            queue_form = CrmImportQueueForm(request.POST)
            if queue_form.is_valid():
                source = get_object_or_404(
                    business.crm_import_source_files.all(),
                    id=queue_form.cleaned_data["source_file_id"],
                )
                selected_template = None
                template_id = queue_form.cleaned_data.get("template_id")
                if template_id:
                    selected_template = get_object_or_404(CrmImportTemplate, business_profile=business, id=template_id)
                mapping = selected_template.mapping if selected_template else infer_default_mapping(source.column_snapshot)
                template_name = (queue_form.cleaned_data.get("template_name") or "").strip()
                template = selected_template
                if template is None and queue_form.cleaned_data.get("save_as_template") and template_name:
                    template = save_import_template(
                        business_profile=business,
                        created_by=request.user,
                        name=template_name,
                        mapping=mapping,
                    )
                enqueue_import_job(
                    business_profile=business,
                    source_file=source,
                    initiated_by=request.user,
                    mapping=mapping,
                    template=template,
                )
                upload_notice = _("Import file queued for processing.")
                upload_form = CrmImportUploadForm(business_profile=business)
                queue_form = CrmImportQueueForm()
            else:
                source_id = request.POST.get("source_file_id")
                template_id_raw = request.POST.get("template_id")
                try:
                    source = get_object_or_404(business.crm_import_source_files.all(), id=uuid.UUID(str(source_id)))
                except (TypeError, ValueError):
                    source = None
                selected_template = None
                if source is not None and template_id_raw:
                    try:
                        selected_template = CrmImportTemplate.objects.filter(business_profile=business, id=uuid.UUID(str(template_id_raw))).first()
                    except (TypeError, ValueError):
                        selected_template = None
                if source is not None:
                    mapping = selected_template.mapping if selected_template else infer_default_mapping(source.column_snapshot)
                    import_preview = _build_import_preview(
                        source,
                        mapping=mapping,
                        selected_template=selected_template,
                        template_name=(request.POST.get("template_name") or "").strip(),
                    )
                upload_form = CrmImportUploadForm(
                    business_profile=business,
                    initial={
                        "template_name": request.POST.get("template_name") or "",
                        "existing_template": selected_template,
                    },
                )
        else:
            upload_form = CrmImportUploadForm(request.POST, request.FILES, business_profile=business)
            if upload_form.is_valid():
                try:
                    source = store_import_source_file(
                        business_profile=business,
                        uploaded_by=request.user,
                        uploaded_file=upload_form.cleaned_data["source_file"],
                    )
                except ValidationError as exc:
                    upload_form.add_error("source_file", exc)
                else:
                    selected_template = upload_form.cleaned_data.get("existing_template")
                    mapping = selected_template.mapping if selected_template else infer_default_mapping(source.column_snapshot)
                    template_name = (upload_form.cleaned_data.get("template_name") or "").strip()
                    import_preview = _build_import_preview(
                        source,
                        mapping=mapping,
                        selected_template=selected_template,
                        template_name=template_name,
                    )
                    queue_form = CrmImportQueueForm(
                        initial={
                            "source_file_id": source.id,
                            "template_id": selected_template.id if selected_template else "",
                            "template_name": template_name,
                            "save_as_template": bool(template_name and not selected_template),
                        }
                    )
    jobs = [
        _serialize_import_job(job)
        for job in CrmImportJob.objects.filter(business_profile=business).select_related("source_file", "template").order_by("-created_at")[:12]
    ]
    duplicates = CrmDuplicateSuggestion.objects.filter(business_profile=business).order_by("-created_at")[:8]
    templates = CrmImportTemplate.objects.filter(business_profile=business).order_by("name")
    active_imports = sum(1 for item in jobs if item["status"] in {"queued", "running"})
    context = {
        **_crm_shell_context(request, business, active="crm-imports"),
        "form": upload_form,
        "queue_form": queue_form,
        "jobs": jobs,
        "duplicates": duplicates,
        "upload_notice": upload_notice,
        "import_preview": import_preview,
        "saved_templates": templates,
        "active_imports": active_imports,
    }
    return render(request, "frontend/crm/imports.html", context)


@login_required
def dashboard_crm_duplicates(request: HttpRequest) -> HttpResponse:
    business = _current_business(request)
    status_filter_raw = request.POST.get("status") if request.method == "POST" else request.GET.get("status")
    status_filter = (status_filter_raw or CrmDuplicateSuggestionStatus.OPEN).strip().lower()
    allowed_statuses = {
        CrmDuplicateSuggestionStatus.OPEN,
        CrmDuplicateSuggestionStatus.IGNORED,
        CrmDuplicateSuggestionStatus.MERGED,
    }
    if status_filter not in allowed_statuses:
        status_filter = CrmDuplicateSuggestionStatus.OPEN

    notice = ""
    error_message = ""
    if request.method == "POST":
        suggestion = get_object_or_404(CrmDuplicateSuggestion, business_profile=business, id=request.POST.get("suggestion_id"))
        action = (request.POST.get("action") or "").strip().lower()
        resolution_note = (request.POST.get("resolution_note") or "").strip()
        try:
            if action == "ignore":
                ignore_duplicate_suggestion(business_profile=business, actor=request.user, suggestion=suggestion, resolution_note=resolution_note)
                notice = _("Duplicate suggestion ignored.")
            elif action == "reopen":
                reopen_duplicate_suggestion(business_profile=business, actor=request.user, suggestion=suggestion)
                notice = _("Duplicate suggestion reopened.")
            elif action == "merge":
                record_id_raw = (request.POST.get("record_id") or "").strip()
                merged_record_id = uuid.UUID(record_id_raw) if record_id_raw else None
                merge_duplicate_suggestion(
                    business_profile=business,
                    actor=request.user,
                    suggestion=suggestion,
                    merged_record_id=merged_record_id,
                    resolution_note=resolution_note,
                )
                notice = _("Duplicate suggestion merged.")
            else:
                error_message = _("Unsupported duplicate action.")
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                error_message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            else:
                error_message = _("A valid mergeable record is required before this duplicate can be merged.")

        params = {"status": status_filter}
        if notice:
            params["notice"] = notice
        if error_message:
            params["error"] = error_message
        return redirect(f"{reverse('frontend:dashboard-crm-duplicates')}?{urlencode(params)}")

    if request.GET.get("notice"):
        notice = request.GET["notice"]
    if request.GET.get("error"):
        error_message = request.GET["error"]

    suggestions = list_duplicate_suggestions(
        business_profile=business,
        status=status_filter,
        limit=100,
    )
    queue_items = [_serialize_duplicate_queue_item(business=business, suggestion=item) for item in suggestions]
    counts = {
        "open": CrmDuplicateSuggestion.objects.filter(business_profile=business, status=CrmDuplicateSuggestionStatus.OPEN).count(),
        "ignored": CrmDuplicateSuggestion.objects.filter(business_profile=business, status=CrmDuplicateSuggestionStatus.IGNORED).count(),
        "merged": CrmDuplicateSuggestion.objects.filter(business_profile=business, status=CrmDuplicateSuggestionStatus.MERGED).count(),
    }
    context = {
        **_crm_shell_context(request, business, active="crm-duplicates"),
        "duplicate_items": queue_items,
        "duplicate_status_filter": status_filter,
        "duplicate_counts": counts,
        "duplicate_notice": notice,
        "duplicate_error": error_message,
    }
    return render(request, "frontend/crm/duplicates.html", context)


@login_required
def dashboard_crm_fields(request: HttpRequest) -> HttpResponse:
    business = _current_business(request)
    target_filter = (request.GET.get("target") or CrmFieldTarget.CONTACT).strip().lower()
    if target_filter not in {CrmFieldTarget.CONTACT, CrmFieldTarget.COMPANY}:
        target_filter = CrmFieldTarget.CONTACT

    selected_definition = None
    selected_definition_raw = (request.GET.get("definition") or "").strip()
    if selected_definition_raw:
        try:
            selected_definition = get_field_definition(
                business_profile=business,
                field_definition_id=uuid.UUID(selected_definition_raw),
            )
        except (ValidationError, ValueError, CrmFieldDefinition.DoesNotExist):
            selected_definition = None

    notice = ""
    error_message = ""
    editor_form = CrmFieldDefinitionForm(
        initial={
            "target_object": selected_definition.target_object if selected_definition else target_filter,
            "field_type": selected_definition.field_type if selected_definition else CrmFieldType.TEXT,
            "options": selected_definition.options if selected_definition else [],
            "schema": selected_definition.schema if selected_definition else {},
        },
        instance=selected_definition,
    )

    if request.method == "POST":
        action = (request.POST.get("action") or "save").strip().lower()
        definition_id_raw = (request.POST.get("definition_id") or "").strip()
        action_definition = None
        if definition_id_raw:
            try:
                action_definition = get_field_definition(
                    business_profile=business,
                    field_definition_id=uuid.UUID(definition_id_raw),
                )
            except (ValidationError, ValueError, CrmFieldDefinition.DoesNotExist):
                action_definition = None

        if action in {"archive", "restore"}:
            if action_definition is None:
                error_message = _("The selected field definition could not be found.")
            else:
                if action == "archive":
                    archive_field_definition(business_profile=business, actor=request.user, definition=action_definition)
                    notice = _("Field definition archived.")
                else:
                    restore_field_definition(business_profile=business, actor=request.user, definition=action_definition)
                    notice = _("Field definition restored.")
                target_filter = action_definition.target_object
                selected_definition = action_definition
        else:
            editor_form = CrmFieldDefinitionForm(request.POST, instance=action_definition)
            if editor_form.is_valid():
                payload = editor_form.cleaned_data
                if action_definition is None:
                    selected_definition = upsert_field_definition(
                        business_profile=business,
                        payload=payload,
                        actor=request.user,
                    )
                    notice = _("Field definition saved.")
                else:
                    selected_definition = update_field_definition(
                        business_profile=business,
                        definition=action_definition,
                        payload=payload,
                        actor=request.user,
                    )
                    notice = _("Field definition updated.")
                target_filter = selected_definition.target_object
                editor_form = CrmFieldDefinitionForm(instance=selected_definition)
            else:
                error_message = _("Review the field configuration and correct the highlighted inputs.")
                selected_definition = action_definition
                target_filter = (
                    editor_form.data.get("target_object")
                    or (action_definition.target_object if action_definition else target_filter)
                )
                if target_filter not in {CrmFieldTarget.CONTACT, CrmFieldTarget.COMPANY}:
                    target_filter = CrmFieldTarget.CONTACT

    active_definitions = list_field_definitions(business_profile=business, target_object=target_filter)
    archived_definitions = tuple(
        CrmFieldDefinition.objects.filter(
            business_profile=business,
            target_object=target_filter,
            archived=True,
        ).order_by("label")
    )
    if selected_definition is None and active_definitions:
        selected_definition = active_definitions[0]
        editor_form = CrmFieldDefinitionForm(instance=selected_definition)

    definition_counts = {
        "contact": CrmFieldDefinition.objects.filter(
            business_profile=business,
            target_object=CrmFieldTarget.CONTACT,
            archived=False,
        ).count(),
        "company": CrmFieldDefinition.objects.filter(
            business_profile=business,
            target_object=CrmFieldTarget.COMPANY,
            archived=False,
        ).count(),
    }
    context = {
        **_crm_shell_context(request, business, active="crm-fields"),
        "fields_notice": notice,
        "fields_error": error_message,
        "field_target_filter": target_filter,
        "field_counts": definition_counts,
        "field_definitions": tuple(_serialize_field_definition(item) for item in active_definitions),
        "archived_field_definitions": tuple(_serialize_field_definition(item) for item in archived_definitions),
        "selected_field_definition": _serialize_field_definition(selected_definition) if selected_definition else None,
        "field_form": editor_form,
    }
    return render(request, "frontend/crm/fields.html", context)
