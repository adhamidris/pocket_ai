from __future__ import annotations

import uuid
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.crm.dashboard.duplicate_helpers import _serialize_duplicate_queue_item
from apps.crm.dashboard.import_helpers import _build_import_preview, _serialize_import_job
from apps.crm.dashboard.shared import _crm_shell_context, _current_business, dashboard_legacy_cases_retired
from apps.crm.forms import CrmCompanyForm, CrmContactForm, CrmFieldDefinitionForm, CrmImportQueueForm, CrmImportUploadForm
from apps.crm.import_pipeline.service import enqueue_import_job, infer_default_mapping, save_import_template, store_import_source_file
from apps.crm.models import (
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
from apps.crm.domain.services import (
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
