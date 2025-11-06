from __future__ import annotations

from django.contrib import admin

from .models import Case, CaseDocumentLink, CaseHistoryEntry, CaseMessage


@admin.register(CaseHistoryEntry)
class CaseHistoryEntryAdmin(admin.ModelAdmin):
    list_display = ("case", "summary", "source", "occurred_at")
    list_filter = ("source", "occurred_at")
    search_fields = ("case__case_number", "summary", "session_reference")
    ordering = ("-occurred_at",)


@admin.register(CaseMessage)
class CaseMessageAdmin(admin.ModelAdmin):
    list_display = ("case", "sender", "sender_display_name", "sent_at")
    list_filter = ("sender", "content_type")
    search_fields = ("case__case_number", "content", "sender_display_name", "session_reference")
    ordering = ("-sent_at",)


@admin.register(CaseDocumentLink)
class CaseDocumentLinkAdmin(admin.ModelAdmin):
    list_display = ("case", "name", "knowledge_upload", "captured_at")
    search_fields = ("case__case_number", "name", "external_document_id")
    ordering = ("-captured_at",)


class CaseHistoryInline(admin.TabularInline):
    model = CaseHistoryEntry
    extra = 0
    fields = ("occurred_at", "source", "summary", "session_reference")
    readonly_fields = ("occurred_at", "summary")
    ordering = ("-occurred_at",)
    show_change_link = True


class CaseMessageInline(admin.TabularInline):
    model = CaseMessage
    extra = 0
    fields = ("sent_at", "sender", "sender_display_name", "content")
    readonly_fields = ("sent_at", "content")
    ordering = ("-sent_at",)
    show_change_link = True


class CaseDocumentInline(admin.TabularInline):
    model = CaseDocumentLink
    extra = 0
    fields = ("name", "knowledge_upload", "document_url", "captured_at")
    readonly_fields = ("captured_at",)
    show_change_link = True


@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = (
        "case_number",
        "title",
        "business_profile",
        "priority",
        "status",
        "customer_display",
        "started_at",
        "updated_at",
    )
    list_filter = ("status", "priority", "business_profile")
    search_fields = (
        "case_number",
        "title",
        "description",
        "customer__display_name",
        "customer_snapshot",
    )
    ordering = ("-started_at",)
    readonly_fields = ("case_number", "started_at", "updated_at", "closed_at")
    inlines = (CaseHistoryInline, CaseMessageInline, CaseDocumentInline)
    autocomplete_fields = ("business_profile", "agent_profile", "customer")

    @admin.display(description="Customer")
    def customer_display(self, obj: Case) -> str:
        if obj.customer:
            return obj.customer.display_name
        snapshot = obj.customer_snapshot or {}
        return snapshot.get("display_name") or snapshot.get("name") or "Unattributed"
