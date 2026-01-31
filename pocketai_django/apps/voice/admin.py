from __future__ import annotations

from django.contrib import admin

from apps.voice.models import CallSession, VoiceCallAuditEvent, VoiceConfiguration, VoiceCountryPolicy


@admin.register(VoiceCountryPolicy)
class VoiceCountryPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "country",
        "timezone",
        "is_active",
        "service_calls_allowed",
        "marketing_calls_allowed",
        "recording_allowed",
        "updated_at",
    )
    list_filter = ("is_active", "service_calls_allowed", "marketing_calls_allowed", "recording_allowed")
    search_fields = ("country", "timezone")
    ordering = ("country",)


@admin.register(VoiceConfiguration)
class VoiceConfigurationAdmin(admin.ModelAdmin):
    list_display = (
        "business_profile",
        "trust_tier",
        "service_calls_enabled",
        "marketing_calls_enabled",
        "max_concurrent_calls",
        "max_calls_per_day",
        "max_call_duration_seconds",
        "monthly_budget_usd",
        "updated_at",
    )
    list_filter = ("trust_tier", "service_calls_enabled", "marketing_calls_enabled")
    search_fields = ("business_profile__name", "business_profile__id")
    autocomplete_fields = ("business_profile",)
    ordering = ("-updated_at",)


@admin.register(VoiceCallAuditEvent)
class VoiceCallAuditEventAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "business_profile",
        "action",
        "call_session_id_snapshot",
    )
    list_filter = ("action", "occurred_at")
    search_fields = ("business_profile__name", "call_session_id_snapshot")
    autocomplete_fields = ("business_profile", "actor_user", "actor_agent", "call_session")
    ordering = ("-occurred_at",)
    readonly_fields = ("id", "created_at", "occurred_at")


@admin.register(CallSession)
class CallSessionAdmin(admin.ModelAdmin):
    search_fields = ("id", "to_phone_number", "from_phone_number", "twilio_call_sid")
    list_display = ("id", "business_profile", "status", "call_type", "country", "to_phone_number", "created_at")
    list_filter = ("status", "call_type", "country")
    ordering = ("-created_at",)
