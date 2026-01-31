import json

from django import forms
from django.contrib import admin, messages
from django.contrib.auth import logout
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import AnonymousUser
from django.utils.html import format_html

from .models import (
    AgentEmailAccountPolicyOverride,
    AgentProfile,
    BusinessProfile,
    EmailAccount,
    EmailAccountAuditEvent,
    EmailAccountHealthJob,
    IntegrationSyncFrequency,
    KnowledgeAlias,
    KnowledgeEntity,
    KnowledgeFeedbackCase,
    IdentifierColumnMapping,
    IdentifierSchema,
    IntegrationCredentialEvent,
    KnowledgeIntegration,
    KnowledgeIngestionJob,
    KnowledgeUpload,
    KnowledgeVisibility,
    KnowledgeDriftSample,
    OAuthProvider,
    RAGEvaluationRun,
    RegistrationSession,
    TenantMemoryConfiguration,
    TenantMemoryConfigurationAuditEvent,
    User,
)
from apps.rag.evaluation.harness import RAGEvaluationHarness
from apps.accounts.memory_policy_presets import (
    TENANT_MEMORY_POLICY_PRESETS,
    get_tenant_memory_policy_preset,
)


admin.site.site_header = "PocketAI Operations Console"
admin.site.site_title = "PocketAI Admin"
admin.site.index_title = "Platform Management"


def _pocket_admin_has_permission(self, request):
    user = getattr(request, "user", None)
    return bool(user and user.is_active and user.is_superuser)


admin.site.has_permission = _pocket_admin_has_permission.__get__(admin.site, admin.site.__class__)

_admin_original_login = admin.site.login


def _pocket_admin_login(self, request, extra_context=None):
    if request.user.is_authenticated and not self.has_permission(request):
        logout(request)
        request.user = AnonymousUser()
    response = _admin_original_login(request, extra_context)
    if request.user.is_authenticated and self.has_permission(request):
        request.session["auth_entrypoint"] = "admin"
    return response


admin.site.login = _pocket_admin_login.__get__(admin.site, admin.site.__class__)


class MonospaceJSONWidget(forms.Textarea):
    """Smaller helper to provide a consistent monospace textarea widget."""

    def __init__(self, *args, **kwargs):
        attrs = kwargs.setdefault("attrs", {})
        attrs.setdefault("rows", 12)
        attrs.setdefault("class", "vLargeTextField monospace")
        super().__init__(*args, **kwargs)


