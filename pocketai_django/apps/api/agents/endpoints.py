from __future__ import annotations

import json
import uuid
from http import HTTPStatus

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from core.tenancy import tenant_context

from apps.accounts.agent_capabilities import (
    AgentCapabilityGraph,
    AgentCapabilityGroup,
    AgentCapabilityItem,
    resolve_agent_capabilities,
)
from apps.accounts.agents import (
    AgentListValidationError,
    agent_identifier,
    display_tone_label,
    get_agent_detail,
    list_agents,
)
from apps.accounts.models import AgentProfile
from apps.api.shared import _iso, _resolve_business_profile
from apps.knowledge.models import KnowledgeUpload


def _serialize_agent_capability_item(item: AgentCapabilityItem) -> dict[str, object]:
    return {
        "category": item.category,
        "key": item.key,
        "label": item.label,
        "availability": item.availability,
        "executionPolicy": item.execution_policy,
        "source": item.source,
        "reason": item.reason,
        "metadata": dict(item.metadata or {}),
    }


def _serialize_agent_capability_group(group: AgentCapabilityGroup) -> dict[str, object]:
    return {
        "category": group.category,
        "label": group.label,
        "items": [_serialize_agent_capability_item(item) for item in group.items],
    }


def _serialize_agent_capability_graph(graph: AgentCapabilityGraph) -> dict[str, object]:
    summary = graph.summary
    return {
        "summary": {
            "knowledgeScope": summary.knowledge_scope,
            "knowledgeDocumentCount": summary.knowledge_document_count,
            "nativeActionTotal": summary.native_action_total,
            "nativeActionEnabled": summary.native_action_enabled,
            "nativeIntegrationTotal": summary.native_integration_total,
            "nativeIntegrationEnabled": summary.native_integration_enabled,
            "nativeToolTotal": summary.native_tool_total,
            "nativeToolEnabled": summary.native_tool_enabled,
            "mcpConnectionTotal": summary.mcp_connection_total,
            "mcpConnectionEnabled": summary.mcp_connection_enabled,
            "mcpConnectionOptedOut": summary.mcp_connection_opted_out,
            "mcpToolTotal": summary.mcp_tool_total,
            "mcpToolAuto": summary.mcp_tool_auto,
            "mcpToolApprovalRequired": summary.mcp_tool_approval_required,
            "restrictionsTotal": summary.restrictions_total,
            "defaultApprovalMode": summary.default_approval_mode,
        },
        "groups": [_serialize_agent_capability_group(group) for group in graph.groups],
    }


def _parse_json_object(request: HttpRequest) -> tuple[dict[str, object] | None, JsonResponse | None]:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, JsonResponse({"error": "INVALID_JSON", "message": "Request body must be valid JSON."}, status=HTTPStatus.BAD_REQUEST)
    if not isinstance(payload, dict):
        return None, JsonResponse({"error": "INVALID_JSON", "message": "Request body must be a JSON object."}, status=HTTPStatus.BAD_REQUEST)
    return payload, None


def _serialize_agent_summary(agent: AgentProfile) -> dict[str, object]:
    return {
        "id": str(agent.id),
        "identifier": agent_identifier(agent.id),
        "name": agent.name,
        "status": getattr(agent, "status", "active"),
        "permissionConfig": agent.permission_config if isinstance(agent.permission_config, dict) else {},
        "tone": agent.tone or None,
        "toneLabel": display_tone_label(agent.tone),
        "publicSlug": agent.slug or "",
        "createdAt": _iso(agent.created_at),
        "updatedAt": _iso(agent.updated_at),
    }


