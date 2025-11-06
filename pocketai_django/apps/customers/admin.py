from __future__ import annotations

from django.contrib import admin

from .forms import CustomerAdminForm
from .models import (
    Customer,
    CustomerActivity,
    CustomerContactPoint,
    CustomerNote,
)


class CustomerContactPointInline(admin.TabularInline):
    model = CustomerContactPoint
    extra = 0
    fields = ("contact_type", "label", "value", "is_primary", "metadata")
    readonly_fields = ("created_at",)
    ordering = ("-is_primary", "contact_type")


class CustomerNoteInline(admin.TabularInline):
    model = CustomerNote
    extra = 0
    fields = ("author_type", "content", "is_pinned", "created_at")
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    form = CustomerAdminForm
    list_display = (
        "display_name",
        "business_profile",
        "record_origin",
        "record_state",
        "is_placeholder",
        "primary_email",
        "primary_phone",
        "updated_at",
    )
    list_filter = ("record_state", "record_origin", "is_placeholder", "business_profile")
    search_fields = ("display_name", "primary_email", "primary_phone", "public_id")
    ordering = ("-updated_at",)
    readonly_fields = ("public_id", "created_at", "updated_at", "first_seen_at", "last_interaction_at")
    autocomplete_fields = ("business_profile", "agent_profile", "created_by")
    inlines = (CustomerContactPointInline, CustomerNoteInline)


@admin.register(CustomerActivity)
class CustomerActivityAdmin(admin.ModelAdmin):
    list_display = ("customer", "activity_type", "actor_type", "subject", "occurred_at")
    list_filter = ("activity_type", "actor_type", "occurred_at")
    search_fields = ("customer__display_name", "subject", "metadata")
    ordering = ("-occurred_at",)
    autocomplete_fields = ("customer", "case", "business_profile", "agent_profile")


@admin.register(CustomerContactPoint)
class CustomerContactPointAdmin(admin.ModelAdmin):
    list_display = ("customer", "contact_type", "label", "value", "is_primary")
    list_filter = ("contact_type", "is_primary")
    search_fields = ("customer__display_name", "value", "normalized_value")
    ordering = ("contact_type", "label")
    autocomplete_fields = ("customer",)


@admin.register(CustomerNote)
class CustomerNoteAdmin(admin.ModelAdmin):
    list_display = ("customer", "case", "author_type", "is_pinned", "created_at")
    list_filter = ("author_type", "is_pinned", "created_at")
    search_fields = ("customer__display_name", "content")
    ordering = ("-created_at",)
    autocomplete_fields = ("customer", "case")
