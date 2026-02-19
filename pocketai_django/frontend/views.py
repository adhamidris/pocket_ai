from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import uuid

from http import HTTPStatus

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping
from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect, render
from django.test.client import RequestFactory
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify
from django.utils.translation import get_language, gettext_lazy as _
from django.views.decorators.http import require_http_methods

from pocketai.language import normalize_language_code

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    IntegrationSyncFrequency,
    KnowledgeIntegrationStatus,
    KnowledgeIntegrationType,
    KnowledgeSourceType,
    KnowledgeStatus,
    RegistrationSession,
)
from apps.knowledge.models import (
    KnowledgeDriftSample,
    KnowledgeUpload,
    KnowledgeUploadFile,
    KnowledgeUploadText,
    KnowledgeUploadUrl,
)
from apps.integrations.models import KnowledgeIntegration
from apps.rag.query_analytics import build_query_analytics_report
from apps.cases.models import Case, CaseStatus
from apps.conversations.models import Conversation, ConversationSender
from apps.customers.models import Customer
from apps.accounts.agents import (
    AgentListValidationError,
    agent_identifier,
    display_role_label,
    display_tone_label,
    initials_from_name,
    list_agents,
)
from apps.accounts.action_controls import list_action_settings
from apps.accounts.registration import KnowledgeUploadError
from apps.cases.services import list_cases
from apps.customers.services import list_customers
from apps.knowledge.documents import DocumentListValidationError, list_documents
from apps.knowledge.knowledge_ingestion import queue_ingestion_job
from apps.api.chat_portal import bootstrap_session as bootstrap_session_view
from apps.api.views import start_google_drive_oauth as start_google_drive_oauth_view


PORTAL_BOOTSTRAP_SCRIPT_ID = "portal-bootstrap-data"
_portal_request_factory = RequestFactory()
logger = logging.getLogger(__name__)
QUERY_ANALYTICS_SAMPLE_LIMIT = 5000
QUERY_ANALYTICS_WINDOWS: tuple[tuple[int, str], ...] = (
    (24, "24h"),
    (24 * 7, "7d"),
    (24 * 30, "30d"),
)

KNOWLEDGE_UPLOAD_SIMPLE_TYPES: tuple[tuple[str, str], ...] = (
    (KnowledgeSourceType.FILE, _("File Upload")),
    (KnowledgeSourceType.LINK, _("External Link")),
    (KnowledgeSourceType.TEXT, _("Manual Entry")),
)

INTEGRATION_TYPE_DESCRIPTIONS = {
    KnowledgeIntegrationType.GOOGLE_DRIVE: _(
        "Sync Google Sheets automatically to keep SOPs and trackers up to date."
    ),
    KnowledgeIntegrationType.NOTION: _("Mirror Notion pages into the knowledge base."),
    KnowledgeIntegrationType.ZENDESK: _("Import help-center articles from Zendesk Guide."),
    KnowledgeIntegrationType.HUBSPOT: _("Bring HubSpot knowledge articles into Pocket AI."),
    KnowledgeIntegrationType.SLACK: _("Capture curated Slack posts as living documentation."),
    KnowledgeIntegrationType.CONFLUENCE: _("Sync wiki spaces from Confluence."),
    KnowledgeIntegrationType.CUSTOM: _("Custom connector managed by your team."),
}


def _call_portal_bootstrap_api(
    request: HttpRequest,
    *,
    business_slug: str,
    agent_slug: str,
    existing_session_token: str | None,
    metadata: dict,
) -> dict:
    payload = {
        "business_slug": business_slug,
        "agent_slug": agent_slug,
        "session_token": existing_session_token,
        "metadata": metadata,
    }
    api_request = _portal_request_factory.post(
        reverse("api:chat-portal-session"),
        data=json.dumps(payload),
        content_type="application/json",
    )
    api_request.user = getattr(request, "user", None)
    api_request.COOKIES = request.COOKIES.copy()
    api_request.META.update(
        {
            "REMOTE_ADDR": request.META.get("REMOTE_ADDR", ""),
            "HTTP_USER_AGENT": request.META.get("HTTP_USER_AGENT", ""),
            "HTTP_REFERER": request.META.get("HTTP_REFERER", ""),
        }
    )
    response = bootstrap_session_view(api_request)
    if response.status_code == 404:
        raise Http404(_("Chat portal not found"))
    if response.status_code >= 400:
        raise Http404(_("Unable to start chat session"))
    try:
        return json.loads(response.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:  # pragma: no cover - defensive
        raise Http404(_("Invalid bootstrap payload")) from exc


def _format_dashboard_datetime(value: datetime | str | None) -> str | None:
    if not value:
        return None
    dt: datetime | None
    if isinstance(value, str):
        dt = parse_datetime(value)
    else:
        dt = value
    if not dt:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    local_dt = timezone.localtime(dt)
    hour = local_dt.strftime("%I").lstrip("0") or "0"
    minute = local_dt.strftime("%M")
    ampm = local_dt.strftime("%p")
    month = local_dt.strftime("%b")
    return f"{month} {local_dt.day}, {hour}:{minute} {ampm}"


def _integration_status_badge(status: str) -> str:
    palette = {
        KnowledgeIntegrationStatus.CONNECTED: "text-emerald-600",
        KnowledgeIntegrationStatus.SYNCING: "text-blue-600",
        KnowledgeIntegrationStatus.ERROR: "text-rose-600",
        KnowledgeIntegrationStatus.DISCONNECTED: "text-amber-600",
    }
    return palette.get(status, "text-slate-500")


def _format_integration_frequency(frequency: str | None) -> str:
    if not frequency:
        return _("Manual")
    try:
        return IntegrationSyncFrequency(frequency).label
    except ValueError:
        return str(frequency).replace("_", " ").title()


def _describe_integration_type(integration_type: str) -> str:
    return INTEGRATION_TYPE_DESCRIPTIONS.get(
        integration_type,
        _("Keep this data source in sync with Pocket AI."),
    )


def _serialize_dashboard_integration(integration: KnowledgeIntegration) -> dict[str, object]:
    metadata = integration.metadata or {}
    sync_stats = metadata.get("sync_stats") or {}
    schedule = integration.get_sync_schedule()
    description = _describe_integration_type(integration.integration_type)
    last_sync = _format_dashboard_datetime(integration.last_synced_at)
    next_sync = _format_dashboard_datetime(schedule.get("next_run_at"))
    frequency = schedule.get("frequency") or integration.get_default_sync_frequency()
    frequency_label = _format_integration_frequency(frequency)
    rows_ingested = sync_stats.get("rows_ingested")
    status_note = None
    if integration.status == KnowledgeIntegrationStatus.DISCONNECTED:
        status_note = _("Connection needs attention. Reconnect to resume syncing.")
    elif integration.status == KnowledgeIntegrationStatus.ERROR and not integration.sync_error:
        status_note = _("Sync failed. Review credentials and try again.")
    sheets_url = ""
    sync_url = ""
    if integration.integration_type == KnowledgeIntegrationType.GOOGLE_DRIVE:
        sheets_url = reverse("api:integrations-sheets", args=[integration.id])
        sync_url = reverse("api:integrations-google-sync")
    return {
        "id": str(integration.id),
        "name": integration.name,
        "description": description,
        "status": integration.status,
        "status_label": integration.get_status_display(),
        "status_class": _integration_status_badge(integration.status),
        "last_sync": last_sync,
        "next_sync": next_sync,
        "resource_count": len(integration.resource_configs or []),
        "rows_ingested": rows_ingested,
        "sync_error": integration.sync_error,
        "status_note": status_note,
        "sync_frequency": frequency,
        "sync_frequency_label": frequency_label,
        "is_attention": integration.status in {
            KnowledgeIntegrationStatus.ERROR,
            KnowledgeIntegrationStatus.DISCONNECTED,
        },
        "default_visibility": integration.get_default_visibility(),
        "default_sync_frequency": integration.get_default_sync_frequency(),
        "api": {
            "sheets": sheets_url,
            "sync": sync_url,
        },
    }


def _gather_dashboard_integrations(business: BusinessProfile | None) -> list[dict[str, object]]:
    if not business:
        return []
    integrations = (
        KnowledgeIntegration.objects.filter(business_profile=business)
        .order_by("name")
    )
    return [_serialize_dashboard_integration(integration) for integration in integrations]


def _mobile_app_section() -> Dict[str, object]:
    return {
        "title": _("Try the mobile app"),
        "stores": [
            {
                "label": _("Google Play"),
                "href": "#play",
                "icon": """<svg width="30" height="30" viewBox="0 0 512 512" aria-hidden="true"><path fill="currentColor" d="M325.3 234.3 90.7 28.6C79 19 64 24.7 64 39.3v433.4c0 14.7 15 20.3 26.7 10.7l234.6-205.7c9.3-8.1 9.3-23.1 0-30.4z"/><linearGradient id="g2-app" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#34a853"/><stop offset="100%" stop-color="#4285f4"/></linearGradient><path fill="url(#g2-app)" d="M421.9 213.8 360.4 178 325.3 234.3c9.3 8.1 9.3 23.1 0 30.4l35.1 56.3 61.5-35.8c18.5-10.8 18.5-38.5 0-49.4z"/></svg>""",
            },
            {
                "label": _("App Store"),
                "href": "#store",
                "icon": """<svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M16.365 1.43c0 1.14-.47 2.25-1.2 3.05-.76.83-2.01 1.47-3.12 1.39-.13-1.13.38-2.27 1.14-3.06.79-.85 2.12-1.46 3.18-1.38zm3.54 16.3c-.61 1.36-.9 1.95-1.68 3.15-1.09 1.67-2.63 3.75-4.53 3.75-1.7 0-2.14-1.1-4.46-1.1-2.34 0-2.83 1.1-4.53 1.1-1.92 0-3.36-1.8-4.45-3.46C-.02 17.9-.4 14.49 1.3 12.23c1.1-1.54 2.86-2.51 4.85-2.55 1.9-.04 3.69 1.28 4.46 1.28.77 0 2.54-1.58 4.3-1.35 1.47.17 2.85.76 3.88 1.73-3.52 1.93-2.95 6.97.1 7.49z"/></svg>""",
            },
        ],
    }


def _legal_section(
    section_id: str,
    title: str,
    paragraphs: List[str] | None = None,
    bullets: List[str] | None = None,
) -> Dict[str, object]:
    body: List[Dict[str, object]] = []
    for text in paragraphs or []:
        body.append({"type": "paragraph", "text": text})
    if bullets:
        body.append({"type": "list", "items": bullets})
    return {"id": section_id, "title": title, "body": body}


def _current_user_name(request: HttpRequest) -> str:
    if not request.user.is_authenticated:
        return ""
    user = request.user
    first = getattr(user, "first_name", "")
    if isinstance(first, str) and first.strip():
        return first.strip()
    if hasattr(user, "get_short_name"):
        short = (user.get_short_name() or "").strip()
        if short:
            return short
    if hasattr(user, "get_username"):
        return user.get_username()
    return str(user)


def _format_ratio_percent(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    number = max(0.0, min(1.0, number))
    return f"{number * 100:.1f}%"


def _parse_result_count(metrics: Mapping[str, object]) -> int | None:
    for key in ("result_count", "snippet_count"):
        raw = metrics.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def _humanize_metric_key(raw: str) -> str:
    value = (raw or "").strip().replace("_", " ")
    return value.title() if value else _("Unknown")


def _counter_rows(counter: Mapping[str, object], *, total: int, limit: int | None = None) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key, raw_value in counter.items():
        try:
            count = int(raw_value)
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "key": key,
                "label": _humanize_metric_key(str(key)),
                "count": count,
                "rate": (count / total) if total else 0.0,
                "rate_label": _format_ratio_percent((count / total) if total else 0.0),
            }
        )
    if limit is not None:
        return rows[:limit]
    return rows


def _rag_window_hours(raw_value: str | None) -> int:
    allowed = {hours for hours, _label in QUERY_ANALYTICS_WINDOWS}
    try:
        parsed = int(str(raw_value or "").strip())
    except (TypeError, ValueError):
        return 24
    return parsed if parsed in allowed else 24


def _build_window_links(*, selected_business_id: str) -> list[dict[str, object]]:
    base_path = reverse("frontend:dashboard-rag-analytics")
    links: list[dict[str, object]] = []
    for hours, label in QUERY_ANALYTICS_WINDOWS:
        payload: dict[str, str | int] = {"hours": hours}
        if selected_business_id:
            payload["business_id"] = selected_business_id
        links.append(
            {
                "hours": hours,
                "label": label,
                "url": f"{base_path}?{urlencode(payload)}",
            }
        )
    return links


def _compute_business_rows(samples: list[dict[str, object]], *, limit: int = 12) -> list[dict[str, object]]:
    buckets: dict[str, dict[str, int]] = {}
    for row in samples:
        business_id = row.get("business_profile_id")
        if business_id is None:
            continue
        metrics = row.get("metrics")
        if not isinstance(metrics, Mapping):
            continue
        key = str(business_id)
        bucket = buckets.setdefault(
            key,
            {
                "queries": 0,
                "ok": 0,
                "empty": 0,
                "not_found": 0,
                "throttled": 0,
                "error": 0,
            },
        )
        status = str(metrics.get("status") or "unknown").strip().lower() or "unknown"
        result_count = _parse_result_count(metrics)
        bucket["queries"] += 1
        if status == "ok":
            bucket["ok"] += 1
        if status == "not_found":
            bucket["not_found"] += 1
        if status == "throttled":
            bucket["throttled"] += 1
        if status in {"error", "constraint_error"}:
            bucket["error"] += 1
        if result_count is not None and result_count == 0 and status in {"ok", "not_found", "unknown"}:
            bucket["empty"] += 1

    if not buckets:
        return []

    names = {
        str(item["id"]): item["name"] or _("Unknown business")
        for item in BusinessProfile.objects.filter(id__in=list(buckets.keys())).values("id", "name")
    }
    ranked = sorted(
        buckets.items(),
        key=lambda item: (-item[1]["queries"], item[0]),
    )
    rows: list[dict[str, object]] = []
    for business_id, stats in ranked[:limit]:
        total = max(1, int(stats["queries"]))
        rows.append(
            {
                "business_id": business_id,
                "business_name": names.get(business_id, _("Unknown business")),
                "queries": int(stats["queries"]),
                "ok_rate": stats["ok"] / total,
                "ok_rate_label": _format_ratio_percent(stats["ok"] / total),
                "empty_rate": stats["empty"] / total,
                "empty_rate_label": _format_ratio_percent(stats["empty"] / total),
                "throttled_rate": stats["throttled"] / total,
                "throttled_rate_label": _format_ratio_percent(stats["throttled"] / total),
                "error_rate": stats["error"] / total,
                "error_rate_label": _format_ratio_percent(stats["error"] / total),
            }
        )
    return rows


def _case_priority_class(priority: str) -> str:
    mapping = {
        "critical": "border-transparent bg-red-500/10 text-red-600",
        "high": "border-transparent bg-orange-500/10 text-orange-600",
        "medium": "border-transparent bg-amber-500/10 text-amber-600",
        "low": "border-transparent bg-emerald-500/10 text-emerald-600",
    }
    if not priority:
        return "border-transparent bg-muted/60 text-muted-foreground"
    return mapping.get(priority.lower(), "border-transparent bg-muted/60 text-muted-foreground")


def _case_status_class(status: str) -> str:
    mapping = {
        "open": "border-transparent bg-emerald-500/10 text-emerald-600",
        "closed": "border-transparent bg-muted/60 text-muted-foreground",
        "resolved": "border-transparent bg-blue-500/10 text-blue-600",
        "escalated": "border-transparent bg-rose-500/10 text-rose-600",
    }
    if not status:
        return "border-transparent bg-muted/60 text-muted-foreground"
    return mapping.get(status.lower(), "border-transparent bg-muted/60 text-muted-foreground")


