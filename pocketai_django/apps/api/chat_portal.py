from __future__ import annotations

import copy
import json
import logging
import threading
import time
import uuid
from queue import Empty, Queue
from typing import Any, Iterable

from django.conf import settings
from django.db import close_old_connections
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.models import BusinessProfile
from apps.conversations.models import ConversationSender
from apps.services.ai_orchestrator import (
    ActionDispatcher,
    AiOrchestratorPlan,
    AiOrchestratorService,
    StreamingTurnContext,
)
from apps.services.mcp.sanitizer import sanitize_text, sanitize_with_diagnostics
from apps.services.llm_provider import load_default_provider
from apps.services.chat_portal import (
    ChatPortalService,
    PortalAgentSummary,
    PortalBusinessSummary,
    PortalMessage,
    PortalNotFoundError,
    PortalSessionBootstrap,
    PortalSessionState,
    PortalValidationError,
)

logger = logging.getLogger(__name__)

CONTEXT_STATUS_CODES = {
    "searching_knowledge",
    "reading_document",
    "planning_actions",
    "responding",
}


def _queue_put(queue, item):
    put = getattr(queue, "put", None)
    if callable(put):
        put(item)
    else:
        queue.append(item)


def _enqueue_status_events(queue, *, code: str, label: str | None = None, meta: dict | None = None) -> None:
    code_value = (code or "").strip()
    if not code_value:
        return
    label_value = label or code_value.replace("_", " ").title()
    payload: dict[str, object] = {"type": "status", "state": code_value, "label": label_value}
    if meta:
        payload["meta"] = meta
    if code_value in CONTEXT_STATUS_CODES:
        ctx_payload = {"type": "context_progress", "state": code_value, "label": label_value}
        if meta:
            ctx_payload["meta"] = meta
        _queue_put(queue, ctx_payload)
    _queue_put(queue, payload)


def _service() -> ChatPortalService:
    return ChatPortalService()


def _json_error(code: str, message: str, *, status: int = 400, extra: dict | None = None) -> JsonResponse:
    payload: dict[str, object] = {"error": {"code": code, "message": message}}
    if extra:
        payload["error"].update(extra)
    return JsonResponse(payload, status=status)


def _parse_json_body(request: HttpRequest) -> dict:
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PortalValidationError("Invalid JSON payload") from exc


def _business_prefers_mcp(business: BusinessProfile | None) -> bool:
    """
    Evaluate whether a business should use the MCP orchestrator.

    Business metadata can override the global setting via the key
    `mcp_orchestrator_enabled`. When unset, the global
    RAG_USE_MCP_ORCHESTRATOR flag is used.
    """

    global_default = getattr(settings, "RAG_USE_MCP_ORCHESTRATOR", False)
    if business is None:
        return global_default
    metadata = business.metadata if isinstance(business.metadata, dict) else {}
    override = metadata.get("mcp_orchestrator_enabled")
    if override is None:
        return global_default
    return bool(override)


def _business_to_dict(summary: PortalBusinessSummary) -> dict:
    return {"id": str(summary.id), "name": summary.name, "slug": summary.slug}


def _agent_to_dict(summary: PortalAgentSummary) -> dict:
    return {
        "id": str(summary.id),
        "name": summary.name,
        "role": summary.role,
        "slug": summary.slug,
        "shareable_path": summary.shareable_path,
    }


def _session_to_dict(session: PortalSessionState) -> dict:
    return {
        "conversation_id": str(session.conversation_id),
        "session_token": session.session_token,
        "status": session.status,
        "started_at": session.started_at.isoformat(),
        "expires_at": session.expires_at.isoformat() if session.expires_at else None,
    }


def _message_to_dict(message: PortalMessage) -> dict:
    return {
        "id": str(message.id),
        "sender": message.sender,
        "body": message.body,
        "sent_at": message.sent_at.isoformat(),
        "metadata": message.metadata,
    }


