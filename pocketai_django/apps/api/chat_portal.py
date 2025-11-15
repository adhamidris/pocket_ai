from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from queue import Empty, Queue
from typing import Iterable

from django.db import close_old_connections
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.conversations.models import ConversationSender
from apps.services.ai_orchestrator import ActionDispatcher, AiOrchestratorPlan, AiOrchestratorService
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

    def _response_chunks(text: str, chunk_size: int = 240) -> Iterable[str]:
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
    plan_holder: dict[str, Any] = {}

    def on_response_text_delta(chunk: str) -> None:
        if chunk:
            stream_queue.put(chunk)

    def on_status_change(state: str) -> None:
        if state:
            stream_queue.put({"type": "status", "state": state})

    def on_placeholder_response(text: str) -> None:
        clean = (text or "").strip()
        if clean:
            stream_queue.put({"type": "placeholder", "text": clean})

    def orchestrate() -> None:
        close_old_connections()
        try:
            plan_holder["plan"] = orchestrator.run_turn(
                conversation=conversation,
                user_message=body,
                on_response_text_delta=on_response_text_delta,
                on_status_change=on_status_change,
                on_placeholder_response=on_placeholder_response,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Orchestrator turn failed: %s", exc)
            plan_holder["error"] = str(exc)
        finally:
            close_old_connections()
            stream_queue.put(stream_sentinel)

    worker = threading.Thread(target=orchestrate, daemon=True)
    worker.start()

    placeholder_message = None

    def event_stream() -> Iterable[str]:
        nonlocal placeholder_message
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
                if chunk.get("type") == "status":
                    yield "event: status\n"
                    yield f"data: {json.dumps({'state': chunk.get('state')})}\n\n"
                    continue
                if chunk.get("type") == "placeholder":
                    placeholder_text = chunk.get("text") or ""
                    payload = {"text": placeholder_text}
                    if placeholder_text and placeholder_message is None:
                        placeholder_message = service.append_message(
                            session_token=session_token,
                            sender=ConversationSender.AI,
                            body=placeholder_text,
                            metadata={"placeholder": True},
                        )
                        payload["message_id"] = str(placeholder_message.id)
                    yield "event: placeholder\n"
                    yield f"data: {json.dumps(payload)}\n\n"
                    continue
            streamed_from_provider = True
            yield "event: delta\n"
            yield f"data: {json.dumps({'text': chunk})}\n\n"
        worker.join()
        plan: AiOrchestratorPlan | None = plan_holder.get("plan")
        if not plan:
            error_message = plan_holder.get("error", "AI orchestration failed")
            yield "event: error\n"
            yield f"data: {json.dumps(error_message)}\n\n"
            return
        else:
            logger.info(
                "portal plan ready conversation=%s actions=%s extractions=%s",
                conversation.id,
                [action.action.value for action in plan.planned_actions],
                [extraction.extraction_type.value for extraction in plan.extractions],
            )
        if not streamed_from_provider:
            stream_text = plan.diagnostics.get("response_stream_text") if plan and plan.diagnostics else None
            stream_text = stream_text or plan.response_text
            for chunk in _response_chunks(stream_text):
                yield "event: delta\n"
                yield f"data: {json.dumps({'text': chunk})}\n\n"

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

        serialized_actions = serialize_action_results(action_results)
        answer_confidence = None
        if plan.diagnostics:
            answer_confidence = plan.diagnostics.get("answer_confidence")
        message_metadata = {
            "citations": [snippet.title for snippet in plan.citations],
            "actions": serialized_actions,
            "diagnostics": plan.diagnostics,
        }
        if answer_confidence is not None:
            message_metadata["answer_confidence"] = answer_confidence
        if plan.ingestion_warnings:
            message_metadata["ingestion_warnings"] = [dict(item) for item in plan.ingestion_warnings]
        ai_message = service.append_message(
            session_token=session_token,
            sender=ConversationSender.AI,
            body=plan.response_text,
            metadata=message_metadata,
        )

        session_state = service.get_session_state(session_token=session_token)
        final_payload = {
            "text": plan.response_text,
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
        yield "event: final\n"
        yield f"data: {json.dumps(final_payload)}\n\n"

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
logger = logging.getLogger(__name__)
