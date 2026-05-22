from __future__ import annotations

from typing import Any, Mapping

from django.utils.translation import gettext as _, get_language

from apps.accounts.models import (
    BusinessProfile,
    EmailAccountStatus,
    IntegrationAccountStatus,
    McpConnectionApprovalMode,
    McpToolOperationType,
)
from apps.integrations.models import EmailAccount, IntegrationAccount
from apps.mcp.connectors import _infer_operation_type_from_tool_name
from apps.mcp.models import McpConnection
from apps.api.mcp.serializers import _serialize_mcp_connection

_TOOL_APPROVAL_OVERRIDES_META_KEY = "tool_approval_overrides"


def _controls_mode_from_approval_mode(value: str | None, *, operation_type: str | None = None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == McpConnectionApprovalMode.AUTO:
        return "auto"
    if normalized == McpConnectionApprovalMode.APPROVE_WRITES:
        op = str(operation_type or "").strip().lower()
        return "auto" if op == McpToolOperationType.READ else "confirm"
    return "confirm"


def _normalize_controls_mode(value: object) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"auto", "confirm"}:
        return normalized
    return None


def _controls_default_mode_for_operation_type(operation_type: str | None) -> str:
    op = str(operation_type or "").strip().lower()
    return "auto" if op == McpToolOperationType.READ else "confirm"


def _load_business_tool_approval_overrides(business: BusinessProfile) -> dict[str, str]:
    metadata = business.metadata if isinstance(getattr(business, "metadata", None), Mapping) else {}
    raw = metadata.get(_TOOL_APPROVAL_OVERRIDES_META_KEY)
    if raw is None:
        raw = metadata.get("toolApprovalOverrides")
    if not isinstance(raw, Mapping):
        return {}
    overrides: dict[str, str] = {}
    for tool_name, mode_value in raw.items():
        normalized_tool_name = str(tool_name or "").strip()
        normalized_mode = _normalize_controls_mode(mode_value)
        if not normalized_tool_name or normalized_mode is None:
            continue
        overrides[normalized_tool_name] = normalized_mode
    return overrides


def _save_business_tool_approval_overrides(*, business: BusinessProfile, overrides: Mapping[str, str]) -> None:
    metadata = dict(business.metadata) if isinstance(getattr(business, "metadata", None), Mapping) else {}
    cleaned = {
        str(tool_name or "").strip(): str(mode or "").strip().lower()
        for tool_name, mode in dict(overrides).items()
        if str(tool_name or "").strip() and str(mode or "").strip().lower() in {"auto", "confirm"}
    }
    if cleaned:
        metadata[_TOOL_APPROVAL_OVERRIDES_META_KEY] = cleaned
    else:
        metadata.pop(_TOOL_APPROVAL_OVERRIDES_META_KEY, None)
    metadata.pop("toolApprovalOverrides", None)
    business.metadata = metadata
    business.save(update_fields=["metadata", "updated_at"])


def _controls_tool_label(tool_name: str) -> str:
    normalized = str(tool_name or "").strip()
    if not normalized:
        return "Tool"
    return normalized.replace("_", " ").strip().title()