class KnowledgeIntegrationAdminForm(forms.ModelForm):
    default_sync_frequency = forms.ChoiceField(choices=IntegrationSyncFrequency.choices)
    default_visibility = forms.ChoiceField(choices=KnowledgeVisibility.choices)
    resource_configs = forms.JSONField(
        required=False,
        widget=MonospaceJSONWidget,
        help_text="Provide a JSON list of sheet resources (drive_file_id, sheet_gid, sheet_name, privacy).",
    )

    class Meta:
        model = KnowledgeIntegration
        exclude = ("settings",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        default_vis = KnowledgeVisibility.PRIVATE
        default_freq = IntegrationSyncFrequency.DAILY
        if self.instance and self.instance.pk:
            default_vis = self.instance.get_default_visibility() or default_vis
            default_freq = self.instance.get_default_sync_frequency() or default_freq
            resources = self.instance.resource_configs
            if resources:
                self.fields["resource_configs"].initial = json.dumps(resources, indent=2)
        else:
            self.fields["resource_configs"].initial = json.dumps([], indent=2)
        self.fields["default_visibility"].initial = default_vis
        self.fields["default_sync_frequency"].initial = default_freq

    @staticmethod
    def _sanitize_column_list(value):
        if not isinstance(value, (list, tuple)):
            return []
        sanitized: list[str] = []
        for entry in value:
            if entry is None:
                continue
            text = str(entry).strip()
            if text:
                sanitized.append(text)
        return sanitized

    def clean_resource_configs(self):
        resources = self.cleaned_data.get("resource_configs")
        if not resources:
            return []
        if not isinstance(resources, list):
            raise forms.ValidationError("Resources must be provided as a JSON list.")
        visibility_default = self.cleaned_data.get("default_visibility") or KnowledgeVisibility.PRIVATE
        frequency_default = self.cleaned_data.get("default_sync_frequency") or IntegrationSyncFrequency.DAILY
        valid_visibilities = {choice for choice, _ in KnowledgeVisibility.choices}
        valid_frequencies = {choice for choice, _ in IntegrationSyncFrequency.choices}
        normalized: list[dict[str, object]] = []
        for idx, resource in enumerate(resources, start=1):
            if not isinstance(resource, dict):
                raise forms.ValidationError(f"Resource #{idx} must be an object with metadata.")
            drive_file_id = str(resource.get("drive_file_id") or "").strip()
            sheet_gid = str(resource.get("sheet_gid") or resource.get("sheet_id") or resource.get("gid") or "").strip()
            sheet_name = str(resource.get("sheet_name") or resource.get("tab_name") or resource.get("title") or "").strip()
            drive_file_name = str(resource.get("drive_file_name") or resource.get("drive_file_title") or drive_file_id).strip() or drive_file_id
            if not drive_file_id or not sheet_gid or not sheet_name:
                raise forms.ValidationError(
                    f"Resource #{idx} must include drive_file_id, sheet_gid, and sheet_name values."
                )
            resource_id = str(resource.get("resource_id") or f"{drive_file_id}:{sheet_gid}").strip()
            sync_frequency = str(resource.get("sync_frequency") or frequency_default)
            if sync_frequency not in valid_frequencies:
                raise forms.ValidationError(
                    f"Resource #{idx} has invalid sync_frequency '{sync_frequency}'."
                )
            visibility = str(resource.get("visibility") or visibility_default)
            if visibility not in valid_visibilities:
                raise forms.ValidationError(f"Resource #{idx} has invalid visibility '{visibility}'.")
            column_privacy = resource.get("column_privacy") or {}
            if not isinstance(column_privacy, dict):
                column_privacy = {}
            normalized_privacy = {
                "shared_columns": self._sanitize_column_list(column_privacy.get("shared_columns")),
                "internal_only_columns": self._sanitize_column_list(column_privacy.get("internal_only_columns")),
                "excluded_columns": self._sanitize_column_list(column_privacy.get("excluded_columns")),
            }
            metadata = resource.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {}
            normalized.append(
                {
                    "resource_id": resource_id,
                    "drive_file_id": drive_file_id,
                    "drive_file_name": drive_file_name,
                    "sheet_gid": sheet_gid,
                    "sheet_name": sheet_name,
                    "sync_frequency": sync_frequency,
                    "visibility": visibility,
                    "column_privacy": normalized_privacy,
                    "metadata": metadata,
                }
            )
        return normalized

    def save(self, commit=True):
        instance: KnowledgeIntegration = super().save(commit=False)
        instance.set_default_visibility(self.cleaned_data.get("default_visibility") or KnowledgeVisibility.PRIVATE)
        instance.set_default_sync_frequency(
            self.cleaned_data.get("default_sync_frequency") or IntegrationSyncFrequency.DAILY
        )
        instance.set_resource_configs(self.cleaned_data.get("resource_configs") or [])
        if commit:
            instance.save()
            self.save_m2m()
        return instance


TENANT_MEMORY_CONFIG_AUDIT_FIELDS: tuple[str, ...] = (
    "default_hot_period_days",
    "default_warm_period_days",
    "default_archive_after_days",
    "custom_rules",
    "minimum_retention_days",
    "maximum_retention_days",
    "purge_enabled",
    "legal_hold",
)


def _snapshot_tenant_memory_config(config: TenantMemoryConfiguration) -> dict[str, object]:
    return {field: getattr(config, field) for field in TENANT_MEMORY_CONFIG_AUDIT_FIELDS}


def _diff_tenant_memory_config(before: dict[str, object], after: dict[str, object]) -> list[str]:
    changed: list[str] = []
    for field in TENANT_MEMORY_CONFIG_AUDIT_FIELDS:
        if before.get(field) != after.get(field):
            changed.append(field)
    return changed


def _log_tenant_memory_config_audit_event(
    *,
    business_profile: BusinessProfile,
    actor_user: User | None,
    action: str,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
    description: str = "",
    preset_key: str | None = None,
) -> None:
    before_payload = before or {}
    after_payload = after or {}
    metadata: dict[str, object] = {
        "before": before_payload,
        "after": after_payload,
    }
    changed_fields = _diff_tenant_memory_config(before_payload, after_payload)
    if changed_fields:
        metadata["changed_fields"] = changed_fields
    if preset_key:
        metadata["preset_key"] = preset_key

    TenantMemoryConfigurationAuditEvent.objects.create(
        business_profile=business_profile,
        actor_user=actor_user,
        action=action,
        description=description or "",
        metadata=metadata,
    )


class TenantMemoryConfigurationAdminForm(forms.ModelForm):
    custom_rules = forms.JSONField(
        required=False,
        widget=MonospaceJSONWidget,
        help_text=(
            "JSON object mapping rule keys to {hot,warm,archive} overrides. "
            "Keys can be 'kind:<memory_kind>' (e.g. kind:workflow_state) or substrings."
        ),
    )

    class Meta:
        model = TenantMemoryConfiguration
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        rules = getattr(self.instance, "custom_rules", None) if self.instance and self.instance.pk else None
        if rules:
            self.fields["custom_rules"].initial = json.dumps(rules, indent=2)
        else:
            self.fields["custom_rules"].initial = json.dumps({}, indent=2)

    def clean_custom_rules(self):
        rules = self.cleaned_data.get("custom_rules") or {}
        if not rules:
            return {}
        if not isinstance(rules, dict):
            raise forms.ValidationError("Custom rules must be a JSON object (dictionary).")
        return rules


class TenantMemoryConfigurationInline(admin.StackedInline):
    model = TenantMemoryConfiguration
    form = TenantMemoryConfigurationAdminForm
    extra = 0
    max_num = 1
    can_delete = True
    verbose_name_plural = "Memory policy"
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "default_hot_period_days",
                    "default_warm_period_days",
                    "default_archive_after_days",
                    "minimum_retention_days",
                    "maximum_retention_days",
                    "purge_enabled",
                    "legal_hold",
                    "custom_rules",
                )
            },
        ),
    )


