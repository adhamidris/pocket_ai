from django.contrib import admin

from .models import (
    AgentMcpToolSetting,
    McpConnection,
    McpConnectionAgentOptOut,
    McpConnectionAuditEvent,
    McpConnectionTestJob,
    McpConnectionToolSetting,
    McpToolOutputArtifact,
)


@admin.register(McpConnection)
class McpConnectionAdmin(admin.ModelAdmin):
    list_display = ("name", "business_profile", "status", "source_type", "default_approval_mode", "updated_at")
    list_filter = ("status", "source_type", "default_approval_mode")
    search_fields = ("name", "slug", "marketplace_key", "business_profile__name")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("name",)


@admin.register(McpConnectionToolSetting)
class McpConnectionToolSettingAdmin(admin.ModelAdmin):
    list_display = ("connection", "tool_name", "operation_type", "approval_mode", "updated_at")
    list_filter = ("operation_type", "approval_mode")
    search_fields = ("connection__name", "tool_name")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("connection__name", "tool_name")


@admin.register(AgentMcpToolSetting)
class AgentMcpToolSettingAdmin(admin.ModelAdmin):
    list_display = ("agent_profile", "connection", "tool_name", "operation_type", "approval_mode", "updated_at")
    list_filter = ("operation_type", "approval_mode")
    search_fields = ("agent_profile__name", "connection__name", "tool_name")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("agent_profile__name", "connection__name", "tool_name")


@admin.register(McpConnectionAgentOptOut)
class McpConnectionAgentOptOutAdmin(admin.ModelAdmin):
    list_display = ("connection", "agent_profile", "opted_out_by", "opted_out_at")
    list_filter = ("opted_out_at",)
    search_fields = ("connection__name", "agent_profile__name", "opted_out_by__email")
    readonly_fields = ("id", "opted_out_at")
    ordering = ("-opted_out_at",)


@admin.register(McpConnectionAuditEvent)
class McpConnectionAuditEventAdmin(admin.ModelAdmin):
    list_display = ("connection", "business_profile", "action", "actor_user", "occurred_at")
    list_filter = ("action", "occurred_at")
    search_fields = ("connection__name", "business_profile__name", "actor_user__email", "description")
    readonly_fields = (
        "id",
        "business_profile",
        "connection",
        "connection_id_snapshot",
        "actor_user",
        "action",
        "description",
        "metadata",
        "occurred_at",
        "created_at",
    )
    ordering = ("-occurred_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(McpConnectionTestJob)
class McpConnectionTestJobAdmin(admin.ModelAdmin):
    list_display = ("connection", "business_profile", "status", "attempt_count", "run_after", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("connection__name", "business_profile__name", "trigger")
    readonly_fields = (
        "id",
        "business_profile",
        "connection",
        "status",
        "trigger",
        "attempt_count",
        "max_attempts",
        "run_after",
        "lease_expires_at",
        "started_at",
        "finished_at",
        "error_detail",
        "payload",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False


@admin.register(McpToolOutputArtifact)
class McpToolOutputArtifactAdmin(admin.ModelAdmin):
    list_display = ("conversation", "invoked_tool", "remote_connection_name", "status", "is_error", "created_at")
    list_filter = ("is_error", "status", "created_at")
    search_fields = ("conversation__id", "invoked_tool", "remote_tool", "tool_call_id", "tool_event_id")
    readonly_fields = (
        "id",
        "conversation",
        "tool_call_id",
        "tool_event_id",
        "invoked_tool",
        "tool_id",
        "remote_connection_id",
        "remote_connection_name",
        "remote_tool",
        "status",
        "is_error",
        "request",
        "response",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