def _controls_tool_description(tool_name: str, description: str, *, source_type: str = "internal") -> str:
    tool_key = str(tool_name or "").strip().lower()
    language_code = str(get_language() or "").strip().lower()
    if language_code.startswith("ar"):
        arabic_overrides = {
            "continue_agent_run": "استأنف تشغيلًا خلفيًا قائمًا (وكيلًا فرعيًا) برسالة متابعة. استخدمه لإرسال تعليمات إضافية بدل إنشاء تشغيل جديد.",
            "start_agent_run": "أنشئ تشغيلًا خلفيًا (وكيلًا فرعيًا) مرتبطًا بهذه المحادثة للمهام الطويلة أو متعددة الخطوات.",
            "list_agent_runs": "اعرض قائمة التشغيلات الخلفية الخاصة بالمحادثة مع حالتها الحالية.",
            "get_agent_run": "اجلب الحالة التفصيلية ونتيجة تشغيل خلفي معيّن.",
            "initiate_phone_call": "ابدأ مكالمة هاتفية صادرة واحدة عبر مزوّد المكالمات المهيّأ.",
            "search_knowledge": "ابحث في قاعدة المعرفة باستخدام استعلام بلغة طبيعية.",
            "read_knowledge": "اقرأ المقاطع المرجعية الناتجة من البحث في المعرفة مع حدود آمنة للحجم.",
            "search_conversation_files": "ابحث داخل ملفات المحادثة المرفوعة (مثل PDF) عن المقاطع ذات الصلة.",
            "read_conversation_file": "اقرأ مقاطع محددة من ملف مرفوع داخل المحادثة.",
            "retrieve_earlier_context": "استرجع سياقًا أقدم من تاريخ المحادثة عند الحاجة للرجوع للمحتوى السابق.",
            "request_user_input": "اطلب من المستخدم إدخالًا مباشرًا عند الحاجة لتوضيح أو قرار.",
            "portal_emit_blocks": "حدّث واجهة البوابة بكتل محتوى منظّمة بشكل تدريجي.",
            "pdf_generate": "أنشئ ملف PDF جديدًا من النص أو المحتوى المرسل.",
            "pdf_merge": "ادمج عدة ملفات PDF في ملف واحد.",
            "pdf_extract_pages": "استخرج صفحات محددة من ملف PDF.",
            "pdf_extract_text": "استخرج النص من ملف PDF (مع خيار تحديد صفحات).",
            "mcp_search_tools": "ابحث في أدوات MCP الخارجية المتاحة واعرض أفضل الخيارات المناسبة.",
            "mcp_call_tool": "نفّذ أداة MCP خارجية عبر `tool_id` مع المعاملات المطلوبة.",
            "calendar_list_events": "اعرض الأحداث القادمة من تقويم Google المتصل.",
            "calendar_get_event": "اجلب تفاصيل حدث محدد من تقويم Google.",
            "calendar_create_event": "أنشئ حدثًا جديدًا في تقويم Google المتصل.",
            "calendar_update_event": "حدّث حدثًا موجودًا في تقويم Google.",
            "drive_search_files": "ابحث عن الملفات في Google Drive المتصل.",
            "drive_get_file": "اقرأ محتوى/بيانات ملف محدد من Google Drive.",
            "drive_list_files": "اعرض ملفات مجلد Google Drive (أو الجذر عند عدم تحديد مجلد).",
            "onedrive_search_files": "ابحث عن الملفات في OneDrive المتصل.",
            "onedrive_get_file": "اقرأ محتوى/بيانات ملف محدد من OneDrive.",
            "onedrive_list_files": "اعرض ملفات مجلد OneDrive (أو الجذر عند عدم تحديد مجلد).",
            "slack_list_channels": "اعرض قنوات Slack المتاحة في مساحة العمل المتصلة.",
            "slack_read_channel": "اقرأ الرسائل الحديثة من قناة Slack محددة.",
            "slack_send_message": "أرسل رسالة إلى قناة Slack.",
            "slack_search_messages": "ابحث في رسائل Slack ضمن مساحة العمل المتصلة.",
            "hubspot_search_contacts": "ابحث عن جهات الاتصال في HubSpot المتصل.",
            "hubspot_get_contact": "اجلب تفاصيل جهة اتصال محددة من HubSpot.",
            "hubspot_create_contact": "أنشئ جهة اتصال جديدة في HubSpot.",
            "hubspot_search_deals": "ابحث عن الصفقات في HubSpot المتصل.",
            "email_search": "ابحث في صندوق البريد المتصل (Google/Microsoft).",
            "email_get_message": "اجلب رسالة بريد إلكتروني محددة بحسب المعرّف.",
            "email_get_thread": "اجلب سلسلة بريد إلكتروني كاملة بحسب المعرّف.",
            "email_create_draft": "أنشئ مسودة بريد إلكتروني في الحساب المتصل.",
            "email_send_draft": "أرسل مسودة بريد إلكتروني موجودة (حسب سياسة الموافقات).",
        }
        if tool_key in arabic_overrides:
            return arabic_overrides[tool_key]

    localized_overrides = {
        "continue_agent_run": _(
            "Continue an existing sub-agent run with a follow-up message. "
            "Use this to send additional instructions to a completed or waiting sub-agent instead of creating a new one. "
            "The sub-agent will resume with its full conversation history."
        ),
        "start_agent_run": _(
            "Create a sub-agent run anchored to this conversation. "
            "Use this when the current session needs a focused parallel investigation or multi-step branch. "
            "The sub-agent belongs inside the current session, not the Agentic Task panel."
        ),
        "get_agent_run": _(
            "Get detailed status and result of a specific sub-agent run. "
            "Use this after list_agent_runs to check on a particular task."
        ),
        "initiate_phone_call": _(
            "Initiate a single outbound phone call. "
            "Creates a queued CallSession that will be executed by the voice_call_worker."
        ),
    }
    if tool_key in localized_overrides:
        return localized_overrides[tool_key]

    normalized = str(description or "").strip()
    if normalized:
        return normalized

    if str(source_type or "").strip().lower() == "integration":
        return _("Tool exposed from a connected MCP integration.")
    return _("Tool exposed to the LLM runtime.")