@admin.register(TenantMemoryConfiguration)
class TenantMemoryConfigurationAdmin(admin.ModelAdmin):
    form = TenantMemoryConfigurationAdminForm
    list_display = (
        "business_profile",
        "maximum_retention_days",
        "purge_enabled",
        "legal_hold",
        "updated_at",
    )
    list_filter = ("purge_enabled", "legal_hold")
    search_fields = ("business_profile__name", "business_profile__user__email", "business_profile__id")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-updated_at",)
    actions = (
        "apply_preset_general",
        "apply_preset_banking",
        "apply_preset_healthcare",
        "apply_preset_enterprise",
    )

    def save_model(self, request, obj: TenantMemoryConfiguration, form, change):
        before: dict[str, object] | None = None
        if change and obj.pk:
            try:
                before_obj = TenantMemoryConfiguration.objects.get(pk=obj.pk)
                before = _snapshot_tenant_memory_config(before_obj)
            except TenantMemoryConfiguration.DoesNotExist:
                before = None

        super().save_model(request, obj, form, change)

        after = _snapshot_tenant_memory_config(obj)
        action = (
            TenantMemoryConfigurationAuditEvent.ActionChoices.UPDATED
            if change
            else TenantMemoryConfigurationAuditEvent.ActionChoices.CREATED
        )
        if not change or before != after:
            _log_tenant_memory_config_audit_event(
                business_profile=obj.business_profile,
                actor_user=request.user,
                action=action,
                before=before,
                after=after,
            )

    def delete_model(self, request, obj: TenantMemoryConfiguration):
        before = _snapshot_tenant_memory_config(obj)
        business_profile = obj.business_profile
        super().delete_model(request, obj)
        _log_tenant_memory_config_audit_event(
            business_profile=business_profile,
            actor_user=request.user,
            action=TenantMemoryConfigurationAuditEvent.ActionChoices.DELETED,
            before=before,
            after=None,
        )

    def _apply_preset(self, request, queryset, preset_key: str) -> None:
        preset = get_tenant_memory_policy_preset(preset_key)
        if preset is None:
            self.message_user(request, f"Unknown preset '{preset_key}'.", level=messages.ERROR)
            return

        updated = 0
        for config in queryset:
            before = _snapshot_tenant_memory_config(config)
            for field, value in preset.values.items():
                setattr(config, field, value)
            config.save()
            after = _snapshot_tenant_memory_config(config)
            _log_tenant_memory_config_audit_event(
                business_profile=config.business_profile,
                actor_user=request.user,
                action=TenantMemoryConfigurationAuditEvent.ActionChoices.PRESET_APPLIED,
                before=before,
                after=after,
                preset_key=preset_key,
            )
            updated += 1

        self.message_user(request, f"Applied '{preset.label}' to {updated} tenant(s).", level=messages.SUCCESS)

    @admin.action(description="Apply memory preset: General (Default)")
    def apply_preset_general(self, request, queryset):
        return self._apply_preset(request, queryset, "general")

    @admin.action(description="Apply memory preset: Banking / FinTech (Conservative)")
    def apply_preset_banking(self, request, queryset):
        return self._apply_preset(request, queryset, "banking")

    @admin.action(description="Apply memory preset: Healthcare (Conservative)")
    def apply_preset_healthcare(self, request, queryset):
        return self._apply_preset(request, queryset, "healthcare")

    @admin.action(description="Apply memory preset: Enterprise (Longer Retention)")
    def apply_preset_enterprise(self, request, queryset):
        return self._apply_preset(request, queryset, "enterprise")


