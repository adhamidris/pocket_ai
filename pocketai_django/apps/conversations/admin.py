from __future__ import annotations

from django.contrib import admin
from django.db.models import Count
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.text import Truncator

from .models import (
    Conversation,
    ConversationFile,
    ConversationMessage,
    ConversationStatus,
)

@admin.action(description="Mark selected conversations as CLOSED")
def close_conversations(modeladmin: admin.ModelAdmin, request, queryset) -> None:
    now = timezone.now()
    queryset.update(status=ConversationStatus.CLOSED, closed_at=now, last_activity_at=now)


@admin.action(description="Mark selected conversations as EXPIRED")
def expire_conversations(modeladmin: admin.ModelAdmin, request, queryset) -> None:
    now = timezone.now()
    queryset.update(status=ConversationStatus.EXPIRED, expires_at=now, closed_at=now, last_activity_at=now)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    date_hierarchy = "started_at"
    list_display = (
        "id",
        "business_profile",
        "agent_profile",
        "channel",
        "status",
        "started_at",
        "last_activity_at",
        "expires_at",
        "closed_at",
        "messages_link",
        "files_link",
        "csat_score",
    )
    list_filter = ("status", "channel", "business_profile")
    search_fields = (
        "id",
        "session_token",
        "business_profile__name",
        "agent_profile__name",
    )
    ordering = ("-started_at",)
    readonly_fields = (
        "id",
        "session_token",
        "started_at",
        "last_activity_at",
        "closed_at",
        "csat_recorded_at",
    )
    autocomplete_fields = ("business_profile", "agent_profile")
    actions = (close_conversations, expire_conversations)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return (
            qs.select_related("business_profile", "agent_profile")
            .annotate(_messages_count=Count("messages", distinct=True))
            .annotate(_files_count=Count("files", distinct=True))
        )

    @admin.display(description="Messages", ordering="_messages_count")
    def messages_link(self, obj: Conversation) -> str:
        count = int(getattr(obj, "_messages_count", 0) or 0)
        url = reverse("admin:conversations_conversationmessage_changelist")
        return format_html('<a href="{}?conversation__id__exact={}">{} </a>', url, obj.id, count)

    @admin.display(description="Files", ordering="_files_count")
    def files_link(self, obj: Conversation) -> str:
        count = int(getattr(obj, "_files_count", 0) or 0)
        url = reverse("admin:conversations_conversationfile_changelist")
        return format_html('<a href="{}?conversation__id__exact={}">{} </a>', url, obj.id, count)


@admin.register(ConversationMessage)
class ConversationMessageAdmin(admin.ModelAdmin):
    date_hierarchy = "sent_at"
    list_display = ("sent_at", "conversation", "sender", "body_preview")
    list_filter = ("sender", "sent_at")
    search_fields = ("conversation__session_token", "conversation__id", "body")
    ordering = ("-sent_at",)
    readonly_fields = ("id", "created_at")
    autocomplete_fields = ("conversation",)

    @admin.display(description="Body")
    def body_preview(self, obj: ConversationMessage) -> str:
        return Truncator(obj.body).chars(180)


@admin.register(ConversationFile)
class ConversationFileAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = ("created_at", "conversation", "business_profile", "status", "kind", "sender", "filename", "size_bytes")
    list_filter = ("status", "kind", "sender", "business_profile")
    search_fields = ("conversation__session_token", "conversation__id", "filename", "checksum_sha256", "storage_path")
    ordering = ("-created_at",)
    readonly_fields = ("id", "created_at", "updated_at")
    autocomplete_fields = ("conversation", "business_profile")
