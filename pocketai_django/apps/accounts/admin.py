from django.contrib import admin
from django.contrib.auth import logout
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import AnonymousUser

from .models import AgentProfile, BusinessProfile, KnowledgeUpload, RegistrationSession, User


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


@admin.register(AgentProfile)
class AgentProfileAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "business_profile", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("name", "business_profile__name", "user__email")
    ordering = ("-updated_at",)


@admin.register(KnowledgeUpload)
class KnowledgeUploadAdmin(admin.ModelAdmin):
    list_display = ("id", "display_name", "business_profile", "source_type", "status", "updated_at")
    list_filter = ("source_type", "status", "visibility", "is_sensitive")
    search_fields = ("display_name", "source_name", "legacy_url", "business_profile__name", "user__email")
    ordering = ("-updated_at",)