@admin.register(TenantMemoryConfigurationAuditEvent)
class TenantMemoryConfigurationAuditEventAdmin(admin.ModelAdmin):
    list_display = ("occurred_at", "business_profile", "action", "actor_user")
    list_filter = ("action",)
    search_fields = ("business_profile__name", "business_profile__id", "actor_user__email")
    ordering = ("-occurred_at",)
    readonly_fields = ("id", "business_profile", "actor_user", "action", "description", "metadata", "occurred_at", "created_at")

@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    model = User
    list_display = ("email", "first_name", "status", "is_staff", "created_at")
    list_filter = ("status", "is_staff", "is_superuser", "is_active")
    ordering = ("-created_at",)
    search_fields = ("email", "first_name", "last_name", "public_id")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "public_id")}),
        (
            "Permissions",
            {
                "fields": (
                    "status",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "created_at", "updated_at")}),
    )
    readonly_fields = ("public_id", "created_at", "updated_at")
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "first_name", "password1", "password2", "status", "is_staff", "is_superuser"),
            },
        ),
    )


@admin.register(RegistrationSession)
class RegistrationSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "current_step", "steps_completed", "is_complete", "updated_at")
    list_filter = ("is_complete", "current_step")
    search_fields = ("id", "user__email", "user__public_id")
    ordering = ("-updated_at",)