def _bootstrap_to_dict(result: PortalSessionBootstrap) -> dict:
    return {
        "business": _business_to_dict(result.business),
        "agent": _agent_to_dict(result.agent),
        "session": _session_to_dict(result.session),
        "messages": [_message_to_dict(msg) for msg in result.messages],
    }


@require_GET
def resolve_portal_handle(request: HttpRequest, business_slug: str, agent_slug: str) -> JsonResponse:
    service = _service()
    try:
        business, agent = service.resolve_handle(business_slug, agent_slug)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)
    return JsonResponse(
        {
            "business": {"id": str(business.id), "name": business.name, "slug": business.slug},
            "agent": {
                "id": str(agent.id),
                "name": agent.name,
                "role": agent.role or "AI Assistant",
                "slug": agent.slug,
                "shareable_path": agent.shareable_path,
            },
        }
    )


@csrf_exempt
@require_POST
def bootstrap_session(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    business_slug = (payload.get("business_slug") or payload.get("businessSlug") or "").strip()
    agent_slug = (payload.get("agent_slug") or payload.get("agentSlug") or "").strip()
    existing_session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip() or None
    metadata = payload.get("metadata") or {}

    if not business_slug or not agent_slug:
        return _json_error("validation_error", "business_slug and agent_slug are required.")

    try:
        result = service.bootstrap_session(
            business_slug=business_slug,
            agent_slug=agent_slug,
            existing_session_token=existing_session_token,
            metadata=metadata,
        )
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    return JsonResponse(_bootstrap_to_dict(result), status=200)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def messages_endpoint(request: HttpRequest) -> JsonResponse:
    service = _service()
    if request.method == "GET":
        session_token = request.GET.get("session_token") or request.GET.get("sessionToken")
        if not session_token:
            return _json_error("validation_error", "session_token is required")
        limit_param = request.GET.get("limit")
        limit = None
        if limit_param:
            try:
                limit = max(1, min(200, int(limit_param)))
            except ValueError:
                return _json_error("validation_error", "limit must be an integer between 1 and 200")
        try:
            messages = service.list_messages(session_token=session_token, limit=limit)
            session = service.get_session_state(session_token=session_token)
        except PortalNotFoundError as exc:
            return _json_error("not_found", str(exc), status=404)
        return JsonResponse(
            {"session": _session_to_dict(session), "messages": [_message_to_dict(msg) for msg in messages]}
        )

    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    body = (payload.get("body") or "").strip()
    metadata = payload.get("metadata") or {}

    try:
        message = service.append_message(
            session_token=session_token,
            sender=ConversationSender.CUSTOMER,
            body=body,
            metadata=metadata,
        )
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    return JsonResponse({"message": _message_to_dict(message)}, status=201)


@csrf_exempt
@require_POST
def submit_csat(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    try:
        score = int(payload.get("score"))
    except (TypeError, ValueError):
        return _json_error("validation_error", "score must be an integer between 1 and 5")
    comment = (payload.get("comment") or "").strip() or None

    try:
        session = service.record_csat(session_token=session_token, score=score, comment=comment)
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    return JsonResponse({"session": _session_to_dict(session)}, status=200)


@csrf_exempt
@require_POST
def submit_feedback(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    feedback_type = (payload.get("feedback_type") or payload.get("feedbackType") or "").strip()
    if not session_token or not feedback_type:
        return _json_error("validation_error", "session_token and feedback_type are required.")
    message_id_value = payload.get("message_id") or payload.get("messageId")
    message_id: uuid.UUID | None = None
    if message_id_value:
        try:
            message_id = uuid.UUID(str(message_id_value))
        except (TypeError, ValueError):
            return _json_error("validation_error", "message_id must be a valid UUID.")
    feedback_payload = {
        "query_text": payload.get("query_text"),
        "expected_behavior": payload.get("expected_behavior"),
        "expected_entities": payload.get("expected_entities") or [],
        "expected_aliases": payload.get("expected_aliases") or [],
        "notes": payload.get("notes"),
        "auto_promote": payload.get("auto_promote", True),
    }
    try:
        feedback = service.record_feedback(
            session_token=session_token,
            feedback_type=feedback_type,
            message_id=message_id,
            payload=feedback_payload,
        )
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    return JsonResponse(
        {
            "feedback": {
                "id": str(feedback.id),
                "feedback_type": feedback.feedback_type,
                "created_at": feedback.created_at.isoformat(),
            }
        },
        status=201,
    )


@csrf_exempt
@require_POST
def stream_send(request: HttpRequest) -> StreamingHttpResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError:
        return StreamingHttpResponse(status=400)

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    body = (payload.get("body") or "").strip()
    metadata = payload.get("metadata") or {}

    try:
        service.append_message(
            session_token=session_token,
            sender=ConversationSender.CUSTOMER,
            body=body,
            metadata=metadata,
        )
    except PortalValidationError:
        return StreamingHttpResponse(status=400)
    except PortalNotFoundError:
        return StreamingHttpResponse(status=404)

    try:
        conversation = service.get_conversation(session_token=session_token)
    except PortalNotFoundError:
        return StreamingHttpResponse(status=404)

    agent = conversation.agent_profile
    if not agent:
        return StreamingHttpResponse(status=500)

    use_mcp = _business_prefers_mcp(conversation.business_profile)
    logger.info(
        "chat_portal.stream_send orchestrator=%s conversation=%s business=%s",
        "mcp" if use_mcp else "legacy",
        conversation.id,
        getattr(conversation.business_profile, "id", None),
    )
    if use_mcp:
        from apps.services.llm_provider import load_mcp_provider
        from apps.services.mcp import McpOrchestratorService

        provider = load_mcp_provider()
        orchestrator = McpOrchestratorService(agent=agent, provider=provider)
    else:
        provider = load_default_provider()
        orchestrator = AiOrchestratorService(agent=agent, provider=provider)
    dispatcher = ActionDispatcher(agent=agent)

    def serialize_action_results(results):
        payloads = []
        for result in results:
            payloads.append(
                {
                    "action": result.action.value,
                    "status": result.status,
                    "metadata": result.metadata,
                    "error": result.error,
                }
            )
        return payloads

    def serialize_planned_actions(planned):
        payloads = []
        for action in planned:
            payloads.append(
                {
                    "action": action.action.value,
                    "status": "queued",
                    "metadata": action.payload,
                    "error": None,
                }
            )
        return payloads

    def _response_chunks(text: str, chunk_size: int = 64) -> Iterable[str]:
        clean = (text or "").strip()
        if not clean:
            return
        words = clean.split()
        if not words:
            return
        current: list[str] = []
        current_len = 0
        for word in words:
            if not current:
                current.append(word)
                current_len = len(word)
                continue
            projected = current_len + 1 + len(word)
            if projected <= chunk_size:
                current.append(word)
                current_len = projected
            else:
                yield " ".join(current)
                current = [word]
                current_len = len(word)
        if current:
            yield " ".join(current)

    stream_queue: Queue = Queue()
    stream_sentinel = object()
    finalize_queue: Queue = Queue()
    finalize_sentinel = object()
    actions_queue: Queue = Queue()
    actions_sentinel = object()
    stream_complete = threading.Event()
    plan_holder: dict[str, Any] = {}
    streamed_text_chunks: list[str] = []

    def on_response_text_delta(chunk: str) -> None:
        if chunk:
            stream_queue.put(chunk)

    def on_status_change(state) -> None:
        if not state:
            return
        code: str | None = None
        label: str | None = None
        meta: dict | None = None
        if isinstance(state, str):
            code = state.strip()
        elif isinstance(state, dict):
            raw_code = state.get("code") or state.get("state")
            if isinstance(raw_code, str):
                code = raw_code.strip()
            raw_label = state.get("label")
            if isinstance(raw_label, str):
                label = raw_label.strip()
            raw_meta = state.get("meta")
            if isinstance(raw_meta, dict):
                meta = raw_meta
        if not code:
            return
        _enqueue_status_events(stream_queue, code=code, label=label, meta=meta)

    def signal_stream_complete() -> None:
        if stream_complete.is_set():
            return
        stream_complete.set()
        stream_queue.put({"type": "status", "state": "complete", "label": ""})
        logger.debug("Stream completion signaled for conversation %s", conversation.id)
        stream_queue.put(stream_sentinel)

    def on_placeholder_response(text: str) -> None:
        # Placeholder responses are suppressed; status events handle UX.
        return

    def finalize_stream_context(stream_context: StreamingTurnContext) -> None:
        close_old_connections()
        try:
            # Planner now runs asynchronously using the streamed answer/context.
            plan = orchestrator.run_planner_only(
                conversation=conversation,
                user_message=body,
                answer_text=stream_context.response_text,
                tool_context=getattr(stream_context, "tool_context", None),
            )
            if plan is None:
                # Fallback to the streamed response without actions/extractions.
                plan = orchestrator.finalize_turn(stream_context)
            plan_holder["plan"] = plan
            persist_text = plan.response_text or ""
            if not persist_text:
                streamed_text = "".join(stream_context.streamed_chunks).strip() if stream_context.streamed_chunks else ""
                if streamed_text:
                    persist_text = streamed_text
            if not persist_text:
                persist_text = "(no content)"
            response_text, dropped = sanitize_with_diagnostics(
                persist_text,
                conversation=conversation,
                stage="persisted_message",
            )
            answer_confidence = None
            if plan.diagnostics:
                answer_confidence = plan.diagnostics.get("answer_confidence")
            pending_actions = serialize_planned_actions(plan.planned_actions)
            message_metadata = {
                "citations": [snippet.title for snippet in plan.citations],
                "actions": pending_actions,
                "diagnostics": plan.diagnostics,
            }
            if answer_confidence is not None:
                message_metadata["answer_confidence"] = answer_confidence
            if plan.ingestion_warnings:
                message_metadata["ingestion_warnings"] = [dict(item) for item in plan.ingestion_warnings]
            ai_message = service.append_message(
                session_token=session_token,
                sender=ConversationSender.AI,
                body=response_text,
                metadata=message_metadata,
            )

            session_state = service.get_session_state(session_token=session_token)
            final_payload = {
                "text": response_text,
                "message_id": str(ai_message.id),
                "session_status": session_state.status,
            }
            if answer_confidence is not None:
                final_payload["answer_confidence"] = answer_confidence
            if plan.ingestion_warnings:
                final_payload["ingestion_warnings"] = [dict(item) for item in plan.ingestion_warnings]
            logger.info(
                "portal response finalized conversation=%s message_id=%s status=%s",
                conversation.id,
                ai_message.id,
                session_state.status,
            )
            plan_holder["message_metadata"] = message_metadata
            plan_holder["final_payload"] = final_payload
            plan_holder["ai_message_id"] = ai_message.id

            def run_post_actions() -> None:
                close_old_connections()
                try:
                    action_results = []
                    if plan.planned_actions:
                        action_results = dispatcher.execute(conversation=conversation, planned_actions=plan.planned_actions)
                        logger.info(
                            "portal action results conversation=%s results=%s",
                            conversation.id,
                            [
                                {
                                    "action": result.action.value,
                                    "status": result.status,
                                    "error": result.error,
                                }
                                for result in action_results
                            ],
                        )
                    if plan.extractions:
                        service.store_extractions(
                            session_token=session_token,
                            items=((extraction.extraction_type, extraction.payload) for extraction in plan.extractions),
                        )
                        logger.info(
                            "portal extractions stored conversation=%s count=%s",
                            conversation.id,
                            len(plan.extractions),
                        )
                    if plan.planned_actions:
                        serialized_actions = serialize_action_results(action_results)
                        updated_metadata = copy.deepcopy(message_metadata)
                        updated_metadata["actions"] = serialized_actions
                        service.update_message(
                            session_token=session_token,
                            message_id=ai_message.id,
                            metadata=updated_metadata,
                        )
                        actions_queue.put(
                            {
                                "type": "actionsComplete",
                                "message_id": str(ai_message.id),
                                "actions": serialized_actions,
                            }
                        )
                    elif plan.extractions:
                        actions_queue.put(
                            {
                                "type": "actionsComplete",
                                "message_id": str(ai_message.id),
                                "actions": [],
                            }
                        )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.exception("portal post-processing failed: %s", exc)
                    actions_queue.put(
                        {
                            "type": "actionsError",
                            "message_id": str(ai_message.id),
                            "error": str(exc),
                        }
                    )
                finally:
                    close_old_connections()
                    actions_queue.put(actions_sentinel)

            if plan.planned_actions or plan.extractions:
                threading.Thread(target=run_post_actions, daemon=True).start()
            else:
                actions_queue.put(actions_sentinel)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Orchestrator finalize failed: %s", exc)
            plan_holder["final_error"] = str(exc)
            actions_queue.put(actions_sentinel)
        finally:
            close_old_connections()
            finalize_queue.put(finalize_sentinel)

    def orchestrate() -> None:
        close_old_connections()
        try:
            context = orchestrator.stream_turn(
                conversation=conversation,
                user_message=body,
                on_response_text_delta=on_response_text_delta,
                on_status_change=on_status_change,
                on_placeholder_response=on_placeholder_response,
                on_stream_complete=signal_stream_complete,
            )
            plan_holder["context"] = context
            threading.Thread(target=finalize_stream_context, args=(context,), daemon=True).start()
            # Streaming is complete; signal immediately so SSE can finish without waiting for planner/actions.
            signal_stream_complete()
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Orchestrator turn failed: %s", exc)
            plan_holder["error"] = str(exc)
            signal_stream_complete()
            finalize_queue.put(finalize_sentinel)
            actions_queue.put(actions_sentinel)
        finally:
            close_old_connections()

    worker = threading.Thread(target=orchestrate, daemon=True)
    worker.start()

    def event_stream() -> Iterable[str]:
        streamed_from_provider = False
        while True:
            try:
                chunk = stream_queue.get(timeout=0.1)
            except Empty:
                if worker.is_alive():
                    continue
                else:
                    continue
            if chunk is stream_sentinel:
                break
            if isinstance(chunk, dict):
                if chunk.get("type") == "context_progress":
                    state_value = chunk.get("state")
                    label_value = chunk.get("label")
                    data: dict[str, object] = {}
                    if isinstance(state_value, str):
                        data["state"] = state_value
                    if isinstance(label_value, str):
                        data["label"] = label_value
                    meta_value = chunk.get("meta")
                    if isinstance(meta_value, dict):
                        data["meta"] = meta_value
                    yield "event: context_progress\n"
                    yield f"data: {json.dumps(data)}\n\n"
                    continue
                if chunk.get("type") == "status":
                    state_value = chunk.get("state")
                    label_value = chunk.get("label")
                    data: dict[str, object] = {}
                    if isinstance(state_value, str):
                        data["state"] = state_value
                    if isinstance(label_value, str):
                        data["label"] = label_value
                    meta_value = chunk.get("meta")
                    if isinstance(meta_value, dict):
                        data["meta"] = meta_value
                    yield "event: status\n"
                    yield f"data: {json.dumps(data)}\n\n"
                    continue
            streamed_from_provider = True
            chunk_text = str(chunk)
            streamed_text_chunks.append(chunk_text)
            yield "event: delta\n"
            yield f"data: {json.dumps({'text': chunk_text})}\n\n"
        streamed_text = "".join(streamed_text_chunks)
        normalized_streamed = streamed_text.strip()

        session_status: str | None = None
        try:
            session_state = service.get_session_state(session_token=session_token)
            session_status = session_state.status
        except PortalNotFoundError:
            session_status = None

        context: StreamingTurnContext | None = None
        need_context_for_final = (not streamed_from_provider) or not normalized_streamed
        if need_context_for_final:
            worker.join()
            context = plan_holder.get("context")
            if not context:
                error_message = plan_holder.get("error", "AI orchestration failed")
                yield "event: error\n"
                yield f"data: {json.dumps(error_message)}\n\n"
                return
            if not streamed_from_provider:
                stream_text = "".join(context.streamed_chunks).strip() or context.response_text or ""
                for chunk in _response_chunks(stream_text):
                    streamed_text_chunks.append(chunk)
                    yield "event: delta\n"
                    yield f"data: {json.dumps({'text': chunk})}\n\n"
                normalized_streamed = "".join(streamed_text_chunks).strip()
        provisional_text = normalized_streamed
        if need_context_for_final and context:
            fallback_text = context.response_text or ""
            if not provisional_text:
                provisional_text = fallback_text
        provisional_payload = {
            "text": provisional_text,
            "message_id": None,
            "session_status": session_status,
            "pending": True,
        }
        yield "event: final\n"
        yield f"data: {json.dumps(provisional_payload)}\n\n"

        if not need_context_for_final:
            worker.join()
            context = plan_holder.get("context")

        finalize_queue.get()
        plan: AiOrchestratorPlan | None = plan_holder.get("plan")
        if not plan:
            error_message = plan_holder.get("final_error") or plan_holder.get("error", "AI orchestration failed")
            yield "event: error\n"
            yield f"data: {json.dumps(error_message)}\n\n"
            return
        logger.info(
            "portal plan ready conversation=%s actions=%s extractions=%s",
            conversation.id,
            [action.action.value for action in plan.planned_actions],
            [extraction.extraction_type.value for extraction in plan.extractions],
        )

        final_payload = plan_holder.get("final_payload")
        if not final_payload:
            error_message = plan_holder.get("final_error", "AI finalization failed")
            yield "event: error\n"
            yield f"data: {json.dumps(error_message)}\n\n"
            return

        final_payload = dict(final_payload)
        persisted_text = final_payload.get("text", "")
        effective_text = normalized_streamed or persisted_text
        message_id_value = final_payload.get("message_id")
        if effective_text and effective_text != persisted_text and message_id_value:
            try:
                message_uuid = uuid.UUID(str(message_id_value))
            except (TypeError, ValueError):
                message_uuid = None
            if message_uuid:
                service.update_message(
                    session_token=session_token,
                    message_id=message_uuid,
                    body=effective_text,
                )
                final_payload["text"] = effective_text

        final_payload["pending"] = False
        yield "event: turnPersisted\n"
        yield f"data: {json.dumps(final_payload)}\n\n"

        while True:
            post_event = actions_queue.get()
            if post_event is actions_sentinel:
                break
            if post_event.get("type") == "actionsComplete":
                payload = {
                    "message_id": post_event.get("message_id"),
                    "actions": post_event.get("actions", []),
                    "label": "Follow-up tasks completed.",
                }
                yield "event: actionsComplete\n"
                yield f"data: {json.dumps(payload)}\n\n"
            elif post_event.get("type") == "actionsError":
                payload = {
                    "message_id": post_event.get("message_id"),
                    "error": post_event.get("error", "Background workflow failed."),
                }
                yield "event: actionsError\n"
                yield f"data: {json.dumps(payload)}\n\n"

    return StreamingHttpResponse(event_stream(), content_type="text/event-stream")


@require_GET
def events(request: HttpRequest) -> StreamingHttpResponse:
    session_token = request.GET.get("session_token") or request.GET.get("sessionToken")
    if not session_token:
        return StreamingHttpResponse(status=400)
    service = _service()
    try:
        session = service.get_session_state(session_token=session_token)
    except PortalNotFoundError:
        return StreamingHttpResponse(status=404)

    def heartbeat_stream() -> Iterable[str]:
        yield "event: statusChanged\n"
        yield f"data: {json.dumps({'status': session.status})}\n\n"
        while True:
            yield "event: heartbeat\n"
            yield "data: {}\n\n"
            time.sleep(15)

    response = StreamingHttpResponse(heartbeat_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