ACTION_BADGE_STYLES = {
    "create_case": {
        "label": "Case created",
        "classes": "border border-emerald-200 bg-emerald-50 text-emerald-700",
    },
    "update_case_status": {
        "label": "Case updated",
        "classes": "border border-teal-200 bg-teal-50 text-teal-600",
    },
    "flag_escalation": {
        "label": "Escalation flagged",
        "classes": "border border-rose-200 bg-rose-50 text-rose-600",
    },
    "create_customer": {
        "label": "Customer created",
        "classes": "border border-blue-200 bg-blue-50 text-blue-600",
    },
    "update_customer": {
        "label": "Customer updated",
        "classes": "border border-indigo-200 bg-indigo-50 text-indigo-600",
    },
    "create_lead": {
        "label": "Lead captured",
        "classes": "border border-sky-200 bg-sky-50 text-sky-600",
    },
    "create_appointment": {
        "label": "Appointment logged",
        "classes": "border border-amber-200 bg-amber-50 text-amber-700",
    },
}

ACTION_BADGE_DEFAULT = {
    "label": "Action applied",
    "classes": "border border-border/70 bg-muted/40 text-foreground",
}


def _format_datetime_label(value: datetime | None) -> str:
    if not value:
        return "—"
    return value.strftime("%b %d, %Y %I:%M %p")


def _describe_action_detail(metadata: dict[str, Any]) -> str:
    if not isinstance(metadata, dict):
        return ""
    if metadata.get("case_number"):
        return f"#{metadata['case_number']}"
    if metadata.get("display_name"):
        return str(metadata["display_name"])
    if metadata.get("reason"):
        return str(metadata["reason"])
    if metadata.get("status"):
        return str(metadata["status"]).replace("_", " ")
    return ""