def _controls_available_integration_tool_names(
    *,
    business: BusinessProfile,
    enabled_connections: list[McpConnection],
) -> set[str]:
    from apps.mcp import tools as mcp_tools

    available: set[str] = set()

    integration_accounts = IntegrationAccount.objects.filter(
        business_profile=business,
        status=IntegrationAccountStatus.CONNECTED,
    ).order_by("-updated_at")
    for account in integration_accounts:
        catalog = mcp_tools.get_native_integration_tools_for_type(str(account.integration_type or ""))
        tool_names = [str(row.get("toolName") or "").strip() for row in catalog if str(row.get("toolName") or "").strip()]
        if not tool_names:
            continue
        enabled_map = mcp_tools.get_native_tool_enabled_map_for_account(account, tool_names=tool_names)
        for tool_name in tool_names:
            if bool(enabled_map.get(tool_name, True)):
                available.add(tool_name)

    email_accounts = EmailAccount.objects.filter(
        business_profile=business,
        status=EmailAccountStatus.CONNECTED,
    ).order_by("-updated_at")
    for account in email_accounts:
        provider = str(account.provider or "").strip().lower()
        if not provider:
            continue
        catalog = mcp_tools.get_email_integration_tools_for_provider(provider)
        tool_names = [str(row.get("toolName") or "").strip() for row in catalog if str(row.get("toolName") or "").strip()]
        if not tool_names:
            continue
        enabled_map = mcp_tools.get_email_tool_enabled_map_for_account(account, tool_names=tool_names)
        for tool_name in tool_names:
            if bool(enabled_map.get(tool_name, True)):
                available.add(tool_name)

    if enabled_connections:
        available.update({"mcp_search_tools", "mcp_call_tool"})

    return available