@admin.register(BusinessProfile)
class BusinessProfileAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "user", "status", "industry", "updated_at")
    list_filter = ("status", "industry")
    search_fields = ("name", "user__email", "registration_session__id")
    ordering = ("-updated_at",)
    inlines = (TenantMemoryConfigurationInline,)

    def save_formset(self, request, form, formset, change):
        if getattr(formset, "model", None) is TenantMemoryConfiguration:
            actor_user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None

            before_by_pk: dict[object, dict[str, object]] = {}
            for inline_form in formset.forms:
                if not inline_form.has_changed():
                    continue
                instance: TenantMemoryConfiguration = inline_form.instance
                if not instance.pk:
                    continue
                try:
                    before_obj = TenantMemoryConfiguration.objects.get(pk=instance.pk)
                    before_by_pk[instance.pk] = _snapshot_tenant_memory_config(before_obj)
                except TenantMemoryConfiguration.DoesNotExist:
                    before_by_pk[instance.pk] = _snapshot_tenant_memory_config(instance)

            deleted_snapshots: dict[object, dict[str, object]] = {}
            for obj in getattr(formset, "deleted_objects", []):
                if not getattr(obj, "pk", None):
                    continue
                try:
                    deleted_snapshots[obj.pk] = _snapshot_tenant_memory_config(
                        TenantMemoryConfiguration.objects.get(pk=obj.pk)
                    )
                except TenantMemoryConfiguration.DoesNotExist:
                    deleted_snapshots[obj.pk] = _snapshot_tenant_memory_config(obj)

            instances = formset.save(commit=False)
            for obj in instances:
                created = obj.pk is None
                before = None if created else before_by_pk.get(obj.pk)
                obj.save()
                after = _snapshot_tenant_memory_config(obj)
                action = (
                    TenantMemoryConfigurationAuditEvent.ActionChoices.CREATED
                    if created
                    else TenantMemoryConfigurationAuditEvent.ActionChoices.UPDATED
                )
                if created or before != after:
                    _log_tenant_memory_config_audit_event(
                        business_profile=obj.business_profile,
                        actor_user=actor_user,
                        action=action,
                        before=before,
                        after=after,
                    )

            formset.save_m2m()

            for obj in getattr(formset, "deleted_objects", []):
                before = deleted_snapshots.get(getattr(obj, "pk", None), _snapshot_tenant_memory_config(obj))
                business_profile = obj.business_profile
                obj.delete()
                _log_tenant_memory_config_audit_event(
                    business_profile=business_profile,
                    actor_user=actor_user,
                    action=TenantMemoryConfigurationAuditEvent.ActionChoices.DELETED,
                    before=before,
                    after=None,
                )
            return

        super().save_formset(request, form, formset, change)


@admin.register(AgentProfile)
class AgentProfileAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "business_profile", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("name", "business_profile__name", "user__email")
    ordering = ("-updated_at",)