@require_http_methods(["GET", "POST"])
def agents_collection(request: HttpRequest) -> JsonResponse:
    business_id = request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    if request.method == "POST":
        payload, error = _parse_json_object(request)
        if error:
            return error
        name = str((payload or {}).get("name") or "").strip()
        if not name:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "name is required."}, status=HTTPStatus.BAD_REQUEST)
        status = str((payload or {}).get("status") or AgentProfile.StatusChoices.ACTIVE).strip().lower()
        if status not in {choice for choice, _ in AgentProfile.StatusChoices.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid status."}, status=HTTPStatus.BAD_REQUEST)
        if status == AgentProfile.StatusChoices.ACTIVE and AgentProfile.objects.filter(
            business_profile=business,
            status=AgentProfile.StatusChoices.ACTIVE,
        ).exists():
            return JsonResponse(
                {
                    "error": "VALIDATION_ERROR",
                    "message": "This workspace already has a Business Assistant. Create Custom Assistants from the Custom Assistants page for specialization.",
                },
                status=HTTPStatus.BAD_REQUEST,
            )
        agent = AgentProfile.objects.create(
            business_profile=business,
            user=request.user,
            name=name[:120],
            status=status,
            permission_config=dict((payload or {}).get("permissionConfig") or (payload or {}).get("permission_config") or {}),
            tone=str((payload or {}).get("tone") or "")[:60],
        )
        return JsonResponse({"agent": _serialize_agent_summary(agent)}, status=HTTPStatus.CREATED)

    q_name = request.GET.get("q_name") or request.GET.get("qName")
    limit_param = request.GET.get("limit")
    offset_param = request.GET.get("offset")
    sort_by = request.GET.get("sort_by") or request.GET.get("sortBy") or "created_at"
    order = request.GET.get("order") or "desc"

    limit = limit_param if limit_param not in (None, "") else 50
    offset = offset_param if offset_param not in (None, "") else 0

    try:
        result = list_agents(
            business_profile=business,
            q_name=q_name,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            order=order,
        )
    except AgentListValidationError as exc:
        payload = {
            "error": "VALIDATION_ERROR",
            "message": str(exc),
        }
        if exc.field:
            payload["field"] = exc.field
        return JsonResponse(payload, status=HTTPStatus.BAD_REQUEST)

    response = {
        "items": [
            {
                "id": str(item.id),
                "identifier": agent_identifier(item.id),
                "name": item.name,
                "status": item.status,
                "tone": item.tone,
                "toneLabel": display_tone_label(item.tone),
                "publicSlug": item.public_slug,
                "conversations": item.conversations,
                "activeConversations": item.active_conversations,
                "completedConversations": item.completed_conversations,
                "escalations": item.escalations,
                "averageHandleSeconds": item.average_handle_seconds,
                "lastActiveAt": _iso(item.last_active_at),
                "updatedAt": _iso(item.updated_at),
                "createdAt": _iso(item.created_at),
            }
            for item in result.items
        ],
        "total": result.total,
        "limit": result.limit,
        "offset": result.offset,
    }
    return JsonResponse(response, status=HTTPStatus.OK)


@require_http_methods(["GET"])
def agents_directory(request: HttpRequest) -> JsonResponse:
    business_id = request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None
    agents = AgentProfile.objects.filter(business_profile=business).order_by("name")
    return JsonResponse(
        {
            "agents": [
                {
                    "id": str(agent.id),
                    "name": agent.name,
                    "status": agent.status,
                    "tone": agent.tone or "",
                }
                for agent in agents
            ]
        },
        status=HTTPStatus.OK,
    )


@require_http_methods(["GET", "PATCH", "PUT", "DELETE"])
def agent_detail_view(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    business_id = request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    agent_obj = AgentProfile.objects.filter(id=agent_id, business_profile=business).first()
    if agent_obj is None:
        return JsonResponse(
            {"error": "AGENT_NOT_FOUND", "message": "Agent profile not found."},
            status=HTTPStatus.NOT_FOUND,
        )
    if request.method == "DELETE":
        agent_obj.status = AgentProfile.StatusChoices.ARCHIVED
        agent_obj.save(update_fields=["status", "updated_at"])
        return JsonResponse({}, status=HTTPStatus.NO_CONTENT)
    if request.method in {"PATCH", "PUT"}:
        payload, error = _parse_json_object(request)
        if error:
            return error
        updates: list[str] = []
        next_status = agent_obj.status
        for public, field, limit in (
            ("name", "name", 120),
            ("status", "status", 24),
            ("tone", "tone", 60),
        ):
            if public in payload or field in payload:
                raw = payload.get(public) if public in payload else payload.get(field)
                value = str(raw or "").strip()[:limit]
                if field == "status" and value not in {choice for choice, _ in AgentProfile.StatusChoices.choices}:
                    return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid status."}, status=HTTPStatus.BAD_REQUEST)
                if field == "status":
                    next_status = value
                setattr(agent_obj, field, value)
                updates.append(field)
        if next_status == AgentProfile.StatusChoices.ACTIVE and AgentProfile.objects.filter(
            business_profile=business,
            status=AgentProfile.StatusChoices.ACTIVE,
        ).exclude(id=agent_obj.id).exists():
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "This workspace already has an active Business Assistant."},
                status=HTTPStatus.BAD_REQUEST,
            )
        if "permissionConfig" in payload or "permission_config" in payload:
            agent_obj.permission_config = dict(payload.get("permissionConfig") or payload.get("permission_config") or {})
            updates.append("permission_config")
        if updates:
            agent_obj.save(update_fields=sorted(set([*updates, "updated_at"])))
        return JsonResponse({"agent": _serialize_agent_summary(agent_obj)}, status=HTTPStatus.OK)

    try:
        detail = get_agent_detail(business_profile=business, agent_id=agent_id)
    except AgentProfile.DoesNotExist:
        return JsonResponse({"error": "AGENT_NOT_FOUND", "message": "Agent profile not found."}, status=HTTPStatus.NOT_FOUND)
    capability_graph = resolve_agent_capabilities(
        AgentProfile.objects.select_related("business_profile")
        .prefetch_related("action_permissions", "allowed_documents")
        .get(id=agent_id, business_profile=business)
    )
    capability_payload = _serialize_agent_capability_graph(capability_graph)

    response = {
        "agent": {
            "id": str(detail.summary.id),
            "identifier": agent_identifier(detail.summary.id),
            "name": detail.summary.name,
            "status": agent_obj.status,
            "permissionConfig": agent_obj.permission_config if isinstance(agent_obj.permission_config, dict) else {},
            "tone": detail.summary.tone,
            "toneLabel": display_tone_label(detail.summary.tone),
            "publicSlug": detail.summary.public_slug,
            "shareablePath": detail.shareable_path,
            "knowledge": {
                "mode": detail.knowledge_mode,
                "documents": [
                    {
                        "id": str(doc.id),
                        "name": doc.name,
                        "status": doc.status,
                        "sourceType": doc.source_type,
                        "lastSyncedAt": _iso(doc.last_synced_at),
                    }
                    for doc in detail.knowledge_documents
                ],
            },
            "stats": {
                "conversationsTotal": detail.stats.total_conversations,
                "conversationsActive": detail.stats.active_conversations,
                "conversationsCompleted": detail.stats.completed_conversations,
                "escalations": detail.stats.escalations,
                "averageHandleSeconds": detail.stats.average_handle_seconds,
                "lastActiveAt": _iso(detail.stats.last_active_at),
            },
            "capabilitySummary": capability_payload["summary"],
            "capabilityGraph": capability_payload["groups"],
            "createdAt": _iso(detail.summary.created_at),
            "updatedAt": _iso(detail.summary.updated_at),
        }
    }
    return JsonResponse(response, status=HTTPStatus.OK)