def _controls_internal_tool_items(
    *,
    overrides: Mapping[str, str] | None = None,
    available_integration_tool_names: set[str] | None = None,
    allowed_tool_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    from apps.mcp import tools as mcp_tools

    overrides_map = dict(overrides or {})
    available_integration_tools = set(available_integration_tool_names or set())
    filter_integration_tools = available_integration_tool_names is not None
    allowed_tools = set(allowed_tool_names or set())
    filter_allowed_tools = allowed_tool_names is not None
    native_registry = mcp_tools.get_native_integration_tool_registry()
    email_registry = getattr(mcp_tools, "EMAIL_INTEGRATION_TOOL_REGISTRY", {})
    gateway_tools = {"mcp_search_tools", "mcp_call_tool"}

    definitions = list(mcp_tools.get_tool_definitions()) + list(getattr(mcp_tools, "GATEWAY_TOOL_DEFINITIONS", ()))
    seen: set[str] = set()
    items: list[dict[str, Any]] = []

    for definition in definitions:
        if not isinstance(definition, Mapping):
            continue
        function_block = definition.get("function")
        if not isinstance(function_block, Mapping):
            continue

        tool_name = str(function_block.get("name") or "").strip()
        if not tool_name or tool_name in seen:
            continue
        seen.add(tool_name)
        if filter_allowed_tools and tool_name not in allowed_tools:
            continue

        description = str(function_block.get("description") or "").strip()

        native_meta = native_registry.get(tool_name) if isinstance(native_registry, Mapping) else None
        email_meta = email_registry.get(tool_name) if isinstance(email_registry, Mapping) else None
        source_type = "internal"
        integration_type = "internal"
        source_label = "Internal tool"
        integration_backed = False

        if isinstance(native_meta, Mapping):
            integration_backed = True
            source_type = "integration"
            integration_type = str(native_meta.get("integration_type") or "").strip() or "integration"
            source_label = "Integration"
            operation_type = str(native_meta.get("operation_type") or "").strip() or McpToolOperationType.UNKNOWN
        elif isinstance(email_meta, Mapping):
            integration_backed = True
            source_type = "integration"
            integration_type = "email"
            source_label = "Integration"
            operation_type = str(email_meta.get("operation_type") or "").strip() or McpToolOperationType.UNKNOWN
        elif tool_name in gateway_tools:
            integration_backed = True
            source_type = "integration"
            integration_type = "mcp_gateway"
            source_label = "Integration"
            operation_type = McpToolOperationType.READ
        else:
            operation_type = _infer_operation_type_from_tool_name(tool_name)

        if integration_backed and filter_integration_tools and tool_name not in available_integration_tools:
            continue

        default_controls_mode = _controls_default_mode_for_operation_type(operation_type)
        controls_mode = overrides_map.get(tool_name) or default_controls_mode
        approval_hint = "Workspace override" if tool_name in overrides_map else "System default"
        effective_approval_mode = (
            McpConnectionApprovalMode.AUTO if controls_mode == "auto" else McpConnectionApprovalMode.APPROVE_ALL
        )

        items.append(
            {
                "id": f"internal:{tool_name}",
                "toolName": tool_name,
                "label": _controls_tool_label(tool_name),
                "description": _controls_tool_description(tool_name, description, source_type=source_type),
                "sourceType": source_type,
                "sourceLabel": source_label,
                "integrationType": integration_type,
                "connectionId": None,
                "connectionName": None,
                "scope": "system",
                "editable": True,
                "exposedToLlm": True,
                "operationType": operation_type,
                "effectiveApprovalMode": effective_approval_mode,
                "controlsMode": controls_mode,
                "approvalHint": approval_hint,
            }
        )

    items.sort(key=lambda item: (str(item.get("sourceType") or ""), str(item.get("label") or item.get("toolName") or "")))
    return items


def _controls_agentic_operational_tool_names(
    *,
    business: BusinessProfile,
    enabled_connections: list[McpConnection],
    available_integration_tool_names: set[str],
) -> set[str]:
    # Keep Controls aligned with the operational agentic surface, while excluding
    # UI/internal helpers and legacy retrieval tools.
    allowed: set[str] = {
        "search_knowledge",
        "read_knowledge",
        "search_conversation_files",
        "read_conversation_file",
        "pdf_generate",
        "pdf_merge",
        "pdf_extract_pages",
        "pdf_extract_text",
        "initiate_phone_call",
        "start_agent_run",
        "list_agent_runs",
        "get_agent_run",
        "continue_agent_run",
        "list_tasks",
        "draft_agentic_task",
        "update_agentic_task",
        "request_agentic_task_activation",
        "pause_agentic_task",
        "search_memory",
        "save_memory",
        "forget_memory",
    }
    if enabled_connections:
        allowed.update({"mcp_search_tools", "mcp_call_tool"})
    allowed.update(available_integration_tool_names)
    return allowed


def _controls_connection_tool_items(*, business: BusinessProfile, connections: list[McpConnection]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    for connection in connections:
        serialized = _serialize_mcp_connection(connection, business=business, include_tool_settings=True)
        for tool in serialized.get("toolSettings", []):
            if not isinstance(tool, Mapping):
                continue
            tool_name = str(tool.get("toolName") or "").strip()
            if not tool_name:
                continue

            operation_type = str(tool.get("operationType") or "").strip() or _infer_operation_type_from_tool_name(tool_name)
            effective_mode = str(tool.get("effectiveApprovalMode") or connection.default_approval_mode).strip() or connection.default_approval_mode
            controls_mode = _controls_mode_from_approval_mode(effective_mode, operation_type=operation_type)
            description = str(tool.get("description") or "").strip()
            source_type = "integration"
            source_label = "Integration"
            marketplace_key = str(connection.marketplace_key or "").strip()

            items.append(
                {
                    "id": f"mcp:{connection.id}:{tool_name}",
                    "toolName": tool_name,
                    "label": _controls_tool_label(tool_name),
                    "description": _controls_tool_description(tool_name, description, source_type=source_type),
                    "sourceType": source_type,
                    "sourceLabel": source_label,
                    "integrationType": marketplace_key or "mcp_connection",
                    "connectionId": str(connection.id),
                    "connectionName": connection.name,
                    "scope": "connection",
                    "editable": True,
                    "exposedToLlm": True,
                    "operationType": operation_type,
                    "effectiveApprovalMode": effective_mode,
                    "controlsMode": controls_mode,
                    "approvalHint": "Connection override",
                }
            )

    items.sort(key=lambda item: (str(item.get("connectionName") or ""), str(item.get("label") or item.get("toolName") or "")))
    return items