@admin.register(KnowledgeIntegration)
class KnowledgeIntegrationAdmin(admin.ModelAdmin):
    form = KnowledgeIntegrationAdminForm
    list_display = ("name", "business_profile", "integration_type", "status", "last_synced_at", "resource_total")
    list_filter = ("integration_type", "status")
    search_fields = ("name", "business_profile__name", "external_account_id")
    ordering = ("name",)
    readonly_fields = ("credential_state", "last_synced_at", "sync_error", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("business_profile", "created_by", "name", "slug", "integration_type", "status")}),
        ("Connection", {"fields": ("external_account_id", "credential_state", "metadata", "last_synced_at", "sync_error")}),
        ("Sync Configuration", {"fields": ("default_sync_frequency", "default_visibility", "resource_configs")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Resources")
    def resource_total(self, obj: KnowledgeIntegration) -> int:
        return len(obj.resource_configs)

    @admin.display(description="Credential state")
    def credential_state(self, obj: KnowledgeIntegration) -> str:
        last_rotated = obj.credentials_last_rotated_at.isoformat() if obj.credentials_last_rotated_at else "Never"
        status = "Stored" if obj.has_credentials() else "Missing"
        return format_html(
            "Status: {}<br>Key version: {}<br>Last rotated: {}<br>Failures: {}",
            status,
            obj.credentials_key_version or "-",
            last_rotated,
            obj.credential_error_count,
        )


@admin.register(EmailAccount)
class EmailAccountAdmin(admin.ModelAdmin):
    list_display = ("id", "business_profile", "user", "provider", "email_address", "status", "send_mode", "credentials_state", "updated_at")
    list_filter = ("provider", "status", "send_mode")
    search_fields = ("email_address", "external_account_id", "user__email", "business_profile__name")
    readonly_fields = ("credentials_state", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("business_profile", "user", "provider", "email_address", "external_account_id", "status")}),
        ("Send Policy", {"fields": ("send_mode", "policy_config")}),
        ("Metadata", {"fields": ("metadata", "last_error")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Credentials")
    def credentials_state(self, obj: EmailAccount) -> str:
        return "set" if obj.has_credentials() else "missing"


@admin.register(AgentEmailAccountPolicyOverride)
class AgentEmailAccountPolicyOverrideAdmin(admin.ModelAdmin):
    list_display = ("id", "agent_profile", "email_account", "send_mode", "updated_at")
    list_filter = ("send_mode",)
    search_fields = ("agent_profile__name", "email_account__email_address")
    readonly_fields = ("created_at", "updated_at")


@admin.register(EmailAccountAuditEvent)
class EmailAccountAuditEventAdmin(admin.ModelAdmin):
    list_display = ("occurred_at", "business_profile", "email_account", "action", "actor_user")
    list_filter = ("action",)
    search_fields = ("email_account_id_snapshot", "email_account__email_address", "actor_user__email", "business_profile__name")
    readonly_fields = ("id", "created_at")


@admin.register(EmailAccountHealthJob)
class EmailAccountHealthJobAdmin(admin.ModelAdmin):
    list_display = ("id", "business_profile", "email_account", "status", "trigger", "attempt_count", "created_at", "finished_at")
    list_filter = ("status", "trigger")
    search_fields = ("email_account__email_address", "business_profile__name", "error_detail")
    readonly_fields = ("id", "created_at", "updated_at", "started_at", "finished_at", "payload_pretty")
    fieldsets = (
        (None, {"fields": ("id", "business_profile", "email_account", "status", "trigger")}),
        ("Execution", {"fields": ("attempt_count", "max_attempts", "run_after", "lease_expires_at", "started_at", "finished_at")}),
        ("Details", {"fields": ("error_detail", "payload_pretty")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Payload")
    def payload_pretty(self, obj):
        payload = obj.payload or {}
        try:
            return json.dumps(payload, indent=2, sort_keys=True)
        except Exception:
            return str(payload)


@admin.register(IntegrationCredentialEvent)
class IntegrationCredentialEventAdmin(admin.ModelAdmin):
    list_display = ("integration", "event_type", "triggered_by", "created_at")
    list_filter = ("event_type", "created_at")
    search_fields = ("integration__name", "integration__business_profile__name", "triggered_by__email")
    ordering = ("-created_at",)


@admin.register(KnowledgeUpload)
class KnowledgeUploadAdmin(admin.ModelAdmin):
    list_display = ("id", "display_name", "business_profile", "source_type", "integration", "status", "updated_at")
    list_filter = ("source_type", "status", "visibility", "is_sensitive", "integration")
    search_fields = ("display_name", "source_name", "legacy_url", "business_profile__name", "user__email")
    ordering = ("-updated_at",)
    autocomplete_fields = ("integration",)
    readonly_fields = ("quality_report",)

    @admin.display(description="Quality report")
    def quality_report(self, obj: KnowledgeUpload) -> str:
        meta = obj.ingestion_metadata if isinstance(obj.ingestion_metadata, dict) else {}
        report = meta.get("quality_report") if isinstance(meta, dict) else None
        if not report:
            return "-"
        try:
            return format_html("<pre>{}</pre>", json.dumps(report, indent=2, sort_keys=True))
        except Exception:
            return str(report)


class KnowledgeAliasInline(admin.TabularInline):
    model = KnowledgeAlias
    extra = 0
    readonly_fields = ("alias_raw", "alias_normalized", "source", "created_at", "updated_at")


@admin.register(KnowledgeEntity)
class KnowledgeEntityAdmin(admin.ModelAdmin):
    list_display = ("id", "entity_name", "entity_type", "business_profile", "upload", "updated_at")
    list_filter = ("entity_type", "business_profile")
    search_fields = ("entity_name", "primary_label", "upload__display_name", "upload__id")
    ordering = ("-updated_at",)
    inlines = (KnowledgeAliasInline,)


@admin.register(KnowledgeAlias)
class KnowledgeAliasAdmin(admin.ModelAdmin):
    list_display = ("id", "alias_raw", "business_profile", "entity", "source", "updated_at")
    list_filter = ("source", "business_profile")
    search_fields = ("alias_raw", "alias_normalized", "entity__entity_name", "business_profile__name")
    ordering = ("-updated_at",)


class IdentifierColumnInline(admin.TabularInline):
    model = IdentifierColumnMapping
    extra = 0
    fields = ("upload", "sheet_name", "column_name", "column_normalized", "status", "source", "confidence", "created_at", "updated_at")
    readonly_fields = ("column_normalized", "created_at", "updated_at")


@admin.register(IdentifierSchema)
class IdentifierSchemaAdmin(admin.ModelAdmin):
    list_display = ("key", "display_name", "business_profile", "status", "source", "is_required", "updated_at")
    list_filter = ("status", "source", "business_profile")
    search_fields = ("key", "display_name", "business_profile__name")
    ordering = ("-updated_at",)
    inlines = (IdentifierColumnInline,)
    readonly_fields = ("created_at", "updated_at")


@admin.register(IdentifierColumnMapping)
class IdentifierColumnMappingAdmin(admin.ModelAdmin):
    list_display = ("column_name", "identifier", "business_profile", "upload", "status", "source", "confidence", "updated_at")
    list_filter = ("status", "source", "business_profile")
    search_fields = ("column_name", "column_normalized", "identifier__key", "identifier__display_name", "upload__display_name", "upload__id")
    ordering = ("-updated_at",)
    readonly_fields = ("column_normalized", "created_at", "updated_at")


@admin.register(KnowledgeIngestionJob)
class KnowledgeIngestionJobAdmin(admin.ModelAdmin):
    list_display = ("id", "business_profile", "upload", "job_type", "status", "created_at", "started_at", "finished_at")
    list_filter = ("job_type", "status")
    search_fields = ("id", "upload__display_name", "upload__id", "business_profile__name")
    ordering = ("-created_at",)
    readonly_fields = ("id", "created_at", "started_at", "finished_at", "updated_at", "payload_pretty")
    fieldsets = (
        (None, {"fields": ("id", "business_profile", "upload", "job_type", "status", "created_at", "started_at", "finished_at")}),
        ("Execution", {"fields": ("payload_pretty", "error_detail")}),
    )

    @admin.display(description="Payload")
    def payload_pretty(self, obj):
        payload = obj.payload or {}
        try:
            return json.dumps(payload, indent=2, sort_keys=True)
        except Exception:
            return str(payload)


@admin.register(RAGEvaluationRun)
class RAGEvaluationRunAdmin(admin.ModelAdmin):
    list_display = ("slug", "business_profile", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("slug", "business_profile__name")
    readonly_fields = ("metrics_pretty", "latencies_pretty", "thresholds_pretty", "violations_pretty", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("slug", "business_profile", "status", "created_at", "updated_at")}),
        ("Metrics", {"fields": ("metrics_pretty", "latencies_pretty", "thresholds_pretty", "violations_pretty")}),
    )

    @admin.display(description="Metrics")
    def metrics_pretty(self, obj):
        return json.dumps(obj.metrics or {}, indent=2, sort_keys=True)

    @admin.display(description="Latencies")
    def latencies_pretty(self, obj):
        return json.dumps(obj.latencies or {}, indent=2, sort_keys=True)

    @admin.display(description="Thresholds")
    def thresholds_pretty(self, obj):
        return json.dumps(obj.thresholds or {}, indent=2, sort_keys=True)

    @admin.display(description="Violations")
    def violations_pretty(self, obj):
        return json.dumps(obj.violations or {}, indent=2, sort_keys=True)


@admin.register(KnowledgeDriftSample)
class KnowledgeDriftSampleAdmin(admin.ModelAdmin):
    list_display = ("sample_kind", "business_profile", "observed_at")
    list_filter = ("sample_kind",)
    search_fields = ("business_profile__name",)
    readonly_fields = ("metrics_pretty", "metadata_pretty", "observed_at")
    fieldsets = (
        (None, {"fields": ("business_profile", "sample_kind", "observed_at")}),
        ("Metrics", {"fields": ("metrics_pretty", "metadata_pretty")}),
    )

    @admin.display(description="Metrics")
    def metrics_pretty(self, obj):
        return json.dumps(obj.metrics or {}, indent=2, sort_keys=True)

    @admin.display(description="Metadata")
    def metadata_pretty(self, obj):
        return json.dumps(obj.metadata or {}, indent=2, sort_keys=True)


@admin.register(KnowledgeFeedbackCase)
class KnowledgeFeedbackCaseAdmin(admin.ModelAdmin):
    list_display = ("query_preview", "business_profile", "expected_behavior", "is_active", "updated_at")
    list_filter = ("expected_behavior", "is_active")
    search_fields = ("query_text", "business_profile__name")
    actions = ("deactivate_cases", "replay_in_harness")
    readonly_fields = ("conversation_feedback", "created_at", "updated_at")

    @admin.display(description="Query")
    def query_preview(self, obj):
        return obj.query_text[:80]

    @admin.action(description="Mark selected cases inactive")
    def deactivate_cases(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"Marked {updated} cases inactive.")

    @admin.action(description="Replay in RAG harness")
    def replay_in_harness(self, request, queryset):
        harness = RAGEvaluationHarness(enforce_thresholds=False)
        for case in queryset:
            try:
                observation = harness.replay_feedback_case(case)
                self.message_user(
                    request,
                    f"{case.query_text[:40]} → top1={observation.top1} status={observation.status}",
                )
            except Exception as exc:
                self.message_user(request, f"Failed to replay {case}: {exc}", level=messages.ERROR)


class OAuthProviderAdminForm(forms.ModelForm):
    client_secret = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep the existing secret. Provide a new value to rotate.",
    )
    scopes = forms.JSONField(
        required=False,
        widget=MonospaceJSONWidget,
        help_text="JSON list of OAuth scopes for this provider.",
    )
    marketplace_keys = forms.JSONField(
        required=False,
        widget=MonospaceJSONWidget,
        help_text="JSON list of marketplace keys that should use this provider.",
    )

    class Meta:
        model = OAuthProvider
        fields = (
            "key",
            "name",
            "authorization_url",
            "token_url",
            "client_id",
            "client_secret",
            "scopes",
            "marketplace_keys",
            "is_active",
        )

    def _clean_list_field(self, field_name: str) -> list[str]:
        value = self.cleaned_data.get(field_name)
        if not value:
            return []
        if not isinstance(value, list):
            raise forms.ValidationError("Value must be a JSON list.")
        normalized: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                normalized.append(text)
        return normalized

    def clean_scopes(self):
        return self._clean_list_field("scopes")

    def clean_marketplace_keys(self):
        return self._clean_list_field("marketplace_keys")

    def save(self, commit=True):
        instance: OAuthProvider = super().save(commit=False)
        secret = self.cleaned_data.get("client_secret")
        if secret:
            instance.set_client_secret(secret)
        if commit:
            instance.save()
            self.save_m2m()
        return instance


@admin.register(OAuthProvider)
class OAuthProviderAdmin(admin.ModelAdmin):
    form = OAuthProviderAdminForm
    list_display = ("key", "name", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("key", "name")
    readonly_fields = (
        "client_secret_encrypted",
        "client_secret_key_version",
        "client_secret_last_rotated_at",
        "client_secret_error_count",
        "created_at",
        "updated_at",
    )