def _format_action_badges(actions: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    formatted: list[dict[str, str]] = []
    if not actions:
        return formatted
    for action in actions:
        if not isinstance(action, dict):
            continue
        key = str(action.get("action") or "").lower()
        config = ACTION_BADGE_STYLES.get(key, ACTION_BADGE_DEFAULT)
        status = str(action.get("status") or "").lower()
        classes = config["classes"]
        if status == "failed":
            classes = "border border-rose-200 bg-rose-50 text-rose-600"
        formatted.append(
            {
                "label": config["label"],
                "classes": classes,
                "detail": _describe_action_detail(action.get("metadata") or {}),
                "status": status,
            }
        )
    return formatted


def _format_citations(items: list[Any] | None) -> list[dict[str, str | None]]:
    formatted: list[dict[str, str | None]] = []
    if not items:
        return formatted
    for item in items:
        if isinstance(item, dict):
            formatted.append(
                {
                    "title": item.get("title") or _("Knowledge snippet"),
                    "source": item.get("source"),
                }
            )
        elif isinstance(item, str):
            formatted.append({"title": item, "source": None})
        else:
            formatted.append({"title": str(item), "source": None})
    return formatted


def _format_diagnostics(data: dict[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(data, dict):
        return []
    entries: list[dict[str, str]] = []
    if data.get("llm_strategy"):
        entries.append({"label": _("LLM strategy"), "value": str(data["llm_strategy"])})
    if isinstance(data.get("planned_action_count"), (int, float)):
        entries.append({"label": _("Planned actions"), "value": str(data["planned_action_count"])})
    if isinstance(data.get("extraction_count"), (int, float)):
        entries.append({"label": _("Extractions"), "value": str(data["extraction_count"])})
    if isinstance(data.get("citations"), list) and data["citations"]:
        joined = ", ".join(str(value) for value in data["citations"][:4])
        entries.append({"label": _("Knowledge"), "value": joined})
    return entries


def landing(request: HttpRequest) -> HttpResponse:
    """Render landing page with server-authored copy."""
    hero = {
        "title_prefix": _("AI Powered"),
        "title_highlight": _("Customer Service"),
        "subtitle": _(
            "Deliver instant, intelligent support 24/7 with our AI-powered platform. "
            "Reduce response times by 90% and delight your customers."
        ),
        "primary_cta": {"label": _("Start Free Trial"), "href": "/register"},
        "secondary_cta": {"label": _("Watch Demo"), "href": "#demo"},
        "stats": [
            {
                "id": "faster-response",
                "label": _("Faster Response"),
                "target": 90,
                "suffix": "%",
                "format": "integer",
            },
            {
                "id": "ai-support",
                "label": _("AI Support"),
                "target": 24,
                "suffix": "/7",
                "format": "hours",
            },
            {
                "id": "happy-customers",
                "label": _("Happy Customers"),
                "target": 10_000,
                "suffix": "",
                "format": "thousands-plus",
            },
        ],
        "store_links": [
            {
                "id": "google-play",
                "label_top": _("GET IT ON"),
                "label_bottom": _("Google Play"),
                "href": "#play",
                "icon": """<svg width="30" height="30" viewBox="0 0 512 512" aria-hidden="true"><defs><linearGradient id="hero-google-play" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#34a853"/><stop offset="100%" stop-color="#4285f4"/></linearGradient></defs><path fill="currentColor" d="M325.3 234.3 90.7 28.6C79 19 64 24.7 64 39.3v433.4c0 14.7 15 20.3 26.7 10.7l234.6-205.7c9.3-8.1 9.3-23.1 0-30.4z"/><path fill="url(#hero-google-play)" d="M421.9 213.8 360.4 178 325.3 234.3c9.3 8.1 9.3 23.1 0 30.4l35.1 56.3 61.5-35.8c18.5-10.8 18.5-38.5 0-49.4z"/></svg>""",
            },
            {
                "id": "app-store",
                "label_top": _("Download on the"),
                "label_bottom": _("App Store"),
                "href": "#store",
                "icon": """<svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M16.365 1.43c0 1.14-.47 2.25-1.2 3.05-.76.83-2.01 1.47-3.12 1.39-.13-1.13.38-2.27 1.14-3.06.79-.85 2.12-1.46 3.18-1.38zm3.54 16.3c-.61 1.36-.9 1.95-1.68 3.15-1.09 1.67-2.63 3.75-4.53 3.75-1.7 0-2.14-1.1-4.46-1.1-2.34 0-2.83 1.1-4.53 1.1-1.92 0-3.36-1.8-4.45-3.46C-.02 17.9-.4 14.49 1.3 12.23c1.1-1.54 2.86-2.51 4.85-2.55 1.9-.04 3.69 1.28 4.46 1.28.77 0 2.54-1.58 4.3-1.35 1.47.17 2.85.76 3.88 1.73-3.52 1.93-2.95 6.97.1 7.49z"/></svg>""",
            },
        ],
        "demo": {
            "browser_bar": "chat.pocket.ai",
            "online_label": _("Online"),
            "input_placeholder": _("This is a demo - try the real widget below! →"),
            "scenarios": [
                {
                    "agent_name": "Nancy",
                    "job_title": _("E-Commerce Support Agent"),
                    "conversation": [
                        {
                            "id": 1,
                            "text": _("Hi! I need help with my order #12345"),
                            "is_bot": False,
                            "delay": 1000,
                        },
                        {
                            "id": 2,
                            "text": _("Hello, I'm Nancy. I'd be glad to help. I'll check that order now."),
                            "is_bot": True,
                            "delay": 1400,
                            "spinner_text": _("Searching order details..."),
                        },
                        {
                            "id": 3,
                            "text": _(
                                "Thanks for waiting. Your order shipped yesterday and should arrive tomorrow by 3 PM. Tracking: TR123456789."
                            ),
                            "is_bot": True,
                            "delay": 1600,
                            "spinner_text": _("Checking shipping status..."),
                        },
                        {
                            "id": 4,
                            "text": _("Perfect. Can I change the delivery address?"),
                            "is_bot": False,
                            "delay": 1200,
                        },
                        {
                            "id": 5,
                            "text": _("Certainly. What is the new address?"),
                            "is_bot": True,
                            "delay": 1200,
                            "spinner_text": _("Preparing address update..."),
                        },
                        {
                            "id": 6,
                            "text": _("123 New Street, Los Angeles, CA 90210"),
                            "is_bot": False,
                            "delay": 1300,
                        },
                        {
                            "id": 7,
                            "text": _("All set. I've updated the address. Anything else I can assist with today?"),
                            "is_bot": True,
                            "delay": 1400,
                            "spinner_text": _("Saving changes..."),
                        },
                        {
                            "id": 8,
                            "text": _("No, that's all. Thank you, Nancy."),
                            "is_bot": False,
                            "delay": 1100,
                        },
                        {
                            "id": 9,
                            "text": _("You're welcome. Happy to help."),
                            "is_bot": True,
                            "delay": 1400,
                            "spinner_text": _("Wrapping up..."),
                        },
                    ],
                },
                {
                    "agent_name": "Jack",
                    "job_title": _("Banking Assistance Agent"),
                    "conversation": [
                        {
                            "id": 1,
                            "text": _("Hi. I believe the interest on my credit card was calculated incorrectly."),
                            "is_bot": False,
                            "delay": 1100,
                        },
                        {
                            "id": 2,
                            "text": _(
                                "Hello, this is Jack. I can clarify. Interest is calculated daily on the carried balance and summed for the billing cycle."
                            ),
                            "is_bot": True,
                            "delay": 1600,
                            "spinner_text": _("Reviewing account policy..."),
                        },
                        {
                            "id": 3,
                            "text": _(
                                "For example: 8 days at AED 5,000 and 22 days at AED 2,000 produce a blended amount based on each daily balance."
                            ),
                            "is_bot": True,
                            "delay": 1700,
                            "spinner_text": _("Calculating interest..."),
                        },
                        {
                            "id": 4,
                            "text": _("That helps. Could you send me the detailed breakdown?"),
                            "is_bot": False,
                            "delay": 1200,
                        },
                        {
                            "id": 5,
                            "text": _(
                                "Of course. I've sent a statement breakdown to your registered email. Would you like assistance setting up autopay?"
                            ),
                            "is_bot": True,
                            "delay": 1400,
                            "spinner_text": _("Preparing statement..."),
                        },
                        {
                            "id": 6,
                            "text": _("No, that's fine for now. Thanks, Jack."),
                            "is_bot": False,
                            "delay": 1200,
                        },
                        {
                            "id": 7,
                            "text": _("Anytime. If anything else comes up, I'm here to help."),
                            "is_bot": True,
                            "delay": 1400,
                            "spinner_text": _("Finalizing response..."),
                        },
                    ],
                },
                {
                    "agent_name": "Suzan",
                    "job_title": _("Realtor Agent"),
                    "conversation": [
                        {
                            "id": 1,
                            "text": _("Hello Suzan. Are there any units available in Dubai Marina?"),
                            "is_bot": False,
                            "delay": 1100,
                        },
                        {
                            "id": 2,
                            "text": _("Hello, I'm happy to help. Yes, here's what's currently available:"),
                            "is_bot": True,
                            "delay": 1400,
                            "spinner_text": _("Searching listings..."),
                        },
                        {
                            "id": 3,
                            "text": _(
                                "2BR, 1,320 sqft, Marina view — AED 2.1M.\n3BR, 2,450 sqft, high floor — AED 4.5M.\n1BR, 820 sqft, furnished — AED 1.35M."
                            ),
                            "is_bot": True,
                            "delay": 1700,
                            "spinner_text": _("Compiling matches..."),
                        },
                        {
                            "id": 4,
                            "text": _("Would you like a sales specialist to contact you?"),
                            "is_bot": True,
                            "delay": 1400,
                            "spinner_text": _("Drafting follow-up..."),
                        },
                        {
                            "id": 5,
                            "text": _("Not yet. Could I see some images?"),
                            "is_bot": False,
                            "delay": 1300,
                        },
                        {
                            "id": 6,
                            "text": _("Certainly. Sharing a few photos:"),
                            "is_bot": True,
                            "delay": 1600,
                            "spinner_text": _("Loading photos..."),
                            "images": [
                                "https://images.unsplash.com/photo-1505691723518-36a5ac3b2b8f?w=600&q=80&auto=format&fit=crop",
                                "https://images.unsplash.com/photo-1523217582562-09d0def993a6?w=600&q=80&auto=format&fit=crop",
                                "https://images.unsplash.com/photo-1512917774080-9991f1c4c750?w=600&q=80&auto=format&fit=crop",
                            ],
                        },
                        {
                            "id": 7,
                            "text": _("Looks good. Please have someone contact me at +971 50 123 4567. Name: Ahmed."),
                            "is_bot": False,
                            "delay": 1500,
                        },
                        {
                            "id": 8,
                            "text": _(
                                "Done. I've scheduled a call for today at 4:30 PM. Our specialist Sara will contact you shortly."
                            ),
                            "is_bot": True,
                            "delay": 1600,
                            "spinner_text": _("Booking a call..."),
                        },
                        {
                            "id": 9,
                            "text": _("Great, thank you."),
                            "is_bot": False,
                            "delay": 1200,
                        },
                        {
                            "id": 10,
                            "text": _("You're welcome. I'm here if you need anything else."),
                            "is_bot": True,
                            "delay": 1400,
                            "spinner_text": _("Wrapping up..."),
                        },
                    ],
                },
            ],
        },
    }

    features = {
        "section_title_prefix": _("Everything You Need for"),
        "section_title_highlight": _("Perfect Support"),
        "section_subtitle": "",
        "tabs": [
            {
                "key": "setup",
                "label": _("Easy Setup"),
                "icon": "settings",
                "title": _("From sign-up to live in minutes"),
                "promo": _(
                    "Create your account, connect your business data, configure the agent, and share the chat link instantly—everywhere."
                ),
                "bullets": [
                    {"icon": "check-circle-2", "label": _("Guided, no-code onboarding")},
                    {"icon": "settings", "label": _("Business profile & preferences")},
                    {"icon": "link", "label": _("Instant chat portal link")},
                    {"icon": "globe", "label": _("Omnichannel social & WhatsApp")},
                ],
            },
            {
                "key": "agents",
                "label": _("Multiple Agents"),
                "icon": "users",
                "title": _("Scale with specialized, brand-aligned agents"),
                "promo": _(
                    "Spin up dedicated agents for sales, support, onboarding, and more. Each agent carries your tone and executes actions confidently."
                ),
                "bullets": [
                    {"icon": "check-circle-2", "label": _("Customizable personas and tone")},
                    {"icon": "zap", "label": _("Actionable workflows and tools")},
                    {"icon": "clock", "label": _("24/7 availability across timezones")},
                    {"icon": "shield", "label": _("Modern, fine-tuned LLM models")},
                ],
            },
            {
                "key": "kb",
                "label": _("Knowledge Base"),
                "icon": "book-open",
                "title": _("Instant business intelligence for your agents"),
                "promo": _(
                    "Upload SOPs and docs, sync help centers and sites. Retrieval-augmented generation gives agents precise, grounded answers from your materials."
                ),
                "bullets": [
                    {"icon": "link", "label": _("One-click uploads & syncs")},
                    {"icon": "check-circle-2", "label": _("Automatic chunking & embeddings")},
                    {"icon": "shield", "label": _("Citations & guardrails for trust")},
                    {"icon": "zap", "label": _("Realtime refresh & invalidation")},
                ],
            },
            {
                "key": "crm",
                "label": _("Flexible CRM"),
                "icon": "layers",
                "title": _("Build the CRM your workflows deserve"),
                "promo": _(
                    "Compose a flexible CRM—add or remove tabs, define data parameters to collect, track customer profiles, and manage insights your way."
                ),
                "bullets": [
                    {"icon": "layers", "label": _("Dynamic tabs & fields")},
                    {"icon": "users", "label": _("Customer profiles & segments")},
                    {"icon": "bar-chart-3", "label": _("Insight management & exports")},
                    {"icon": "link", "label": _("Integrations: HubSpot, and more")},
                ],
            },
            {
                "key": "integrations",
                "label": _("Integrations"),
                "icon": "link",
                "title": _("Connect your stack in minutes"),
                "promo": _(
                    "Plug into tools your team already uses — CRM, support, messaging, and automation platforms."
                ),
                "bullets": [],
            },
            {
                "key": "dashboard",
                "label": _("Operations"),
                "icon": "layout-dashboard",
                "title": _("Run operations with clarity and control"),
                "promo": _(
                    "Governance and operations: queue visibility, live sessions, agent routing, and scheduled reports—everything leaders need to steer performance."
                ),
                "bullets": [
                    {"icon": "message-square", "label": _("Live queue & session views")},
                    {"icon": "users", "label": _("Routing & assignment rules")},
                    {"icon": "bar-chart-3", "label": _("Scheduled reports & alerts")},
                    {"icon": "shield", "label": _("Roles, audit logs, and SSO")},
                ],
            },
            {
                "key": "billing",
                "label": _("Billing"),
                "icon": "credit-card",
                "title": _("Pay for outcomes—not idle seats"),
                "promo": _(
                    "Activate agents when you need them. Use wage-based billing, predictable packages, and smart notifications to stay on budget."
                ),
                "bullets": [
                    {"icon": "credit-card", "label": _("Wage-based activation")},
                    {"icon": "zap", "label": _("Usage packages")},
                    {"icon": "clock", "label": _("Credit depletion alerts")},
                    {"icon": "shield", "label": _("Spend caps & schedules")},
                ],
            },
        ],
        "integrations": [
            {"name": "HubSpot", "slug": "hubspot"},
            {"name": "Slack", "slug": "slack"},
            {"name": "Zapier", "slug": "zapier"},
            {"name": "WhatsApp", "slug": "whatsapp"},
            {"name": "Gmail", "slug": "gmail"},
            {"name": "Shopify", "slug": "shopify"},
            {"name": "Zendesk", "slug": "zendesk"},
            {"name": "Intercom", "slug": "intercom"},
        ],
    }

    testimonials = {
        "title_prefix": _("Customers who"),
        "title_highlight": _("build differently"),
        "subtitle": _("Real teams, real results. Built with speed, reliability, and brand in mind."),
        "items": [
            {
                "quote": _(
                    "Pocket helped us cut first response time from hours to minutes. Our customers finally feel heard instantly."
                ),
                "author": "Sofia Martinez",
                "role": _("Director of Customer Experience, Luma"),
            },
            {
                "quote": _(
                    "The AI assistant handles 80% of inquiries, freeing our agents to focus on complex cases and high-value work."
                ),
                "author": "Elliot Rhodes",
                "role": _("Support Operations Lead, Northbeam"),
            },
        ],
    }

    trusted_by = {
        "title": _("Trusted by"),
        "brands": [
            {"name": name, "slug": slug}
            for name, slug in [
                ("Google", "google"),
                ("Apple", "apple"),
                ("Stripe", "stripe"),
                ("Shopify", "shopify"),
                ("Netflix", "netflix"),
                ("Uber", "uber"),
                ("Airbnb", "airbnb"),
                ("Slack", "slack"),
                ("Spotify", "spotify"),
                ("Meta", "meta"),
                ("PayPal", "paypal"),
                ("Samsung", "samsung"),
                ("TikTok", "tiktok"),
            ]
        ]
        * 2,
    }

    mobile_app = _mobile_app_section()

    pricing = {
        "flexible": _("Flexible pricing"),
        "choose": _("Choose what fits "),
        "motion": _("your motion"),
        "tabs": [
            {"key": "wage", "label": _("Wage based")},
            {"key": "packages", "label": _("Packages")},
            {"key": "self", "label": _("One-time setup (self-hosted)")},
        ],
        "subheader": {
            "packages": _(
                "Simple plans for any stage. Switch billing to see savings with yearly."
            ),
            "wage": _(
                "Prepay a minimum credit (wage) to activate agents and features, then scale usage."
            ),
            "self": _(
                "Deploy on your own infrastructure with your database, custom integrations, and add-ons."
            ),
        },
        "billing_cycle": {
            "monthly": _("Monthly"),
            "yearly": _("Yearly"),
            "suffix_monthly": _("/mo"),
            "suffix_yearly": _("/mo · billed yearly"),
            "trial": _("7-day free trial"),
            "get_started": _("Get started"),
            "add_credit": _("Add credit"),
            "flexible": _("Flexible"),
            "min_credit": _("Minimum credit (wage)"),
            "activates": _("Activates agents and unlocks features. Credit is consumed by usage."),
            "users_choice_badge": _("Users' Choice"),
        },
        "packages": [
            {
                "tier": _("Plus"),
                "description": _("For frontliners who talk to customers daily — fits any industry."),
                "monthly": 29,
                "yearly": 24,
                "features": [
                    _("1 agent"),
                    _("Branded chat portal"),
                    _("Core knowledge base"),
                    _("Inbox + basic analytics"),
                    _("Email transcripts"),
                ],
            },
            {
                "tier": _("Pro"),
                "badge": _("Users' Choice"),
                "description": _("For SMBs — multiple agents and advanced workflows."),
                "monthly": 89,
                "yearly": 69,
                "features": [
                    _("Up to 3 agents"),
                    _("Advanced knowledge base + citations"),
                    _("Workflows and tools (actions)"),
                    _("CRM profiles + segments"),
                    _("Reports & scheduled alerts"),
                ],
            },
            {
                "tier": _("Enterprise"),
                "description": _("For large teams — security, scale, and customization."),
                "monthly": 249,
                "yearly": 199,
                "features": [
                    _("Unlimited agents"),
                    _("SSO, roles & audit logs"),
                    _("Custom routing & priority queues"),
                    _("Integrations (HubSpot, webhooks)"),
                    _("Premium support & SLA"),
                ],
            },
        ],
        "wage_plans": [
            {
                "id": "starter",
                "label": _("Starter"),
                "min_credit": 50,
                "bullets": [
                    _("1 agent active"),
                    _("Up to 3k assisted messages"),
                    _("All core features included"),
                    _("Community support"),
                ],
            },
            {
                "id": "growth",
                "label": _("Growth"),
                "min_credit": 200,
                "bullets": [
                    _("Up to 3 agents active"),
                    _("Up to 15k assisted messages"),
                    _("Advanced KB + citations"),
                    _("Priority email support"),
                ],
            },
            {
                "id": "scale",
                "label": _("Scale"),
                "min_credit": 1000,
                "bullets": [
                    _("Unlimited agents active"),
                    _("Up to 100k assisted messages"),
                    _("Full platform + integrations"),
                    _("Premium support & SLA"),
                ],
            },
        ],
        "self": {
            "title": _("Self-hosted deployment"),
            "bullets": [
                _("On-prem or private cloud"),
                _("Your database and VPC"),
                _("Custom integrations & add-ons"),
                _("SSO, roles & audit logs"),
                _("Implementation support"),
            ],
            "cta_quote": _("Get a quote"),
            "cta_sales": _("Talk to sales"),
        },
    }

    faq = {
        "title_prefix": _("Frequently asked"),
        "title_highlight": _("questions"),
        "items": [
            {
                "question": _("How do agents learn our business?"),
                "answer": _(
                    "Sync your docs and sites or upload files. Retrieval with citations keeps answers grounded and up to date."
                ),
            },
            {
                "question": _("What happens if the AI doesn't know?"),
                "answer": _(
                    "It can request clarification, route to a human, or create a follow-up with full context—your choice."
                ),
            },
            {
                "question": _("Can I customize tone and behavior?"),
                "answer": _(
                    "Yes. Configure personas, guardrails, tools, and workflows per agent, then test in a live sandbox."
                ),
            },
            {
                "question": _("How does billing work?"),
                "answer": _(
                    "Choose packages or prepay credit (wage) to activate agents. Switch to yearly for savings."
                ),
            },
            {
                "question": _("Is my data secure?"),
                "answer": _("Bank-level encryption, roles and audit logs. Optional SSO and data residency controls."),
            },
        ],
    }

    get_token(request)

    context = {
        "page": {
            "hero": hero,
            "sections": {
                "trusted_by": trusted_by,
                "features": features,
                "testimonials": testimonials,
                "pricing": pricing,
                "faq": faq,
                "mobile_app": mobile_app,
            },
        },
    }
    return render(request, "frontend/index.html", context)


def register(request: HttpRequest) -> HttpResponse:
    """Registration page – initial step mirrored from the React experience."""

    get_token(request)

    # Check for authenticated user with incomplete registration session
    registration_state: dict[str, Any] | None = None
    prefill_business: dict[str, Any] | None = None
    prefill_agent: dict[str, Any] | None = None

    if request.user.is_authenticated:
        # Look for the most recent incomplete registration session
        incomplete_session = (
            RegistrationSession.objects.filter(
                user=request.user,
                is_complete=False,
            )
            .select_related("business_profile", "business_profile__agent_profile")
            .order_by("-created_at")
            .first()
        )

        if incomplete_session:
            # Build state for template hydration
            business = getattr(incomplete_session, "business_profile", None)
            agent = getattr(business, "agent_profile", None) if business else None

            registration_state = {
                "session_id": str(incomplete_session.id),
                "user_public_id": str(request.user.public_id),
                "email": request.user.email,
                "current_step": incomplete_session.current_step,
                "steps_completed": incomplete_session.steps_completed,
                "business_id": str(business.id) if business else None,
                "agent_id": str(agent.id) if agent else None,
            }

            # Pre-fill business form data if available
            if business:
                prefill_business = {
                    "name": business.name or "",
                    "industry": business.industry or "",
                    "industry_key": business.industry_key or "",
                    "line_of_business": business.line_of_business or [],
                    "line_of_business_custom": business.line_of_business_custom or [],
                    "country": business.country or "",
                    "website": business.website or "",
                }

            # Pre-fill agent form data if available
            if agent:
                prefill_agent = {
                    "name": agent.name or "",
                    "role": agent.role or "",
                    "tone": agent.tone or "",
                    "traits": agent.traits or [],
                    "escalation_rule": agent.escalation_rule or "",
                }
        else:
            # User is authenticated but has no incomplete session
            # Check if they have a completed session - redirect to dashboard
            completed_session = RegistrationSession.objects.filter(
                user=request.user,
                is_complete=True,
            ).exists()
            if completed_session:
                return redirect("frontend:dashboard")

    industries = [
        "E-commerce & Retail",
        "SaaS & Software",
        "Financial Services",
        "Healthcare & Life Sciences",
        "Education",
        "Hospitality & Travel",
        "Manufacturing",
        "Logistics & Transportation",
        "Real Estate",
        "Media & Entertainment",
        "Telecommunications",
        "Energy & Utilities",
        "Nonprofit & NGOs",
        "Professional Services",
        "Consumer Services",
    ]

    industry_to_lob_key = {
        "E-commerce & Retail": "E-commerce",
        "SaaS & Software": "SaaS",
        "Financial Services": "Finance",
        "Healthcare & Life Sciences": "Healthcare",
        "Education": "Education",
        "Hospitality & Travel": "Hospitality",
        "Manufacturing": "Manufacturing",
        "Logistics & Transportation": "Logistics",
        "Real Estate": "Real Estate",
        "Media & Entertainment": "Media & Entertainment",
        "Telecommunications": "Telecommunications",
        "Energy & Utilities": "Energy & Utilities",
        "Nonprofit & NGOs": "Nonprofit & NGOs",
        "Professional Services": "Professional Services",
        "Consumer Services": "Consumer Services",
        "Other": "Other",
    }

    line_of_business_map = {
        "E-commerce": [
            "Apparel",
            "Electronics",
            "Beauty & Personal Care",
            "Home & Kitchen",
            "Sports & Outdoors",
            "Groceries",
            "Digital Goods",
            "Handmade & Crafts",
            "Automotive Accessories",
        ],
        "SaaS": [
            "CRM",
            "Marketing Automation",
            "Analytics",
            "Project Management",
            "Customer Support",
            "Developer Tools",
            "Productivity",
            "Security",
            "Billing/Subscriptions",
            "Auth/Identity",
            "Observability",
            "Data Platform",
        ],
        "Finance": [
            "Banking",
            "Lending",
            "Payments",
            "Wealth Management",
            "Insurance",
            "Accounting",
            "Crypto/Blockchain",
            "Trading Platforms",
        ],
        "Healthcare": [
            "Clinics",
            "Telemedicine",
            "Pharmacy",
            "Diagnostics",
            "Medical Devices",
            "Wellness",
            "Electronic Health Records",
        ],
        "Education": [
            "K-12",
            "Higher Education",
            "EdTech Platform",
            "Corporate Training",
            "Test Prep",
            "Language Learning",
            "Tutoring & Coaching",
        ],
        "Hospitality": [
            "Hotels",
            "Restaurants",
            "Catering",
            "Travel & Tours",
            "Venues & Events",
            "Short-Term Rentals",
        ],
        "Manufacturing": [
            "OEM Production",
            "Contract Manufacturing",
            "CNC Machining",
            "Injection Molding",
            "3D Printing",
            "PCB Assembly",
            "Quality Assurance",
            "Procurement & Supply",
            "Packaging",
            "Maintenance (MRO)",
        ],
        "Logistics": [
            "Freight Forwarding",
            "Last-Mile Delivery",
            "Warehousing & Fulfillment",
            "Cold Chain",
            "Customs Brokerage",
            "Fleet Management",
            "Courier",
            "LTL/FTL Trucking",
            "Air Cargo",
            "Ocean Freight",
        ],
        "Real Estate": [
            "Residential Sales",
            "Commercial Leasing",
            "Property Management",
            "Valuation & Appraisal",
            "Real Estate Development",
            "Facility Management",
            "Co-working",
            "Mortgage Brokerage",
            "Title & Escrow",
            "Short-Term Rentals",
        ],
        "Media & Entertainment": [
            "Streaming Subscriptions",
            "OTT Platform",
            "Content Production",
            "Post-Production",
            "Music Publishing",
            "Game Development",
            "Live Events",
            "Digital Advertising",
            "Influencer Campaigns",
            "Licensing & Syndication",
        ],
        "Telecommunications": [
            "Mobile Voice",
            "Fixed Broadband",
            "VoIP",
            "IoT Connectivity",
            "Cloud PBX",
            "SIP Trunking",
            "Managed Networks",
            "5G Solutions",
            "Fiber to the Home",
            "Data Center Colocation",
        ],
        "Energy & Utilities": [
            "Electricity Supply",
            "Natural Gas Supply",
            "Renewable Generation",
            "Solar Installation",
            "Energy Storage",
            "Smart Metering",
            "Demand Response",
            "Energy Trading",
            "EV Charging",
            "Utility Billing",
        ],
        "Nonprofit & NGOs": [
            "Fundraising",
            "Grant Management",
            "Program Delivery",
            "Volunteer Management",
            "Advocacy & Outreach",
            "Education Programs",
            "Healthcare Missions",
            "Disaster Relief",
            "Community Development",
            "Monitoring & Evaluation",
        ],
        "Professional Services": [
            "Consulting",
            "Legal Advisory",
            "Tax & Audit",
            "Accounting",
            "Architecture",
            "Engineering",
            "Design & Creative",
            "Recruitment",
            "IT Consulting",
            "Managed IT",
        ],
        "Consumer Services": [
            "Home Cleaning",
            "Appliance Repair",
            "Beauty & Wellness",
            "Fitness & Training",
            "Tutoring",
            "Pet Care",
            "Event Planning",
            "Photography",
            "Home Renovation",
            "Moving & Storage",
        ],
        "Other": [
            "Consulting",
            "Custom Development",
            "Training & Enablement",
            "Support & Success",
        ],
    }

    countries = [
        {"value": "United States", "flag": "🇺🇸"},
        {"value": "United Kingdom", "flag": "🇬🇧"},
        {"value": "United Arab Emirates", "flag": "🇦🇪"},
        {"value": "Saudi Arabia", "flag": "🇸🇦"},
        {"value": "Egypt", "flag": "🇪🇬"},
        {"value": "France", "flag": "🇫🇷"},
        {"value": "Germany", "flag": "🇩🇪"},
        {"value": "Spain", "flag": "🇪🇸"},
        {"value": "Italy", "flag": "🇮🇹"},
        {"value": "Other", "flag": "🌐"},
    ]

    agent_roles = [
        _("Customer Support Agent"),
        _("Support Specialist"),
        _("Customer Success Representative"),
        _("Sales Support Agent"),
        _("Front Desk Representative"),
        _("Account Manager"),
        _("Helpdesk Agent"),
    ]

    agent_tones = [
        _("Friendly"),
        _("Professional"),
        _("Casual"),
        _("Formal"),
        _("Empathetic"),
        _("Playful"),
    ]

    agent_traits = [
        _("Concise"),
        _("Detailed"),
        _("Curious"),
        _("Patient"),
        _("Proactive"),
        _("Direct"),
        _("Creative"),
    ]

    agent_escalations = [
        _("Never"),
        _("On fallback"),
        _("On negative sentiment"),
        _("On high value"),
        _("Always"),
    ]

    catalog_entity = _("Products & Services")
    uploads_materials = [
        {
            "id": "vision",
            "label": _("Vision"),
            "field": "uploadsVision",
            "pending_field": "uploadsVisionUrl",
            "placeholder": "https://your-site.com/vision",
        },
        {
            "id": "mission",
            "label": _("Mission"),
            "field": "uploadsMission",
            "pending_field": "uploadsMissionUrl",
            "placeholder": "https://your-site.com/mission",
        },
        {
            "id": "catalog",
            "label": _("%(entity)s Catalog") % {"entity": catalog_entity},
            "field": "uploadsCatalog",
            "pending_field": "uploadsCatalogUrl",
            "placeholder": "https://your-site.com/catalog",
        },
        {
            "id": "faqs",
            "label": _("FAQs"),
            "field": "uploadsFaqs",
            "pending_field": "uploadsFaqsUrl",
            "placeholder": "https://your-site.com/faqs",
        },
        {
            "id": "kb",
            "label": _("Knowledge Base"),
            "field": "uploadsKb",
            "pending_field": "uploadsKbUrl",
            "placeholder": "https://help.your-site.com",
        },
        {
            "id": "sops",
            "label": _("SOPs"),
            "field": "uploadsSops",
            "pending_field": "uploadsSopsUrl",
            "placeholder": "https://drive.google.com/...",
        },
        {
            "id": "tc",
            "label": _("T&C"),
            "field": "uploadsTc",
            "pending_field": "uploadsTcUrl",
            "placeholder": "https://your-site.com/terms",
        },
    ]

    paperclip_icon = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05 12.12 20.37a5 5 0 1 1-7.07-7.07L14.37 4.93a3.5 3.5 0 1 1 4.95 4.95L10.12 19.08"/></svg>"""

    context = {
        "page": {
            "title_prefix": _("Create your"),
            "title_highlight": _("Account"),
            "typed_subtitle": _("Your clients, served better."),
            "step_indicator": "form",
            "divider_label": _("OR"),
            "google_label": _("Continue with Google"),
            "cta_label": _("Create Account"),
            "cta_loading": _("Creating..."),
            "google_icon": """<svg width="16" height="16" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36 16.8 36 11 30.2 11 23S16.8 10 24 10c3.8 0 7.2 1.4 9.8 3.7l5.7-5.7C35.5 4.1 30 2 24 2 12 2 2 12 2 24s10 22 22 22 22-10 22-22c0-1.3-.1-2.5-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.4 16.3 18.8 14 24 14c3.8 0 7.2 1.4 9.8 3.7l5.7-5.7C35.5 4.1 30 2 24 2 15.3 2 7.8 7.1 4.2 14.1l2.1.6z"/><path fill="#4CAF50" d="M24 46c6 0 11.5-2.2 15.6-5.8l-7.2-5.9C30.7 35.7 27.6 37 24 37c-5.2 0-9.7-3.3-11.3-7.9l-6.6 5.1C9.7 40.9 16.3 46 24 46z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-1.3 3.8-4.8 6.5-9.3 6.5-5.2 0-9.7-3.3-11.3-7.9l-6.6 5.1C9.7 40.9 16.3 46 24 46c12 0 22-10 22-22 0-1.3-.1-2.5-.4-3.5z"/></svg>""",
            "fields": {
                "first_name": {
                    "label": _("First name"),
                    "placeholder": "John",
                    "error_required": _("First name is required"),
                },
                "email": {
                    "label": _("Email"),
                    "placeholder": "you@company.com",
                    "error_invalid": _("Please enter a valid email"),
                },
                "password": {
                    "label": _("Password"),
                    "placeholder": "••••••••",
                    "error_min": _("Password must be at least 8 characters"),
                },
                "confirm_password": {
                    "label": _("Confirm password"),
                    "placeholder": "••••••••",
                    "error_match": _("Passwords do not match"),
                },
        },
            "have_account_label": _("Already have an account?"),
            "login_label": _("Log in"),
            "business": {
                "title_prefix": _("Business Profile"),
                "title_highlight": _("Setup"),
                "badge": _("Step 2 · Business profile"),
                "subtitle": _("Help us understand your business"),
                "fields": {
                    "business_name": {
                        "label": _("Business name"),
                        "placeholder": "Acme Inc.",
                        "error_required": _("Business name is required"),
                    },
                    "industry": {
                        "label": "Industry",
                        "placeholder": "Search industry",
                        "search_placeholder": "Search industry...",
                        "empty": "Search industry",
                        "no_results": "No industry found.",
                    },
                    "line_of_business": {
                        "label": "Industry niches",
                        "placeholder": "Select niches",
                        "empty": "Select industry first",
                        "search_placeholder": "Search niches",
                        "custom_placeholder": "Add custom",
                        "no_results": "No niches found.",
                    },
                    "country": {
                        "label": _("Country"),
                        "placeholder": "Select country",
                    },
                    "website": {
                        "label": _("Website"),
                        "optional": _("(optional)"),
                        "placeholder": "https://example.com",
                    },
                },
                "buttons": {
                    "back": _("Back"),
                    "next": _("Next"),
                    "loading": _("Saving..."),
                    "add": _("Add"),
                    "done": _("Done"),
                },
            },
            "agent": {
                "title_prefix": _("Agent"),
                "title_highlight": _("Setup"),
                "subtitle": _("Set up your agent basics"),
                "fields": {
                    "name": {
                        "label": _("Agent name"),
                        "placeholder": "e.g., Nancy",
                        "error_required": _("Please enter an agent name"),
                    },
                    "role": {
                        "label": _("Role"),
                        "placeholder": _("Select a role"),
                        "error_required": _("Please select a role"),
                        "options": agent_roles,
                    },
                    "tone": {
                        "label": _("Tone"),
                        "placeholder": _("Select tone"),
                        "error_required": _("Please choose a tone"),
                        "options": agent_tones,
                    },
                    "traits": {
                        "label": _("Traits"),
                        "hint": _("Pick as many as you like"),
                        "options": agent_traits,
                    },
                    "escalation": {
                        "label": _("Escalation rule"),
                        "placeholder": _("Choose escalation rule"),
                        "error_required": _("Please choose when to escalate"),
                        "options": agent_escalations,
                    },
                },
                "buttons": {
                    "back": _("Back"),
                    "next": _("Next"),
                    "loading": _("Saving..."),
                },
            },
            "uploads": {
                "title_prefix": _("Knowledge"),
                "title_highlight": _("Uploads"),
                "subtitle": _("Add links to your key docs so your agent gets smart fast. You can skip this and do it later."),
                "materials_card": {
                    "title": _("Which materials do you want to attach?"),
                    "description": _("Select the knowledge sources you want to add now. You can always come back later."),
                },
                "materials": uploads_materials,
                "empty_state": _("Select at least one material above to start adding links."),
                "attach_label": _("Attach"),
                "attach_icon": paperclip_icon,
                "count_suffix": _("added"),
                "errors": {
                    "invalid_url": _("Enter a valid URL (https://example.com)"),
                    "duplicate_url": _("This link is already attached"),
                    "materials": _("Select at least one material to continue"),
                    "links": _("Add at least one link for each selected source"),
                    "general": _("We couldn't save your uploads. Try again."),
                    "dependencies": _("Complete the earlier steps before finishing registration."),
                },
                "buttons": {
                    "back": _("Back"),
                    "skip": _("Upload later"),
                    "finish": _("Finish"),
                    "loading": _("Finishing…"),
                },
                # where to go after uploads resolves (default in JS if no custom handler prevents)
                "redirect_after": "/dashboard/",
            },
        },
        "business_data": {
            "industries": industries,
            "industry_map": industry_to_lob_key,
            "line_of_business_map": line_of_business_map,
            "countries": countries,
            "max_badges": 2,
        },
        "mobile_app": _mobile_app_section(),
        # Registration resume state (for authenticated users with incomplete registrations)
        "registration_state": registration_state,
        "prefill_business": prefill_business,
        "prefill_agent": prefill_agent,
    }
    return render(request, "frontend/register.html", context)


def privacy_policy(request: HttpRequest) -> HttpResponse:
    today = date.today().strftime("%B %d, %Y")
    sections = [
        _legal_section(
            "overview",
            "1. Overview",
            paragraphs=[
                'Pocket AI Support ("we", "us", "our") provides AI-powered customer service tools including multi-agent chat, retrieval-based knowledge, and CRM capabilities. This Privacy Policy explains how we collect, use, share, and protect your information when you use our website, products, and services (the "Services").',
                "By using the Services, you agree to this Policy. If you do not agree, please discontinue use.",
            ],
        ),
        _legal_section(
            "collection",
            "2. Information We Collect",
            bullets=[
                "Account and Profile: name, email, role, preferences, and authentication identifiers.",
                "Business Data: uploaded SOPs, documents, websites, and help-center sources you connect for retrieval.",
                "Communications: chat transcripts, feedback, and logs from agent and user interactions.",
                "Usage and Device: product usage metrics, approximate location, device and browser details, cookies.",
                "Integrations: third-party identifiers and metadata when you connect services such as HubSpot, Slack, or WhatsApp.",
            ],
        ),
        _legal_section(
            "use",
            "3. How We Use Information",
            bullets=[
                "Provide, operate, and improve the Services and features.",
                "Configure and personalise AI agents to match your brand.",
                "Power retrieval-augmented answers with citations from your data.",
                "Monitor quality, performance, security, and abuse prevention.",
                "Support, troubleshoot, and communicate about the Services.",
                "Comply with legal obligations and enforce terms.",
            ],
        ),
        _legal_section(
            "ai",
            "4. AI Processing and Training",
            paragraphs=[
                "We use large language models and tooling to enable agent capabilities. Unless you opt in to data sharing for model improvement, we do not use your private business content to train foundation models. We may use aggregated, de-identified analytics to improve reliability and safety.",
                "Retrieval sources and citations are stored to provide grounded responses and auditability. You can refresh or remove sources at any time from your account.",
            ],
        ),
        _legal_section(
            "sharing",
            "5. How We Share Information",
            bullets=[
                "Vendors and sub-processors under contractual safeguards.",
                "Integrations you enable, limited to the data required for that service.",
                "Legal and safety requests where disclosure is required to comply with law or protect rights.",
                "Business transfers as part of a merger, acquisition, or asset sale with notice where required.",
            ],
        ),
        _legal_section(
            "intl",
            "6. International Transfers",
            paragraphs=[
                "Your information may be processed in jurisdictions other than your own. We apply safeguards such as standard contractual clauses to protect cross-border transfers in line with applicable law.",
            ],
        ),
        _legal_section(
            "retention",
            "7. Data Retention",
            paragraphs=[
                "We retain information as long as needed to deliver the Services, meet legal obligations, resolve disputes, and enforce agreements. You may request deletion of certain data from within your account or by contacting us.",
            ],
        ),
        _legal_section(
            "rights",
            "8. Your Rights and Choices",
            bullets=[
                "Access, correct, or delete certain personal information.",
                "Export data where applicable.",
                "Opt out of marketing communications.",
                "Control cookies via browser settings.",
                "Disable or remove integrations at any time.",
            ],
            paragraphs=[
                "Regional rights (for example GDPR or CCPA) may apply depending on your location and role. We honour requests in accordance with applicable laws.",
            ],
        ),
        _legal_section(
            "security",
            "9. Security",
            paragraphs=[
                "We implement industry-standard security measures, including encryption in transit and at rest, role-based access controls, and audit logs for enterprise plans. No method of transmission or storage is 100% secure; please use strong credentials and enable SSO where available.",
            ],
        ),
        _legal_section(
            "children",
            "10. Children's Privacy",
            paragraphs=[
                "Our Services are not directed to children under 13 (or the age of digital consent in your region). We do not knowingly collect data from children. If you believe a child has provided personal information, contact us to request deletion.",
            ],
        ),
        _legal_section(
            "changes",
            "11. Changes to this Policy",
            paragraphs=[
                "We may update this Policy from time to time. Material changes will be announced via the website or email. Your continued use of the Services after an update constitutes acceptance of the revised Policy.",
            ],
        ),
        _legal_section(
            "contact",
            "12. Contact Us",
            paragraphs=[
                "For privacy inquiries, requests, or complaints, contact our team at privacy@pocket.ai.",
            ],
        ),
    ]

    context = {
        "page": {
            "title": "Privacy Policy",
            "last_updated": today,
            "sections": sections,
            "include_mobile_promo": True,
            "sections_mobile": _mobile_app_section(),
        },
    }
    return render(request, "frontend/legal/page.html", context)


def terms_of_service(request: HttpRequest) -> HttpResponse:
    today = date.today().strftime("%B %d, %Y")
    sections = [
        _legal_section(
            "intro",
            "1. Introduction",
            paragraphs=[
                'These Terms of Service ("Terms") govern your access to and use of Pocket AI Support\'s website, products, and services (the "Services"). By accessing or using the Services, you agree to be bound by these Terms. If you do not agree, do not use the Services.',
            ],
        ),
        _legal_section(
            "eligibility",
            "2. Eligibility",
            paragraphs=[
                "You must be at least the age of majority in your jurisdiction and have the authority to bind your organisation to these Terms. You represent and warrant that you will use the Services only for lawful purposes.",
            ],
        ),
        _legal_section(
            "account",
            "3. Account Registration and Security",
            bullets=[
                "Provide accurate and complete information when creating an account.",
                "Maintain the security of your credentials and notify us of any breach.",
                "You are responsible for all activities under your account.",
            ],
        ),
        _legal_section(
            "acceptable-use",
            "4. Acceptable Use",
            bullets=[
                "Do not misuse the Services, attempt unauthorised access, or disrupt operations.",
                "Do not upload unlawful, infringing, or harmful content.",
                "Respect usage limits, fair use, and applicable third-party terms.",
                "Do not use outputs to violate rights, privacy, or applicable law.",
            ],
        ),
        _legal_section(
            "customer-data",
            "5. Customer Data and Privacy",
            paragraphs=[
                '"Customer Data" means content you submit to the Services (for example SOPs, documents, websites, chat transcripts, and configuration). You retain ownership of Customer Data. We process Customer Data to provide and improve the Services in line with our Privacy Policy. You are responsible for obtaining all rights and consents required to submit Customer Data.',
            ],
        ),
        _legal_section(
            "ai",
            "6. AI Outputs and Limitations",
            paragraphs=[
                "AI-generated outputs may be probabilistic and may contain errors. Use human oversight where material. Outputs are provided \"as is\" without warranties. Do not rely on outputs for legal, medical, financial, or other professional advice without validation.",
            ],
        ),
        _legal_section(
            "ip",
            "7. Intellectual Property",
            bullets=[
                "We and our licensors retain all rights, title, and interest in the Services, including software, models, and design elements.",
                "You are granted a limited, non-exclusive, non-transferable licence to use the Services in accordance with these Terms.",
                "Feedback you provide may be used by us without obligation.",
            ],
        ),
        _legal_section(
            "billing",
            "8. Billing and Plans",
            paragraphs=[
                "Pricing is described on our website (packages, wage-based credits, and self-hosted options). Fees are non-refundable unless required by law. We may change prices with prior notice. Usage limits and overage policies may apply.",
            ],
        ),
        _legal_section(
            "integrations",
            "9. Integrations and Third-Party Services",
            paragraphs=[
                "When you connect third-party tools (such as HubSpot, Slack, or WhatsApp), you authorise us to exchange necessary data with those services. Third-party terms govern your use of their products. We are not responsible for third-party services.",
            ],
        ),
        _legal_section(
            "security",
            "10. Security",
            paragraphs=[
                "We implement industry-standard measures to protect the Services. No system is completely secure. You are responsible for securing your accounts, endpoints, and integration credentials.",
            ],
        ),
        _legal_section(
            "term",
            "11. Term and Termination",
            bullets=[
                "We may suspend or terminate access for violations of these Terms.",
                "You may stop using the Services at any time.",
                "Upon termination, your right to access the Services ends; certain provisions survive.",
            ],
        ),
        _legal_section(
            "warranty",
            "12. Disclaimers",
            paragraphs=[
                "THE SERVICES ARE PROVIDED \"AS IS\" AND \"AS AVAILABLE\" WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT. WE DO NOT WARRANT THAT THE SERVICES WILL BE ERROR-FREE OR UNINTERRUPTED.",
            ],
        ),
        _legal_section(
            "liability",
            "13. Limitation of Liability",
            paragraphs=[
                "TO THE MAXIMUM EXTENT PERMITTED BY LAW, NEITHER WE NOR OUR LICENSORS SHALL BE LIABLE FOR INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, OR ANY LOSS OF PROFITS, REVENUE, DATA, OR USE. OUR AGGREGATE LIABILITY WILL NOT EXCEED THE FEES PAID BY YOU FOR THE SERVICES IN THE TWELVE (12) MONTHS PRECEDING THE CLAIM.",
            ],
        ),
        _legal_section(
            "indemnity",
            "14. Indemnification",
            paragraphs=[
                "You will defend, indemnify, and hold harmless Pocket AI Support and its affiliates from and against claims arising out of your use of the Services or violation of these Terms, including Customer Data you provide and your use of AI outputs.",
            ],
        ),
        _legal_section(
            "governing-law",
            "15. Governing Law and Dispute Resolution",
            paragraphs=[
                "These Terms are governed by the laws of the jurisdiction where Pocket AI Support is organised, without regard to conflict of law principles. Disputes will be resolved through good-faith negotiations; if unresolved, they shall be brought in competent courts of that jurisdiction.",
            ],
        ),
        _legal_section(
            "changes",
            "16. Changes to these Terms",
            paragraphs=[
                "We may modify these Terms from time to time. Material changes will be communicated via the website or email. Your continued use of the Services after changes become effective constitutes acceptance.",
            ],
        ),
        _legal_section(
            "contact",
            "17. Contact",
            paragraphs=[
                "Questions about these Terms? Contact our team at legal@pocket.ai.",
            ],
        ),
    ]

    context = {
        "page": {
            "title": "Terms of Service",
            "last_updated": today,
            "sections": sections,
            "include_mobile_promo": True,
            "sections_mobile": _mobile_app_section(),
        },
    }
    return render(request, "frontend/legal/page.html", context)


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    user_name = _current_user_name(request)
    dashboard_metrics: List[Dict[str, object]] = []
    conversation_groups: List[Dict[str, object]] = []
    ai_pulse_items: List[Dict[str, object]] = []
    agent_snapshot_items: List[Dict[str, object]] = []
    total_all = sum(len(group.get("entries", [])) for group in conversation_groups)
    total_ai = sum(
        1
        for group in conversation_groups
        for item in group.get("entries", [])
        if item.get("handled_by") == "ai"
    )
    total_human = sum(
        1
        for group in conversation_groups
        for item in group.get("entries", [])
        if item.get("handled_by") == "human"
    )
    context = {
        "user_name": user_name,
        "dashboard_loading": False,
        "dashboard_metrics": dashboard_metrics,
        "dashboard_metrics_empty_message": _("Metrics will appear once you start receiving conversations."),
        "dashboard_alert_message": _("No escalations yet — monitoring continuously."),
        "dashboard_conversation_groups": conversation_groups,
        "dashboard_conversation_counts": {
            "all": total_all,
            "ai": total_ai,
            "human": total_human,
        },
        "dashboard_conversations_empty_message": _(
            "Connect your support channels to start streaming conversations here."
        ),
        "dashboard_ai_pulse": ai_pulse_items,
        "dashboard_ai_pulse_empty_message": _("Metrics will appear once cases begin flowing in."),
        "dashboard_agent_snapshot": agent_snapshot_items,
        "dashboard_agent_snapshot_empty_message": _("Invite your team to see performance insights here."),
    }
    return render(request, "frontend/dashboard.html", context)


@login_required
def dashboard_rag_analytics(request: HttpRequest) -> HttpResponse:
    if not bool(getattr(request.user, "is_superuser", False)):
        raise Http404(_("Page not found."))

    user_name = _current_user_name(request)
    selected_hours = _rag_window_hours(request.GET.get("hours"))
    selected_business_id = (request.GET.get("business_id") or "").strip()
    invalid_business_filter = False

    now = timezone.now()
    window_start = now - timedelta(hours=selected_hours)
    base_qs = KnowledgeDriftSample.objects.filter(
        sample_kind=KnowledgeDriftSample.SampleKind.RETRIEVAL,
        observed_at__gte=window_start,
    )
    rows_qs = base_qs
    if selected_business_id:
        try:
            selected_business_uuid = uuid.UUID(selected_business_id)
        except (TypeError, ValueError):
            invalid_business_filter = True
            selected_business_id = ""
        else:
            rows_qs = rows_qs.filter(business_profile_id=selected_business_uuid)

    rows = list(
        rows_qs.order_by("-observed_at").values(
            "business_profile_id",
            "observed_at",
            "metrics",
            "metadata",
        )[:QUERY_ANALYTICS_SAMPLE_LIMIT]
    )
    report = build_query_analytics_report(rows)

    status = report.get("status") if isinstance(report.get("status"), Mapping) else {}
    latency = report.get("latency_ms") if isinstance(report.get("latency_ms"), Mapping) else {}
    empty = report.get("empty_results") if isinstance(report.get("empty_results"), Mapping) else {}
    total_queries = int(report.get("total_queries") or 0)

    status_counts = status.get("counts") if isinstance(status.get("counts"), Mapping) else {}
    status_rows = _counter_rows(status_counts, total=total_queries)
    intent_rows = _counter_rows(
        report.get("by_intent") if isinstance(report.get("by_intent"), Mapping) else {},
        total=total_queries,
        limit=10,
    )
    error_rows = _counter_rows(
        report.get("by_error_code") if isinstance(report.get("by_error_code"), Mapping) else {},
        total=total_queries,
        limit=10,
    )
    path_rows = _counter_rows(
        report.get("by_path") if isinstance(report.get("by_path"), Mapping) else {},
        total=total_queries,
        limit=10,
    )
    business_rows = _compute_business_rows(rows)

    analytics_cards = [
        {
            "label": _("Total queries"),
            "value": f"{total_queries}",
            "helper": _("Queries captured in this window"),
        },
        {
            "label": _("Success rate"),
            "value": _format_ratio_percent(status.get("ok_rate")),
            "helper": _("Status = ok"),
        },
        {
            "label": _("Empty result rate"),
            "value": _format_ratio_percent(empty.get("rate")),
            "helper": _("Zero retrieval results"),
        },
        {
            "label": _("Throttled rate"),
            "value": _format_ratio_percent(status.get("throttled_rate")),
            "helper": _("Rate-limit events"),
        },
        {
            "label": _("Error rate"),
            "value": _format_ratio_percent(status.get("error_rate")),
            "helper": _("Constraint + hard failures"),
        },
        {
            "label": _("P95 latency"),
            "value": (
                _("—")
                if latency.get("p95") is None
                else f"{float(latency.get('p95')):.0f} ms"
            ),
            "helper": _("Retrieval latency (95th percentile)"),
        },
    ]

    business_count_rows = list(
        base_qs.values("business_profile_id")
        .annotate(sample_count=Count("id"))
        .order_by("-sample_count")[:100]
    )
    business_ids = [str(row["business_profile_id"]) for row in business_count_rows if row.get("business_profile_id") is not None]
    business_name_rows = BusinessProfile.objects.filter(id__in=business_ids).values("id", "name")
    business_names = {
        str(item["id"]): item["name"] or _("Unknown business")
        for item in business_name_rows
    }
    business_options = [
        {
            "id": business_id,
            "name": business_names.get(business_id, _("Unknown business")),
            "count": int(row.get("sample_count") or 0),
        }
        for row in business_count_rows
        if (business_id := str(row.get("business_profile_id") or "")).strip()
    ]

    if selected_business_id and not any(option["id"] == selected_business_id for option in business_options):
        selected_business_name = (
            BusinessProfile.objects.filter(id=selected_business_id).values_list("name", flat=True).first()
        )
        selected_name = selected_business_name or business_names.get(selected_business_id, _("Unknown business"))
        business_options.insert(
            0,
            {
                "id": selected_business_id,
                "name": selected_name,
                "count": 0,
            },
        )

    window_links = _build_window_links(selected_business_id=selected_business_id)
    for item in window_links:
        item["active"] = item["hours"] == selected_hours

    context = {
        "user_name": user_name,
        "selected_window_hours": selected_hours,
        "selected_business_id": selected_business_id,
        "invalid_business_filter": invalid_business_filter,
        "window_start": window_start,
        "window_end": now,
        "window_links": window_links,
        "business_options": business_options,
        "sample_limit": QUERY_ANALYTICS_SAMPLE_LIMIT,
        "sample_count": len(rows),
        "sample_capped": len(rows) >= QUERY_ANALYTICS_SAMPLE_LIMIT,
        "query_analytics": report,
        "analytics_cards": analytics_cards,
        "status_rows": status_rows,
        "intent_rows": intent_rows,
        "error_rows": error_rows,
        "path_rows": path_rows,
        "business_rows": business_rows,
    }
    return render(request, "frontend/rag_analytics.html", context)


@login_required
def dashboard_customers(request: HttpRequest) -> HttpResponse:
    user_name = _current_user_name(request)
    stats = [
        {"label": _("New customers"), "value": None, "helper": _("No data to measure yet")},
        {"label": _("Active customers"), "value": None, "helper": _("No customers yet")},
        {"label": _("Open cases"), "value": None, "helper": _("Create cases to populate data")},
    ]
    customers: list[dict[str, object]] = []
    total_customers = 0
    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)
    business = None
    if request.user.is_authenticated:
        business = request.user.business_profiles.order_by("-created_at").first()

    if business:
        try:
            result = list_customers(business_profile=business, limit=50)
            total_customers = result.total_count
            for item in result.items:
                name = item.display_name or _("Customer")
                tokens = [token for token in name.split() if token]
                if not tokens:
                    initials = _("CU")
                elif len(tokens) == 1:
                    initials = tokens[0][:2].upper()
                else:
                    initials = (tokens[0][0] + tokens[-1][0]).upper()
                state_label = item.state.replace("_", " ").title() if item.state else _("—")
                last_contact = item.last_interaction_at
                customers.append(
                    {
                        "uuid": str(item.id),
                        "name": name,
                        "initials": initials,
                        "email": item.email or "—",
                        "state": item.state or "",
                        "state_label": state_label,
                        "cases_open": item.open_cases,
                        "cases_total": item.total_cases,
                        "last_contact": last_contact.strftime("%b %d, %Y %H:%M") if last_contact else "No activity yet",
                        "last_contact_iso": last_contact.isoformat() if last_contact else "",
                    }
                )
        except Exception:
            pass

        new_customers = Customer.objects.filter(
            business_profile=business,
            created_at__gte=thirty_days_ago,
        ).count()
        active_customers = Customer.objects.filter(
            business_profile=business,
            record_state="active",
        ).count()
        open_cases = Case.objects.filter(business_profile=business, status=CaseStatus.OPEN).count()
        stats = [
            {"label": _("New customers"), "value": new_customers or 0, "helper": _("Last 30 days")},
            {"label": _("Active customers"), "value": active_customers or 0, "helper": _("Currently engaged")},
            {"label": _("Open cases"), "value": open_cases or 0, "helper": _("Customer cases awaiting action")},
        ]

    context = {
        "user_name": user_name,
        "customers_stats": stats,
        "customers_filters": {
            "search_placeholder": _("Search name, email, text…"),
            "lifecycle_label": _("All lifecycle stages"),
            "date_label": _("Any time"),
            "limit": 25,
        },
        "customers_backend_notice": None,
        "customers_auth_notice": None,
        "customers_loading": False,
        "skeleton_rows": range(6),
        "customers": customers,
        "customers_empty_message": _("No customers added yet. Import customers or add one."),
        "customers_showing_count": len(customers),
        "customers_total": total_customers if total_customers else len(customers),
        "customers_has_prev": False,
        "customers_has_next": False,
        "customers_detail_empty_title": _("No customer selected"),
        "customers_detail_empty_message": _("Choose a customer from the table to inspect profiles, activity, and notes."),
    }
    return render(request, "frontend/customers.html", context)


@login_required
def dashboard_agents(request: HttpRequest) -> HttpResponse:
    user_name = _current_user_name(request)
    stats = [
        {"label": _("Active agents"), "value": None, "helper": _("No agents deployed yet")},
        {"label": _("Avg. satisfaction"), "value": None, "helper": _("Scores will populate once conversations start")},
        {"label": _("Automation coverage"), "value": None, "helper": _("Connect channels to calculate coverage")},
    ]
    agents: list[dict[str, object]] = []
    total_agents = 0
    has_error = False
    business = request.user.business_profiles.order_by("-created_at").first() if request.user.is_authenticated else None
    agent_ids: list[uuid.UUID] = []

    def _format_duration(seconds: float | None) -> str | None:
        if not seconds:
            return None
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        minutes, remainder = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m {remainder:02d}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes:02d}m"

    if business:
        try:
            result = list_agents(
                business_profile=business,
                limit=25,
                offset=0,
                sort_by="updated_at",
                order="desc",
            )
            total_agents = result.total
            business_slug = slugify(business.name)
            for item in result.items:
                agent_ids.append(item.id)
                initials = initials_from_name(item.name)
                identifier = agent_identifier(item.id)
                role_label = display_role_label(item.role)
                tone_label = display_tone_label(item.tone) or _("—")
                updated_at = item.updated_at
                updated_label = updated_at.strftime("%b %d, %Y %H:%M") if updated_at else _("—")
                status_code = (item.status or "").lower()
                status_label_map = {
                    "draft": _("Draft"),
                    "review": _("Review"),
                    "active": _("Active"),
                    "paused": _("Paused"),
                    "disabled": _("Disabled"),
                }
                status_label = status_label_map.get(
                    status_code,
                    status_code.replace("_", " ").title() if status_code else _("Draft"),
                )
                shareable_path = ""
                if item.public_slug:
                    shareable_path = f"/{business_slug}/{item.public_slug}".replace("//", "/")
                agents.append(
                    {
                        "uuid": str(item.id),
                        "name": item.name or _("Agent"),
                        "initials": initials,
                        "identifier": identifier,
                        "roles": [role_label],
                        "primary_role": role_label,
                        "role_code": item.role or "",
                        "tone_label": tone_label,
                        "tone_code": item.tone or "",
                        "status": status_label,
                        "status_code": status_code,
                        "conversations": item.conversations or 0,
                        "satisfaction": None,
                        "aht": _format_duration(item.average_handle_seconds),
                        "aht_seconds": item.average_handle_seconds or 0,
                        "escalations": item.escalations or 0,
                        "updated": updated_label,
                        "updated_iso": updated_at.isoformat() if updated_at else "",
                        "last_active_iso": item.last_active_at.isoformat() if item.last_active_at else "",
                        "public_slug": item.public_slug,
                        "shareable_path": shareable_path,
                    }
                )
        except AgentListValidationError:
            has_error = True
        except Exception:
            has_error = True

        if agent_ids:
            profile_lookup = {
                str(profile.id): profile
                for profile in AgentProfile.objects.filter(business_profile=business, id__in=agent_ids)
                .select_related("business_profile")
                .prefetch_related(
                    "action_permissions",
                    Prefetch(
                        "allowed_documents",
                        queryset=KnowledgeUpload.objects.filter(business_profile=business).only("id", "status", "is_active"),
                    ),
                )
                .only("id", "escalation_rule", "business_profile__name", "business_profile__slug")
            }
            active_knowledge_qs = (
                KnowledgeUpload.objects.filter(business_profile=business, is_active=True)
                .exclude(status=KnowledgeStatus.ARCHIVED)
            )
            knowledge_total = active_knowledge_qs.count()
            knowledge_status_counts = {
                row["status"]: row["count"]
                for row in active_knowledge_qs.values("status").annotate(count=Count("id"))
            }
            knowledge_processing_total = knowledge_status_counts.get(KnowledgeStatus.PENDING, 0) + knowledge_status_counts.get(
                KnowledgeStatus.PROCESSING,
                0,
            )
            knowledge_failed_total = knowledge_status_counts.get(KnowledgeStatus.FAILED, 0)

            for agent in agents:
                profile = profile_lookup.get(str(agent.get("uuid") or ""))
                if not profile:
                    continue

                allowed_docs = list(getattr(profile, "allowed_documents", []).all())
                has_restrictions = bool(allowed_docs)

                if knowledge_total == 0:
                    agent["knowledge_mode"] = "missing"
                    agent["knowledge_total"] = 0
                    agent["knowledge_processing"] = 0
                    agent["knowledge_failed"] = 0
                elif has_restrictions:
                    allowed_doc_ids = [doc.id for doc in allowed_docs]
                    scoped_qs = active_knowledge_qs.filter(id__in=allowed_doc_ids).distinct()
                    scoped = scoped_qs.aggregate(
                        total=Count("id", distinct=True),
                        processing=Count(
                            "id",
                            filter=Q(status__in=[KnowledgeStatus.PENDING, KnowledgeStatus.PROCESSING]),
                            distinct=True,
                        ),
                        failed=Count("id", filter=Q(status=KnowledgeStatus.FAILED), distinct=True),
                    )
                    agent["knowledge_mode"] = "select"
                    agent["knowledge_total"] = int(scoped.get("total") or 0)
                    agent["knowledge_processing"] = int(scoped.get("processing") or 0)
                    agent["knowledge_failed"] = int(scoped.get("failed") or 0)
                else:
                    agent["knowledge_mode"] = "all"
                    agent["knowledge_total"] = knowledge_total
                    agent["knowledge_processing"] = knowledge_processing_total
                    agent["knowledge_failed"] = knowledge_failed_total

                action_settings = list_action_settings(profile)
                enabled_lookup = {setting.key: setting.enabled for setting in action_settings}

                def _enabled(key: str) -> bool:
                    return bool(enabled_lookup.get(key))

                knowledge_enabled = _enabled("read_knowledge")
                cases_enabled = _enabled("create_case")
                customers_enabled = _enabled("create_customer") or _enabled("update_customer")
                leads_enabled = _enabled("create_lead")
                appointments_enabled = _enabled("create_appointment")
                escalation_enabled = _enabled("flag_escalation")

                capability_flags = [
                    (_("Knowledge"), knowledge_enabled),
                    (_("Cases"), cases_enabled),
                    (_("Customers"), customers_enabled),
                    (_("Leads"), leads_enabled),
                    (_("Appointments"), appointments_enabled),
                    (_("Escalation"), escalation_enabled),
                ]
                enabled_labels = [label for label, enabled in capability_flags if enabled]
                highlights = enabled_labels[:3]
                agent["capabilities_enabled"] = sum(1 for _label, enabled in capability_flags if enabled)
                agent["capabilities_total"] = len(capability_flags)
                agent["capabilities_highlights"] = highlights
                agent["capabilities_more"] = max(0, len(enabled_labels) - len(highlights))
                agent["escalation_enabled"] = escalation_enabled
                agent["escalation_rule"] = profile.escalation_rule or ""

        active_agents = AgentProfile.objects.filter(business_profile=business, status="active").count()
        total_recorded_agents = AgentProfile.objects.filter(business_profile=business).count()
        case_counts = Case.objects.filter(business_profile=business).aggregate(
            total=Count("id"),
            automated=Count("id", filter=Q(agent_profile__isnull=False)),
        )
        coverage = None
        if case_counts.get("total"):
            coverage = round(
                (case_counts.get("automated", 0) / max(case_counts["total"], 1)) * 100,
            )
        stats = [
            {
                "label": _("Active agents"),
                "value": active_agents or 0,
                "helper": _("%(count)s total") % {"count": total_recorded_agents or total_agents},
            },
            {
                "label": _("Avg. satisfaction"),
                "value": None,
                "helper": _("Scores populate once conversations sync"),
            },
            {
                "label": _("Automation coverage"),
                "value": f"{coverage}%" if coverage is not None else None,
                "helper": _("Cases handled by AI"),
            },
        ]

    context = {
        "user_name": user_name,
        "business_id": str(getattr(business, "id", "")) if business else "",
        "agents_stats": stats,
        "agents_filters": {
            "search_placeholder": _("Search name, ID, role…"),
            "status_label": _("All statuses"),
            "limit": 25,
        },
        "agents_backend_notice": None if business else _("Link a business profile to create agents."),
        "agents_auth_notice": None,
        "agents_loading": False,
        "agents_error_message": _("Unable to load agents right now.") if has_error else None,
        "skeleton_rows": range(6),
        "agents": agents,
        "agents_empty_message": _("No agents created yet. Launch your first AI teammate to get started."),
        "agents_showing_count": len(agents),
        "agents_total": total_agents or len(agents),
        "agents_has_prev": False,
        "agents_has_next": bool(total_agents and total_agents > len(agents)),
        "agents_panel_empty_title": _("No agent selected"),
        "agents_panel_empty_message": _("Choose an agent from the cards to preview configuration and analytics."),
        "agents_modal_roles": [
            _("Support Agent"),
            _("Sales Associate"),
            _("Technical Specialist"),
            _("Customer Success"),
        ],
    }
    return render(request, "frontend/agents.html", context)


@login_required
def dashboard_leads(request: HttpRequest) -> HttpResponse:
    user_name = _current_user_name(request)
    stats = [
        {"label": _("Open leads"), "value": None, "delta": _("Up 0% vs last week")},
        {"label": _("Hot leads"), "value": None, "delta": _("Ready for outreach")},
        {"label": _("Avg. response SLA"), "value": None, "delta": _("< 3h target")},
    ]
    heat_options = [
        {"label": _("All"), "value": "all"},
        {"label": _("Hot"), "value": "hot"},
        {"label": _("Warm"), "value": "warm"},
        {"label": _("Cold"), "value": "cold"},
    ]
    pipeline_breakdown = [
        {"label": _("New"), "helper": _("0 leads")},
        {"label": _("Qualified"), "helper": _("0 leads")},
        {"label": _("Engaged"), "helper": _("0 leads")},
        {"label": _("Negotiation"), "helper": _("0 leads")},
        {"label": _("Closed Won"), "helper": _("0 leads")},
    ]
    context = {
        "user_name": user_name,
        "leads_stats": stats,
        "leads_filters": {
            "search_placeholder": _("Search lead, company, or tag"),
            "stage_label": _("All stages"),
            "owner_label": _("All owners"),
            "range_label": _("Last 14 days"),
        },
        "leads_heat_options": heat_options,
        "leads_heat_active": "all",
        "leads_backend_notice": None,
        "leads_auth_notice": None,
        "leads_loading": False,
        "leads_error_message": None,
        "skeleton_rows": range(6),
        "leads": [],
        "leads_empty_message": _("No leads match this view yet."),
        "leads_showing_count": 0,
        "leads_total": 0,
        "leads_has_prev": False,
        "leads_has_next": False,
        "leads_detail_empty_title": _("Select a lead"),
        "leads_detail_empty_message": _("Choose a lead to review stage, owner activity, and history."),
        "leads_pipeline_breakdown": pipeline_breakdown,
    }
    return render(request, "frontend/leads.html", context)


@login_required
def dashboard_integrations(request: HttpRequest) -> HttpResponse:
    user_name = _current_user_name(request)
    business = _primary_business_for_user(request.user)
    get_token(request)
    context = {
        "user_name": user_name,
        "business_id": str(business.id) if business else "",
    }
    return render(request, "frontend/integrations.html", context)


@login_required
def dashboard_mcp(request: HttpRequest) -> HttpResponse:
    user_name = _current_user_name(request)
    business = _primary_business_for_user(request.user)
    get_token(request)
    context = {
        "user_name": user_name,
        "business_id": str(business.id) if business else "",
    }
    return render(request, "frontend/mcp.html", context)


@login_required
def dashboard_controls(request: HttpRequest) -> HttpResponse:
    user_name = _current_user_name(request)
    business = _primary_business_for_user(request.user)
    get_token(request)
    context = {
        "user_name": user_name,
        "business_id": str(business.id) if business else "",
    }
    return render(request, "frontend/controls.html", context)


@login_required
def dashboard_voice(request: HttpRequest) -> HttpResponse:
    user_name = _current_user_name(request)
    business = _primary_business_for_user(request.user)
    get_token(request)
    context = {
        "user_name": user_name,
        "business_id": str(business.id) if business else "",
    }
    return render(request, "frontend/voice.html", context)


def _format_document_size(size_bytes: int | None) -> str:
    if not size_bytes:
        return _("—")
    size = float(size_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024
        idx += 1
    if idx == 0:
        return f"{int(size)} {units[idx]}"
    return f"{size:.1f} {units[idx]}"


def _format_document_timestamp(value: datetime | None) -> str:
    if not value:
        return _("—")
    localized = timezone.localtime(value)
    language = (get_language() or "").lower()
    if language.startswith("ar"):
        return date_format(localized, "d/m/Y H:i")
    return date_format(localized, "M j, Y H:i")


def _is_arabic_language() -> bool:
    return (get_language() or "").lower().startswith("ar")


def _localized_document_source_label(source_type: str | None, fallback: str | None = None) -> str:
    if _is_arabic_language():
        source_map = {
            KnowledgeSourceType.FILE: "رفع ملف",
            KnowledgeSourceType.LINK: "رابط خارجي",
            KnowledgeSourceType.TEXT: "إدخال يدوي",
            KnowledgeSourceType.INTEGRATION: "مزامنة التكامل",
            KnowledgeSourceType.EMBED: "محتوى مضمَّن",
        }
        fallback_map = {
            "file upload": "رفع ملف",
            "external link": "رابط خارجي",
            "manual entry": "إدخال يدوي",
            "integration sync": "مزامنة التكامل",
            "embedded content": "محتوى مضمَّن",
        }
    else:
        source_map = {
            KnowledgeSourceType.FILE: "File Upload",
            KnowledgeSourceType.LINK: "External Link",
            KnowledgeSourceType.TEXT: "Manual Entry",
            KnowledgeSourceType.INTEGRATION: "Integration Sync",
            KnowledgeSourceType.EMBED: "Embedded Content",
        }
        fallback_map = {
            "file upload": "File Upload",
            "external link": "External Link",
            "manual entry": "Manual Entry",
            "integration sync": "Integration Sync",
            "embedded content": "Embedded Content",
        }
    normalized = (source_type or "").strip().lower()
    if normalized in source_map:
        return source_map[normalized]

    fallback_text = (fallback or "").strip()
    if fallback_text:
        return fallback_map.get(fallback_text.lower(), fallback_text)
    return "غير معروف" if _is_arabic_language() else "Unknown"


def _localized_document_status_label(status: str | None, fallback: str | None = None) -> str:
    if _is_arabic_language():
        status_map = {
            KnowledgeStatus.PENDING: "قيد الانتظار",
            KnowledgeStatus.PROCESSING: "قيد المعالجة",
            KnowledgeStatus.READY: "جاهز",
            KnowledgeStatus.ACTIVE: "نشط",
            KnowledgeStatus.FAILED: "فشل",
            KnowledgeStatus.ARCHIVED: "مؤرشف",
        }
        fallback_map = {
            "pending": "قيد الانتظار",
            "processing": "قيد المعالجة",
            "ready": "جاهز",
            "active": "نشط",
            "failed": "فشل",
            "archived": "مؤرشف",
        }
    else:
        status_map = {
            KnowledgeStatus.PENDING: "Pending",
            KnowledgeStatus.PROCESSING: "Processing",
            KnowledgeStatus.READY: "Ready",
            KnowledgeStatus.ACTIVE: "Active",
            KnowledgeStatus.FAILED: "Failed",
            KnowledgeStatus.ARCHIVED: "Archived",
        }
        fallback_map = {
            "pending": "Pending",
            "processing": "Processing",
            "ready": "Ready",
            "active": "Active",
            "failed": "Failed",
            "archived": "Archived",
        }
    normalized = (status or "").strip().lower()
    if normalized in status_map:
        return status_map[normalized]

    fallback_text = (fallback or "").strip()
    if fallback_text:
        return fallback_map.get(fallback_text.lower(), fallback_text)
    return "غير معروف" if _is_arabic_language() else "Unknown"


def _localized_document_classification(category: str | None, language: str | None) -> str:
    raw_value = (category or "").strip() or (language or "").strip()
    if not raw_value:
        return "عام" if _is_arabic_language() else "General"
    normalized = raw_value.lower()
    if normalized in {"general", "default", "uncategorized"}:
        return "عام" if _is_arabic_language() else "General"
    return raw_value


def _document_status_class(status: str | None) -> str:
    mapping = {
        "ready": "text-emerald-600",
        "active": "text-emerald-600",
        "processing": "text-amber-600",
        "pending": "text-amber-600",
        "failed": "text-rose-600",
        "archived": "text-slate-500",
    }
    normalized = (status or "").lower()
    return mapping.get(normalized, "text-slate-500")


def _document_identifier(doc_id: uuid.UUID) -> str:
    return f"KN-{str(doc_id).split('-')[0].upper()}"


def _wants_json(request: HttpRequest) -> bool:
    accept_header = request.headers.get("accept", "")
    return request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in accept_header


def _serialize_upload_for_dashboard(upload: KnowledgeUpload) -> dict[str, object]:
    updated_at = upload.updated_at
    source_label = _localized_document_source_label(upload.source_type, upload.get_source_type_display())
    status_label = _localized_document_status_label(
        upload.status,
        (upload.status or "").replace("_", " ").title(),
    )
    classification = _localized_document_classification(upload.category, upload.language)
    return {
        "id": str(upload.id),
        "name": upload.display_name or _("Document"),
        "identifier": _document_identifier(upload.id),
        "classification": classification,
        "type_badge": source_label,
        "source_type": upload.source_type,
        "source_label": source_label,
        "status_label": status_label,
        "status_code": upload.status or "",
        "status_badge_class": _document_status_class(upload.status),
        "updated": _format_document_timestamp(updated_at),
        "updated_iso": updated_at.isoformat() if updated_at else "",
        "size_display": _format_document_size(upload.size_bytes),
        "size_bytes": upload.size_bytes or 0,
        "language": upload.language or "",
        "category": upload.category or "",
        "token_count": upload.token_count or 0,
        "is_sensitive": bool(upload.is_sensitive),
        "last_synced_iso": upload.last_synced_at.isoformat() if upload.last_synced_at else "",
        "last_ingested_iso": upload.last_ingested_at.isoformat() if upload.last_ingested_at else "",
        "integration_name": upload.integration.name if upload.integration_id else "",
    }


def _primary_business_for_user(user) -> BusinessProfile | None:
    if not getattr(user, "is_authenticated", False):
        return None
    return user.business_profiles.order_by("-created_at").first()


def _knowledge_storage_root() -> Path:
    root = Path(getattr(settings, "MEDIA_ROOT", settings.BASE_DIR / "var" / "media"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _create_file_upload(
    *,
    request: HttpRequest,
    business: BusinessProfile,
    display_name: str,
    upload_file,
) -> KnowledgeUpload:
    if not upload_file or not getattr(upload_file, "name", "").strip():
        raise KnowledgeUploadError(_("Select a file to upload."), field="knowledge_file")

    root = _knowledge_storage_root()
    storage_dir = root / "knowledge" / str(business.id)
    storage_dir.mkdir(parents=True, exist_ok=True)

    original_name = Path(upload_file.name).name
    stem = slugify(Path(original_name).stem) or "document"
    suffix = Path(original_name).suffix
    unique_name = f"{uuid.uuid4().hex}_{stem}{suffix}"
    destination = storage_dir / unique_name

    checksum = hashlib.sha256()
    with destination.open("wb+") as handle:
        for chunk in upload_file.chunks():
            checksum.update(chunk)
            handle.write(chunk)

    size_bytes = int(getattr(upload_file, "size", destination.stat().st_size))
    label = display_name or original_name
    metadata = {
        "uploaded_via": "dashboard",
        "public_label": label,
    }
    upload = KnowledgeUpload.objects.create(
        business_profile=business,
        user=request.user,
        display_name=label[:255],
        source_type=KnowledgeSourceType.FILE,
        status=KnowledgeStatus.PROCESSING,
        source_name=label[:255],
        size_bytes=size_bytes,
        metadata=metadata,
    )

    KnowledgeUploadFile.objects.create(
        upload=upload,
        filename=original_name[:255],
        content_type=upload_file.content_type or mimetypes.guess_type(original_name)[0] or "",
        storage_path=str(destination.relative_to(root)),
        size_bytes=size_bytes,
        checksum_sha256=checksum.hexdigest(),
        metadata={"original_name": original_name},
    )
    queue_ingestion_job(upload, trigger="dashboard_file_upload")
    return upload


def _create_link_upload(
    *,
    request: HttpRequest,
    business: BusinessProfile,
    display_name: str,
    url_value: str,
) -> KnowledgeUpload:
    validator = URLValidator()
    validator(url_value)
    parsed = urlparse(url_value)
    host = parsed.netloc or parsed.path or url_value
    label = display_name or host or _("External Resource")
    metadata = {
        "uploaded_via": "dashboard",
        "public_label": label,
    }
    upload = KnowledgeUpload.objects.create(
        business_profile=business,
        user=request.user,
        display_name=label[:255],
        source_type=KnowledgeSourceType.LINK,
        status=KnowledgeStatus.PROCESSING,
        source_name=host[:255],
        legacy_url=url_value,
        metadata=metadata,
    )
    KnowledgeUploadUrl.objects.create(
        upload=upload,
        url=url_value,
        normalized_host=host[:120],
    )
    queue_ingestion_job(upload, trigger="dashboard_link_upload")
    return upload


def _create_text_upload(
    *,
    request: HttpRequest,
    business: BusinessProfile,
    display_name: str,
    content: str,
) -> KnowledgeUpload:
    normalized_content = (content or "").strip()
    if not normalized_content:
        raise KnowledgeUploadError(_("Add content to store a manual snippet."), field="knowledge_text")
    label = display_name or (normalized_content.splitlines()[0][:80] if normalized_content else _("Manual entry"))
    metadata = {
        "uploaded_via": "dashboard",
        "public_label": label,
    }
    upload = KnowledgeUpload.objects.create(
        business_profile=business,
        user=request.user,
        display_name=label[:255],
        source_type=KnowledgeSourceType.TEXT,
        status=KnowledgeStatus.PROCESSING,
        source_name=label[:255],
        size_bytes=len(normalized_content.encode("utf-8")),
        summary=normalized_content[:500],
        metadata=metadata,
    )
    KnowledgeUploadText.objects.create(upload=upload, content=normalized_content)
    queue_ingestion_job(upload, trigger="dashboard_text_upload")
    return upload


@login_required
def dashboard_knowledge(request: HttpRequest) -> HttpResponse:
    user_name = _current_user_name(request)
    documents: list[dict[str, object]] = []
    total_documents = 0
    has_error = False
    business = _primary_business_for_user(request.user)

    if business:
        try:
            result = list_documents(business_profile=business, limit=50, offset=0)
            total_documents = result.total
            for item in result.items:
                source_label = _localized_document_source_label(item.source_type, item.source_label)
                status_label = _localized_document_status_label(item.status, item.status_label)
                documents.append(
                    {
                        "uuid": str(item.id),
                        "name": item.name or _("Document"),
                        "identifier": _document_identifier(item.id),
                        "classification": _localized_document_classification(item.category, item.language),
                        "type_badge": source_label,
                        "source_type": item.source_type,
                        "source_label": source_label,
                        "status_label": status_label,
                        "status_code": item.status,
                        "status_badge_class": _document_status_class(item.status),
                        "updated": _format_document_timestamp(item.updated_at),
                        "updated_iso": item.updated_at.isoformat() if item.updated_at else "",
                        "size_display": _format_document_size(item.size_bytes),
                        "size_bytes": item.size_bytes or 0,
                        "tags": list(item.tags),
                        "tags_display": ", ".join(item.tags) if item.tags else _("—"),
                        "language": item.language or "",
                        "category": item.category or "",
                        "token_count": item.token_count or 0,
                        "is_sensitive": bool(item.is_sensitive),
                        "last_synced_iso": item.last_synced_at.isoformat() if item.last_synced_at else "",
                        "last_ingested_iso": item.last_ingested_at.isoformat() if item.last_ingested_at else "",
                        "integration_name": item.integration_name or "",
                    }
                )
        except DocumentListValidationError:
            has_error = True

    stats = [
        {
            "label": _("Documents indexed"),
            "value": total_documents if business else None,
            "helper": (
                _("Up-to-date count of synced files.")
                if business
                else _("Upload your first files to populate the knowledge base.")
            ),
            "icon_svg": '<svg class="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3-.75c0 .414.336.75.75.75h.75A2.25 2.25 0 0 0 19.5 15V6A2.25 2.25 0 0 0 17.25 3H6.75A2.25 2.25 0 0 0 4.5 5.25V18A2.25 2.25 0 0 0 6.75 20.25H18"/></svg>',
        },
        {
            "label": _("Integrations"),
            "value": len({doc["integration_name"] for doc in documents if doc["integration_name"]}) if business else None,
            "helper": _("Connect Google Drive, Zendesk, or custom APIs."),
            "icon_svg": '<svg class="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12a2.25 2.25 0 0 1 2.25-2.25h10.5A2.25 2.25 0 0 1 19.5 12m-15 0a2.25 2.25 0 0 0 2.25 2.25h10.5A2.25 2.25 0 0 0 19.5 12m-15 0V7.5m15 4.5V16.5m0-9A2.25 2.25 0 0 0 17.25 5.25H6.75A2.25 2.25 0 0 0 4.5 7.5M19.5 16.5a2.25 2.25 0 0 1-2.25 2.25H6.75A2.25 2.25 0 0 1 4.5 16.5"/></svg>',
        },
        {
            "label": _("Coverage"),
            "value": None,
            "helper": _("Coverage metrics appear once agents use knowledge."),
            "icon_svg": '<svg class="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 3v3m0 12v3m9-9h-3M6 12H3m15.364-6.364-2.121 2.121M8.757 15.243l-2.121 2.121m0-12.727 2.121 2.121m6.486 6.486 2.121 2.121"/></svg>',
        },
        {
            "label": _("Sync health"),
            "value": None,
            "helper": _("Status updates will display after first sync."),
            "icon_svg": '<svg class="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12a7.5 7.5 0 0 1 13.35-4.35L21 8.25M19.5 3v5.25M19.5 12a7.5 7.5 0 0 1-13.35 4.35L3 15.75M4.5 21v-5.25"/></svg>',
        },
    ]

    documents_showing = len(documents)
    integrations_cards = _gather_dashboard_integrations(business)
    requested_tab = (request.GET.get("tab") or "").strip().lower()
    valid_tabs = {"documents", "integrations"}
    active_tab = requested_tab if requested_tab in valid_tabs else "documents"
    attention_statuses = {
        KnowledgeIntegrationStatus.ERROR,
        KnowledgeIntegrationStatus.DISCONNECTED,
    }
    integrations_attention = [
        integration for integration in integrations_cards if integration["status"] in attention_statuses
    ]
    integrations_active = [
        integration for integration in integrations_cards if integration["status"] not in attention_statuses
    ]
    integrations_summary = {
        "active": len(integrations_active),
        "attention": len(integrations_attention),
        "syncing": sum(
            1
            for integration in integrations_cards
            if integration["status"] == KnowledgeIntegrationStatus.SYNCING
        ),
        "total": len(integrations_cards),
    }
    context = {
        "user_name": user_name,
        "knowledge_stats": stats,
        "knowledge_active_tab": active_tab,
        "knowledge_filters": {
            "search_placeholder": _("Search documents, tags, sources…"),
            "collection_label": _("All sources"),
        },
        "knowledge_backend_notice": None if business else _("Link a business profile to start indexing knowledge."),
        "knowledge_auth_notice": None,
        "knowledge_loading": False,
        "knowledge_error_message": _("Unable to load documents right now.") if has_error else None,
        "skeleton_rows": range(5),
        "knowledge_documents": documents,
        "knowledge_documents_empty_message": _(
            "No knowledge documents yet. Upload files or connect an integration to populate content."
        ),
        "knowledge_documents_showing": documents_showing,
        "knowledge_documents_total": total_documents if business else 0,
        "knowledge_documents_has_prev": False,
        "knowledge_documents_has_next": bool(business and total_documents > documents_showing),
        "knowledge_integrations": integrations_cards,
        "knowledge_integrations_active": integrations_active,
        "knowledge_integrations_attention": integrations_attention,
        "knowledge_integrations_summary": integrations_summary,
        "knowledge_integrations_empty_message": _(
            "Connect a source to keep external docs and spreadsheets updated automatically."
        ),
        "knowledge_integrations_enabled": bool(business),
        "knowledge_business_id": str(business.id) if business else "",
        "knowledge_integrations_connect_url": reverse("frontend:dashboard-knowledge-integrations-connect"),
        "knowledge_panel_empty_title": _("Select a document"),
        "knowledge_panel_empty_message": _("Choose a document to preview summary, classification, and sync details here."),
        "knowledge_upload_types": [
            {"value": value, "label": label} for value, label in KNOWLEDGE_UPLOAD_SIMPLE_TYPES
        ],
        "knowledge_upload_enabled": bool(business),
        "knowledge_dump_enabled": bool(business),
    }
    return render(request, "frontend/knowledge.html", context)


@login_required
def dashboard_knowledge_visualizer(request: HttpRequest) -> HttpResponse:
    user_name = _current_user_name(request)
    documents: list[dict[str, object]] = []
    total_documents = 0
    has_error = False
    business = _primary_business_for_user(request.user)
    selected_id = (request.GET.get("document_id") or "").strip()

    if business:
        try:
            offset = 0
            limit = 100
            while True:
                result = list_documents(business_profile=business, limit=limit, offset=offset)
                total_documents = result.total
                for item in result.items:
                    source_label = _localized_document_source_label(item.source_type, item.source_label)
                    status_label = _localized_document_status_label(item.status, item.status_label)
                    documents.append(
                        {
                            "uuid": str(item.id),
                            "name": item.name or _("Document"),
                            "source_label": source_label,
                            "status_label": status_label,
                            "status_code": item.status,
                            "status_badge_class": _document_status_class(item.status),
                            "updated": _format_document_timestamp(item.updated_at),
                        }
                    )
                offset += limit
                if offset >= total_documents:
                    break
        except DocumentListValidationError:
            has_error = True

    context = {
        "user_name": user_name,
        "knowledge_business_id": str(business.id) if business else "",
        "visualizer_documents": documents,
        "visualizer_documents_total": total_documents if business else 0,
        "visualizer_documents_empty_message": (
            _("Upload a document to see ingestion artifacts here.")
            if business
            else _("Link a business profile to inspect documents.")
        ),
        "visualizer_empty_title": _("Select a document"),
        "visualizer_empty_message": _("Pick a document to inspect how it was ingested and chunked."),
        "visualizer_selected_id": selected_id,
        "visualizer_error_message": _("Unable to load documents right now.") if has_error else None,
    }
    return render(request, "frontend/knowledge_visualizer.html", context)


@login_required
@require_http_methods(["POST"])
def dashboard_knowledge_upload(request: HttpRequest) -> HttpResponse:
    wants_json = _wants_json(request)
    business = _primary_business_for_user(request.user)
    if not business:
        message = _("Link a business profile before uploading knowledge.")
        if wants_json:
            return JsonResponse({"success": False, "message": message}, status=HTTPStatus.BAD_REQUEST)
        messages.error(request, message)
        return redirect("frontend:dashboard-knowledge")

    source_type = (request.POST.get("source_type") or "").strip().lower()
    valid_types = {value for value, _label in KNOWLEDGE_UPLOAD_SIMPLE_TYPES}
    if source_type not in valid_types:
        message = _("Select a valid knowledge type.")
        if wants_json:
            return JsonResponse({"success": False, "message": message}, status=HTTPStatus.BAD_REQUEST)
        messages.error(request, message)
        return redirect("frontend:dashboard-knowledge")

    display_name = (request.POST.get("display_name") or "").strip()
    upload_records: list[KnowledgeUpload] = []
    try:
        if source_type == KnowledgeSourceType.FILE:
            upload_files = [
                uploaded
                for uploaded in request.FILES.getlist("knowledge_file")
                if getattr(uploaded, "name", "").strip()
            ]
            if not upload_files:
                fallback_file = request.FILES.get("knowledge_file")
                if fallback_file and getattr(fallback_file, "name", "").strip():
                    upload_files = [fallback_file]
            if not upload_files:
                raise KnowledgeUploadError(_("Select a file to upload."), field="knowledge_file")

            per_file_display_name = display_name if len(upload_files) == 1 else ""
            for upload_file in upload_files:
                upload_records.append(
                    _create_file_upload(
                        request=request,
                        business=business,
                        display_name=per_file_display_name,
                        upload_file=upload_file,
                    )
                )
        elif source_type == KnowledgeSourceType.LINK:
            url_value = (request.POST.get("knowledge_url") or "").strip()
            if not url_value:
                raise KnowledgeUploadError(_("Add a URL to capture this resource."), field="knowledge_url")
            upload_records = [
                _create_link_upload(
                    request=request,
                    business=business,
                    display_name=display_name,
                    url_value=url_value,
                )
            ]
        elif source_type == KnowledgeSourceType.TEXT:
            upload_records = [
                _create_text_upload(
                    request=request,
                    business=business,
                    display_name=display_name,
                    content=request.POST.get("knowledge_text") or "",
                )
            ]
        else:  # pragma: no cover - defensive fallback
            raise KnowledgeUploadError(_("Unsupported knowledge type selected."), field="source_type")
    except (KnowledgeUploadError, ValidationError) as exc:
        message = str(exc)
        if wants_json:
            payload = {"success": False, "message": message}
            if isinstance(exc, KnowledgeUploadError) and exc.field:
                payload["field"] = exc.field
            return JsonResponse(payload, status=HTTPStatus.BAD_REQUEST)
        messages.error(request, message)
        return redirect("frontend:dashboard-knowledge")
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Failed to store knowledge upload from dashboard.")
        message = _("Unable to save the document right now. Please try again.")
        if wants_json:
            return JsonResponse({"success": False, "message": message}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        messages.error(request, message)
        return redirect("frontend:dashboard-knowledge")
    else:
        if upload_records:
            latest_upload = upload_records[-1]
            if wants_json:
                documents_total = KnowledgeUpload.objects.filter(business_profile=business).count()
                serialized_uploads = [
                    _serialize_upload_for_dashboard(upload_record) for upload_record in upload_records
                ]
                if len(serialized_uploads) == 1:
                    message = _('"%(name)s" added to your knowledge base.') % {"name": latest_upload.display_name}
                else:
                    message = _("%(count)s files added to your knowledge base.") % {"count": len(serialized_uploads)}
                return JsonResponse(
                    {
                        "success": True,
                        "message": message,
                        "document": serialized_uploads[-1] if serialized_uploads else None,
                        "documents": serialized_uploads,
                        "documents_total": documents_total,
                    },
                    status=HTTPStatus.CREATED,
                )
            if len(upload_records) == 1:
                messages.success(
                    request,
                    _('"%(name)s" added to your knowledge base.') % {"name": latest_upload.display_name},
                )
            else:
                messages.success(
                    request,
                    _("%(count)s files added to your knowledge base.") % {"count": len(upload_records)},
                )
    return redirect("frontend:dashboard-knowledge")


@login_required
@require_http_methods(["GET"])
def dashboard_knowledge_dump(request: HttpRequest) -> HttpResponse:
    business = _primary_business_for_user(request.user)
    if not business:
        messages.error(request, _("Link a business profile before exporting knowledge."))
        return redirect("frontend:dashboard-knowledge")

    uploads = (
        KnowledgeUpload.objects.filter(business_profile=business)
        .order_by("-updated_at")
    )
    documents: list[dict[str, object]] = []
    for upload in uploads:
        documents.append(
            {
                "id": str(upload.id),
                "display_name": upload.display_name,
                "source_type": upload.source_type,
                "status": upload.status,
                "size_bytes": upload.size_bytes,
                "updated_at": upload.updated_at.isoformat() if upload.updated_at else None,
                "metadata": upload.metadata,
                "tags": upload.tags,
            }
        )

    payload = {
        "business_id": str(business.id),
        "business_name": business.name,
        "generated_at": timezone.now().isoformat(),
        "count": len(documents),
        "documents": documents,
    }
    response = HttpResponse(json.dumps(payload, indent=2, default=str), content_type="application/json")
    timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
    response["Content-Disposition"] = f'attachment; filename=\"knowledge_dump_{timestamp}.json\"'
    return response


@login_required
@require_http_methods(["POST"])
def dashboard_knowledge_integrations_connect(request: HttpRequest) -> HttpResponse:
    business = _primary_business_for_user(request.user)
    if not business:
        messages.error(request, _("Link a business profile before connecting integrations."))
        return redirect("frontend:dashboard-knowledge")

    payload = json.dumps({"businessId": str(business.id)})
    api_request = _portal_request_factory.post(
        reverse("api:integrations-google-start"),
        data=payload,
        content_type="application/json",
    )
    api_request.user = request.user
    api_request.COOKIES = request.COOKIES.copy()
    api_request.META.update(
        {
            "REMOTE_ADDR": request.META.get("REMOTE_ADDR", ""),
            "HTTP_USER_AGENT": request.META.get("HTTP_USER_AGENT", ""),
            "HTTP_REFERER": request.META.get("HTTP_REFERER", ""),
        }
    )
    api_request._dont_enforce_csrf_checks = True  # Reuse API view without duplicating logic

    response = start_google_drive_oauth_view(api_request)
    if response.status_code != 200:
        try:
            data = json.loads(response.content.decode("utf-8"))
            message = data.get("message") or data.get("error") or _("Unable to start Google authorization.")
        except (ValueError, UnicodeDecodeError):
            message = _("Unable to start Google authorization.")
        messages.error(request, message)
        return redirect("frontend:dashboard-knowledge")

    try:
        data = json.loads(response.content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        messages.error(request, _("Google returned an invalid authorization payload."))
        return redirect("frontend:dashboard-knowledge")

    authorization_url = data.get("authorizationUrl")
    if not authorization_url:
        messages.error(request, _("Missing authorization URL from Google."))
        return redirect("frontend:dashboard-knowledge")
    return redirect(authorization_url)


@login_required
def dashboard_cases(request: HttpRequest) -> HttpResponse:
    user_name = _current_user_name(request)
    cases: List[Dict[str, object]] = []
    metrics = {"open": None, "urgent": None, "urgent_delta": None, "avg_open": None}
    total = 0

    business = None
    if request.user.is_authenticated:
        business = request.user.business_profiles.order_by("-created_at").first()

    if business:
        try:
            result = list_cases(business_profile=business, limit=25)
            total = result.total_count
            metrics = {
                "open": result.metrics.open_total,
                "urgent": result.metrics.urgent_open,
                "urgent_delta": result.metrics.urgent_delta_hint,
                "avg_open": result.metrics.average_open_hours,
            }
            for item in result.items:
                cases.append(
                    {
                        "id": item.case_number,
                        "uuid": str(item.id),
                        "priority": item.priority,
                        "priority_class": _case_priority_class(item.priority),
                        "type": "inquiry",
                        "title": item.title,
                        "description": item.description,
                        "status": item.status,
                        "status_class": _case_status_class(item.status),
                        "customer": {
                            "name": item.customer_name,
                            "email": item.customer_email or _("—"),
                            "initials": item.customer_initials,
                        },
                        "channel": (item.channel or _("chat")).replace("_", " ").title(),
                        "started": item.started_at.strftime("%b %d, %Y %H:%M"),
                        "started_iso": item.started_at.isoformat(),
                    }
                )
        except Exception:
            pass

    context = {
        "user_name": user_name,
        "cases_total": total if total else len(cases),
        "cases_metrics": metrics,
        "skeleton_rows": range(6),
        "cases_loading": False,
        "cases": cases,
        "cases_empty_message": _("No cases yet. Connect Pocket AI to your support channels to see live traffic."),
        "cases_showing_count": len(cases),
        "cases_has_prev": False,
        "cases_has_next": bool(total and total > len(cases)),
    }
    return render(request, "frontend/cases.html", context)


@login_required
def dashboard_case_detail(request: HttpRequest, case_id: uuid.UUID) -> HttpResponse:
    user_name = _current_user_name(request)
    business = request.user.business_profiles.order_by("-created_at").first()
    if business is None:
        raise Http404(_("Case not found"))

    case = (
        Case.objects.select_related("business_profile", "agent_profile", "customer")
        .filter(id=case_id, business_profile=business)
        .first()
    )
    if case is None:
        raise Http404(_("Case not found"))

    conversation = (
        Conversation.objects.select_related("agent_profile", "customer")
        .prefetch_related("messages", "extractions")
        .filter(case=case)
        .first()
    )

    customer_name = None
    customer_email = None
    if case.customer:
        customer_name = case.customer.display_name or case.customer.primary_email or _("Customer")
        customer_email = case.customer.primary_email or _("—")
    elif conversation and conversation.customer:
        customer_name = conversation.customer.display_name or _("Customer")
        customer_email = conversation.customer.primary_email or _("—")
    else:
        customer_name = _("Customer")
        customer_email = _("—")

    conversation_context: dict[str, str] | None = None
    messages: list[dict[str, Any]] = []
    extractions: list[dict[str, Any]] = []
    if conversation:
        conversation_context = {
            "status": conversation.status,
            "status_label": (conversation.status or "").replace("_", " ").title(),
            "channel": (conversation.channel or _("chat")).replace("_", " ").title(),
            "session_token": conversation.session_token,
            "started_at_label": _format_datetime_label(conversation.started_at),
            "last_activity_label": _format_datetime_label(conversation.last_activity_at),
        }
        agent_label = (conversation.agent_profile.name if conversation.agent_profile else None) or (
            case.agent_profile.name if case.agent_profile else _("Pocket AI")
        )
        customer_label = customer_name
        ordered_messages = conversation.messages.all().order_by("sent_at", "created_at")
        for message in ordered_messages:
            sender = (message.sender or "system").lower()
            if sender == ConversationSender.CUSTOMER:
                author = customer_label
                initials = initials_from_name(customer_label, "CU")
            elif sender == ConversationSender.AI:
                author = agent_label
                initials = initials_from_name(agent_label, "AI")
            else:
                author = _("System")
                initials = _("SYS")
            metadata = message.metadata or {}
            messages.append(
                {
                    "id": str(message.id),
                    "variant": sender,
                    "author": author,
                    "initials": initials,
                    "body": message.body,
                    "sent_at_label": _format_datetime_label(message.sent_at),
                    "actions": _format_action_badges(metadata.get("actions")),
                    "citations": _format_citations(metadata.get("citations")),
                    "diagnostics": _format_diagnostics(metadata.get("diagnostics")),
                }
            )
        for extraction in conversation.extractions.all().order_by("-created_at"):
            payload = extraction.payload or {}
            summary = (
                payload.get("reason")
                or payload.get("title")
                or payload.get("display_name")
                or payload.get("status")
                or ""
            )
            extractions.append(
                {
                    "id": str(extraction.id),
                    "type": (extraction.extraction_type or "").replace("_", " ").title(),
                    "created_at_label": _format_datetime_label(extraction.created_at),
                    "summary": summary,
                    "payload": payload,
                }
            )

    suggested_actions = case.ai_suggested_actions if isinstance(case.ai_suggested_actions, list) else []
    case_context = {
        "id": str(case.id),
        "case_number": case.case_number,
        "title": case.title,
        "description": case.description,
        "status": case.status,
        "status_label": (case.status or "").replace("_", " ").title(),
        "status_class": _case_status_class(case.status),
        "priority": case.priority,
        "priority_label": (case.priority or "").replace("_", " ").title(),
        "priority_class": _case_priority_class(case.priority),
        "started_at_label": _format_datetime_label(case.started_at),
        "updated_at_label": _format_datetime_label(case.updated_at),
        "closed_at_label": _format_datetime_label(case.closed_at),
        "agent_name": case.agent_profile.name if case.agent_profile else _("—"),
        "customer": {
            "name": customer_name,
            "email": customer_email,
            "initials": initials_from_name(customer_name, "CU"),
        },
        "ai_diagnosis": case.ai_diagnosis or _("No diagnosis provided."),
        "ai_actions_taken": case.ai_actions_taken or "",
        "ai_suggested_actions": [str(item) for item in suggested_actions if item],
    }

    context = {
        "user_name": user_name,
        "case": case_context,
        "conversation": conversation_context,
        "messages": messages,
        "extractions": extractions,
        "messages_empty_message": _("No transcript available for this case yet."),
        "back_url": reverse("frontend:dashboard-cases"),
    }
    return render(request, "frontend/case_detail.html", context)


def chat_portal(request: HttpRequest, business_slug: str, agent_slug: str) -> HttpResponse:
    """Render the public chat portal view backed by the API bootstrap endpoint."""

    existing_token = request.GET.get("session") or request.COOKIES.get(f"chat_session_{business_slug}_{agent_slug}")
    ui_language = normalize_language_code(getattr(request, "LANGUAGE_CODE", "")) or "en"
    visitor_metadata = {
        "ip": request.META.get("REMOTE_ADDR"),
        "user_agent": request.META.get("HTTP_USER_AGENT"),
        "referer": request.META.get("HTTP_REFERER"),
        "ui_language": ui_language,
    }
    bootstrap_payload = _call_portal_bootstrap_api(
        request,
        business_slug=business_slug,
        agent_slug=agent_slug,
        existing_session_token=existing_token,
        metadata=visitor_metadata,
    )

    capabilities = bootstrap_payload.get("capabilities") if isinstance(bootstrap_payload, dict) else {}
    subagents_enabled = bool(capabilities.get("subAgentsEnabled")) if isinstance(capabilities, dict) else False

    business = bootstrap_payload.get("business", {})
    agent = bootstrap_payload.get("agent", {})
    session = bootstrap_payload.get("session", {})
    raw_messages = bootstrap_payload.get("messages", [])

    agent_name = agent.get("name") or "Pocket AI"
    agent_initials = initials_from_name(agent_name) or "AI"
    messages: list[dict[str, object]] = []
    for idx, message in enumerate(raw_messages, start=1):
        message_id = message.get("id") or f"msg_{idx}"
        sender = (message.get("sender") or "system").lower()
        content_blocks = message.get("content_blocks") or message.get("contentBlocks") or []
        if sender == "ai":
            author = agent_name
            initials = agent_initials
        elif sender == "customer":
            author = "You"
            initials = "YOU"
        else:
            author = "System"
            initials = "SYS"
        messages.append(
            {
                "id": str(message_id),
                "author": author,
                "initials": initials,
                "sender": sender,
                "body": message.get("body", ""),
                "content_blocks": content_blocks,
                "render_payload": {
                    "body": message.get("body", ""),
                    "content_blocks": content_blocks,
                },
                "sent_at": message.get("sent_at"),
                "metadata": message.get("metadata") or {},
            }
        )

    session_status = (session.get("status") or "new").lower()
    cookie_business_slug = business.get("slug") or slugify(business.get("name", "")) or business_slug
    cookie_agent_slug = agent.get("slug") or agent_slug
    storage_key = f"chat_session_{cookie_business_slug}_{cookie_agent_slug}"

    portal_context = {
        "business": {
            "name": business.get("name", ""),
            "slug": cookie_business_slug,
        },
        "agent": {
            "name": agent_name,
            "role": agent.get("role") or "AI Customer Specialist",
            "bio": "Trained on our knowledge base and policies to provide personalised support.",
            "initials": agent_initials,
            "slug": cookie_agent_slug,
        },
        "session_token": session.get("session_token", ""),
        "session_storage_key": storage_key,
        "ui_language": ui_language,
        "conversation_status": session_status.replace("_", " ").title(),
        "conversation_status_code": session_status,
        "messages": messages,
        "csat_scores": [(i, i) for i in range(1, 6)],
        "bootstrap_payload": bootstrap_payload,
        "bootstrap_script_id": PORTAL_BOOTSTRAP_SCRIPT_ID,
        "subagents_enabled": subagents_enabled,
        "asset_version": getattr(settings, "PORTAL_ASSET_VERSION", "dev"),
        "endpoints": {
            "bootstrap": reverse("api:chat-portal-session"),
            "messages": reverse("api:chat-messages"),
            "turns_create": reverse("api:chat-turns-create"),
            "turn_events_template": reverse(
                "api:chat-turns-events",
                args=["00000000-0000-0000-0000-000000000000"],
            ).replace("00000000-0000-0000-0000-000000000000", "{turn_id}"),
            "turn_cancel": reverse(
                "api:chat-turns-cancel",
                args=["00000000-0000-0000-0000-000000000000"],
            ).replace("00000000-0000-0000-0000-000000000000", "{turn_id}"),
            "events": reverse("api:chat-events"),
            "csat": reverse("api:chat-csat"),
            "tool_approval": reverse("api:chat-portal-tools-approve"),
            "tool_history": reverse("api:chat-portal-tools-history"),
            "run_approval": reverse("api:chat-portal-runs-approval"),
            "run_user_input": reverse("api:chat-portal-runs-user-input"),
            "agent_request_update": reverse("api:chat-portal-agent-requests-update"),
            "email_send_draft": reverse("api:chat-portal-email-send-draft"),
            "email_discard_draft": reverse("api:chat-portal-email-discard-draft"),
            "file_upload": reverse("api:chat-portal-files-upload"),
            "file_download_url_template": reverse(
                "api:chat-portal-files-download-url",
                args=["00000000-0000-0000-0000-000000000000"],
            ).replace("00000000-0000-0000-0000-000000000000", "{file_id}"),
        },
    }
    response = render(request, "frontend/chat/portal.html", {"portal": portal_context})
    cookie_key = storage_key
    response.set_cookie(
        cookie_key,
        portal_context["session_token"],
        max_age=3600 * 24 * 365,
        httponly=False,
        secure=False,
        samesite="Lax",
    )
    return response