@require_http_methods(["GET"])
def agent_capabilities_view(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)

    agent = (
        AgentProfile.objects.select_related("business_profile")
        .prefetch_related("action_permissions", "allowed_documents")
        .filter(id=agent_id, user=request.user)
        .first()
    )
    if agent is None:
        return JsonResponse({"error": "AGENT_NOT_FOUND", "message": "Agent profile not found."}, status=HTTPStatus.NOT_FOUND)

    capability_graph = resolve_agent_capabilities(agent)
    payload = _serialize_agent_capability_graph(capability_graph)
    return JsonResponse(
        {
            "summary": payload["summary"],
            "groups": payload["groups"],
        },
        status=HTTPStatus.OK,
    )


@require_http_methods(["GET", "PUT"])
def agent_knowledge_access_view(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)

    agent = (
        AgentProfile.objects.select_related("business_profile")
        .prefetch_related("allowed_documents")
        .filter(id=agent_id, user=request.user)
        .first()
    )
    if agent is None:
        return JsonResponse({"error": "AGENT_NOT_FOUND", "message": "Agent profile not found."}, status=HTTPStatus.NOT_FOUND)

    business = agent.business_profile

    if request.method == "GET":
        with tenant_context(business.id):
            document_ids = list(agent.allowed_documents.filter(business_profile=business).values_list("id", flat=True))
        return JsonResponse(
            {
                "knowledgeAccess": {
                    "mode": "select" if document_ids else "all",
                    "documentIds": [str(value) for value in document_ids],
                }
            },
            status=HTTPStatus.OK,
        )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "INVALID_JSON", "message": "Body must be valid JSON."}, status=HTTPStatus.BAD_REQUEST)

    mode = str(payload.get("mode") or payload.get("knowledgeMode") or "").strip().lower()
    if mode not in {"all", "select"}:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "mode must be either 'all' or 'select'."},
            status=HTTPStatus.BAD_REQUEST,
        )

    document_ids_raw = payload.get("documentIds")

    def _parse_id_list(values: object, *, field: str) -> list[uuid.UUID]:
        if values is None:
            return []
        if not isinstance(values, list):
            raise ValueError(f"{field} must be a list.")
        parsed: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        for entry in values:
            try:
                entry_id = entry if isinstance(entry, uuid.UUID) else uuid.UUID(str(entry))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field} must contain valid UUIDs.") from exc
            if entry_id in seen:
                continue
            parsed.append(entry_id)
            seen.add(entry_id)
        return parsed

    try:
        document_ids = _parse_id_list(document_ids_raw, field="documentIds")
    except ValueError as exc:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": str(exc), "field": "documentIds"},
            status=HTTPStatus.BAD_REQUEST,
        )

    with tenant_context(business.id):
        if mode == "all":
            agent.allowed_documents.clear()
            updated_documents: list[str] = []
        else:
            if document_ids_raw is None:
                updated_documents = [str(value) for value in agent.allowed_documents.values_list("id", flat=True)]
            else:
                documents = list(KnowledgeUpload.objects.filter(business_profile=business, id__in=document_ids).only("id"))
                found_documents = {doc.id for doc in documents}
                missing_documents = [str(value) for value in document_ids if value not in found_documents]
                if missing_documents:
                    return JsonResponse(
                        {"error": "VALIDATION_ERROR", "message": f"Unknown document ids: {', '.join(missing_documents)}", "field": "documentIds"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                agent.allowed_documents.set(documents)
                updated_documents = [str(value) for value in document_ids]

    return JsonResponse(
        {
            "knowledgeAccess": {
                "mode": "select" if updated_documents else "all",
                "documentIds": updated_documents,
            }
        },
        status=HTTPStatus.OK,
    )
