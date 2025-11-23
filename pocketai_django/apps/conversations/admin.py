from django.contrib import admin

from .models import IdentifierEvent


@admin.register(IdentifierEvent)
class IdentifierEventAdmin(admin.ModelAdmin):
    list_display = ("business_profile", "status", "tool", "match_policy", "upload_id", "created_at")
    list_filter = ("status", "tool", "match_policy", "business_profile")
    search_fields = ("business_profile__name", "conversation__id", "upload_id")
    readonly_fields = ("created_at",)
