from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import BusinessProfile, User


class CrmRecordType(models.TextChoices):
    CONTACT = "contact", "Contact"
    COMPANY = "company", "Company"


class CrmImportSourceFormat(models.TextChoices):
    CSV = "csv", "CSV"
    XLSX = "xlsx", "XLSX"


class CrmImportJobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class CrmImportRowStatus(models.TextChoices):
    CREATED = "created", "Created"
    UPDATED = "updated", "Updated"
    DUPLICATE = "duplicate", "Duplicate"
    SKIPPED = "skipped", "Skipped"
    FAILED = "failed", "Failed"


class CrmDuplicateSuggestionStatus(models.TextChoices):
    OPEN = "open", "Open"
    IGNORED = "ignored", "Ignored"
    MERGED = "merged", "Merged"


class CrmImportSourceFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(BusinessProfile, related_name="crm_import_source_files", on_delete=models.CASCADE)
    uploaded_by = models.ForeignKey(User, related_name="crm_import_source_files", null=True, blank=True, on_delete=models.SET_NULL)
    original_filename = models.CharField(max_length=255)
    file_format = models.CharField(max_length=16, choices=CrmImportSourceFormat.choices)
    file = models.FileField(upload_to="crm/imports/%Y/%m/%d")
    file_size_bytes = models.PositiveBigIntegerField(default=0)
    column_snapshot = models.JSONField(default=list, blank=True)
    sample_rows = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "crm_import_source_file"
        ordering = ("-created_at",)


class CrmImportTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(BusinessProfile, related_name="crm_import_templates", on_delete=models.CASCADE)
    created_by = models.ForeignKey(User, related_name="crm_import_templates", null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=255)
    mapping = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_import_template"
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=["business_profile", "name"], name="crm_import_template_unique_name"),
        ]


class CrmImportJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(BusinessProfile, related_name="crm_import_jobs", on_delete=models.CASCADE)
    source_file = models.ForeignKey(CrmImportSourceFile, related_name="jobs", on_delete=models.CASCADE)
    template = models.ForeignKey(CrmImportTemplate, related_name="jobs", null=True, blank=True, on_delete=models.SET_NULL)
    initiated_by = models.ForeignKey(User, related_name="crm_import_jobs", null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=24, choices=CrmImportJobStatus.choices, default=CrmImportJobStatus.QUEUED, db_index=True)
    mapping = models.JSONField(default=dict, blank=True)
    options = models.JSONField(default=dict, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    run_after = models.DateTimeField(null=True, blank=True, db_index=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=10)
    error_detail = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_import_job"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="crm_import_job_biz_status_idx"),
            models.Index(fields=["status", "run_after"], name="crm_impjob_status_run_idx"),
            models.Index(fields=["status", "lease_expires_at"], name="crm_impjob_status_lease_idx"),
        ]


class CrmImportRowResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(CrmImportJob, related_name="row_results", on_delete=models.CASCADE)
    row_number = models.PositiveIntegerField()
    status = models.CharField(max_length=24, choices=CrmImportRowStatus.choices)
    raw_data = models.JSONField(default=dict, blank=True)
    messages = models.JSONField(default=list, blank=True)
    contact_id = models.UUIDField(null=True, blank=True)
    company_id = models.UUIDField(null=True, blank=True)
    duplicate_of_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "crm_import_row_result"
        ordering = ("row_number",)
        constraints = [
            models.UniqueConstraint(fields=["job", "row_number"], name="crm_import_row_result_unique_row"),
        ]


class CrmDuplicateSuggestion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(BusinessProfile, related_name="crm_duplicate_suggestions", on_delete=models.CASCADE)
    import_job = models.ForeignKey(CrmImportJob, related_name="duplicate_suggestions", null=True, blank=True, on_delete=models.SET_NULL)
    record_type = models.CharField(max_length=16, choices=CrmRecordType.choices)
    record_id = models.UUIDField(null=True, blank=True)
    candidate_record_id = models.UUIDField()
    source_row_number = models.PositiveIntegerField(null=True, blank=True)
    incoming_snapshot = models.JSONField(default=dict, blank=True)
    match_reasons = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=CrmDuplicateSuggestionStatus.choices, default=CrmDuplicateSuggestionStatus.OPEN)
    resolution_note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "crm_duplicate_suggestion"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "record_type", "import_job", "source_row_number", "candidate_record_id"],
                name="crm_duplicate_suggestion_unique_row",
            ),
        ]
        indexes = [
            models.Index(fields=["business_profile", "status"], name="crm_dupe_biz_status_idx"),
        ]


class CrmMergeEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(BusinessProfile, related_name="crm_merge_events", on_delete=models.CASCADE)
    record_type = models.CharField(max_length=16, choices=CrmRecordType.choices)
    survivor_record_id = models.UUIDField()
    merged_record_id = models.UUIDField()
    performed_by = models.ForeignKey(User, related_name="crm_merge_events", null=True, blank=True, on_delete=models.SET_NULL)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "crm_merge_event"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "record_type"], name="crm_merge_event_biz_type_idx"),
        ]
