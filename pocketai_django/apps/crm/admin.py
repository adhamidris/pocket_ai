from __future__ import annotations

from django.contrib import admin

from .models import (
    CrmActivity,
    CrmCompany,
    CrmContact,
    CrmContactCompanyLink,
    CrmDuplicateSuggestion,
    CrmExternalIdentity,
    CrmFieldDefinition,
    CrmFieldValue,
    CrmImportJob,
    CrmImportRowResult,
    CrmImportSourceFile,
    CrmImportTemplate,
    CrmMergeEvent,
    CrmNote,
)


@admin.register(CrmContact)
class CrmContactAdmin(admin.ModelAdmin):
    list_display = ("display_name", "business_profile", "status", "primary_email", "primary_phone", "updated_at")
    list_filter = ("status", "business_profile")
    search_fields = ("display_name", "primary_email", "primary_phone")
    autocomplete_fields = ("business_profile", "owner")


@admin.register(CrmCompany)
class CrmCompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "business_profile", "status", "website", "updated_at")
    list_filter = ("status", "business_profile")
    search_fields = ("name", "website", "primary_phone")
    autocomplete_fields = ("business_profile", "owner")


@admin.register(CrmContactCompanyLink)
class CrmContactCompanyLinkAdmin(admin.ModelAdmin):
    list_display = ("contact", "company", "relationship_title", "is_primary")
    list_filter = ("is_primary", "business_profile")
    autocomplete_fields = ("business_profile", "contact", "company")


@admin.register(CrmExternalIdentity)
class CrmExternalIdentityAdmin(admin.ModelAdmin):
    list_display = ("record_type", "source_system", "external_object_type", "external_id", "last_seen_at")
    list_filter = ("record_type", "source_system", "business_profile")
    search_fields = ("external_id", "external_label", "source_account_ref")
    autocomplete_fields = ("business_profile", "contact", "company")


@admin.register(CrmFieldDefinition)
class CrmFieldDefinitionAdmin(admin.ModelAdmin):
    list_display = ("label", "target_object", "field_type", "searchable", "filterable", "archived")
    list_filter = ("target_object", "field_type", "searchable", "filterable", "archived")
    search_fields = ("label", "key")
    autocomplete_fields = ("business_profile",)


@admin.register(CrmFieldValue)
class CrmFieldValueAdmin(admin.ModelAdmin):
    list_display = ("field_definition", "contact", "company", "exact_text", "number_value")
    list_filter = ("field_definition__target_object", "business_profile")
    search_fields = ("exact_text", "search_text")
    autocomplete_fields = ("business_profile", "field_definition", "contact", "company")


@admin.register(CrmNote)
class CrmNoteAdmin(admin.ModelAdmin):
    list_display = ("business_profile", "contact", "company", "author", "created_at")
    list_filter = ("business_profile", "created_at")
    search_fields = ("body",)
    autocomplete_fields = ("business_profile", "contact", "company", "author")


@admin.register(CrmActivity)
class CrmActivityAdmin(admin.ModelAdmin):
    list_display = ("summary", "activity_type", "actor_type", "contact", "company", "occurred_at")
    list_filter = ("activity_type", "actor_type", "business_profile")
    search_fields = ("summary", "detail")
    autocomplete_fields = ("business_profile", "contact", "company", "actor_user")


@admin.register(CrmImportSourceFile)
class CrmImportSourceFileAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "business_profile", "file_format", "file_size_bytes", "created_at")
    list_filter = ("file_format", "business_profile")
    search_fields = ("original_filename",)
    autocomplete_fields = ("business_profile", "uploaded_by")


@admin.register(CrmImportTemplate)
class CrmImportTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "business_profile", "updated_at")
    search_fields = ("name",)
    autocomplete_fields = ("business_profile", "created_by")


@admin.register(CrmImportJob)
class CrmImportJobAdmin(admin.ModelAdmin):
    list_display = ("id", "business_profile", "status", "created_at", "finished_at")
    list_filter = ("status", "business_profile")
    search_fields = ("id", "error_detail")
    autocomplete_fields = ("business_profile", "source_file", "template", "initiated_by")


@admin.register(CrmImportRowResult)
class CrmImportRowResultAdmin(admin.ModelAdmin):
    list_display = ("job", "row_number", "status", "contact_id", "company_id")
    list_filter = ("status",)
    autocomplete_fields = ("job",)


@admin.register(CrmDuplicateSuggestion)
class CrmDuplicateSuggestionAdmin(admin.ModelAdmin):
    list_display = ("record_type", "record_id", "candidate_record_id", "status", "created_at")
    list_filter = ("record_type", "status", "business_profile")
    autocomplete_fields = ("business_profile", "import_job")


@admin.register(CrmMergeEvent)
class CrmMergeEventAdmin(admin.ModelAdmin):
    list_display = ("record_type", "survivor_record_id", "merged_record_id", "created_at")
    list_filter = ("record_type", "business_profile")
    autocomplete_fields = ("business_profile", "performed_by")
