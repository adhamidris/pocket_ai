from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse
from django.utils.translation import gettext as _

from apps.crm.flags import crm_v1_enabled
from apps.crm.models import CrmDuplicateSuggestion


def _current_user_name(request: HttpRequest) -> str:
    first = (getattr(request.user, "first_name", "") or "").strip()
    return first or request.user.email


def dashboard_legacy_cases_retired(_request: HttpRequest) -> HttpResponse:
    raise Http404(_("Legacy CRM cases are retired."))


def _current_business(request: HttpRequest):
    business = request.user.business_profiles.order_by("-created_at").first()
    if business is None or not crm_v1_enabled(business):
        raise Http404(_("CRM is not enabled for this business."))
    return business


def _crm_shell_context(request: HttpRequest, business, *, active: str) -> dict[str, object]:
    open_duplicates = CrmDuplicateSuggestion.objects.filter(business_profile=business, status="open").count()
    return {
        "user_name": _current_user_name(request),
        "crm_active": active,
        "crm_business_name": business.name,
        "crm_open_duplicates": open_duplicates,
    }


def _format_file_size(num_bytes: int) -> str:
    value = float(num_bytes or 0)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "bytes":
                return _("%(value)s %(unit)s") % {"value": int(value), "unit": unit}
            return _("%(value).1f %(unit)s") % {"value": value, "unit": unit}
        value /= 1024
    return _("0 bytes")


def _mapping_target_label(target: str) -> str:
    labels = {
        "display_name": _("Display name"),
        "first_name": _("First name"),
        "last_name": _("Last name"),
        "primary_email": _("Primary email"),
        "primary_phone": _("Primary phone"),
        "title": _("Role / title"),
        "name": _("Company name"),
        "website": _("Website"),
    }
    return labels.get(target, target.replace("_", " ").title())
