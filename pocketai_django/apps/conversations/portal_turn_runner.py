from __future__ import annotations

import copy
import inspect
import logging
import threading
import re
import time
import uuid
from typing import Any, Callable, Mapping

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from apps.conversations.content_blocks import extract_text_from_content_blocks, new_block_id
from apps.conversations.portal import ChatPortalService
from apps.conversations.rich_blocks import RichBlockStreamBuilder, apply_block_ops, coerce_block_event
from apps.conversations.portal_turn_events import append_turn_event
from apps.conversations.portal_turn_folding import fold_turn_events
from apps.conversations.models import (
    Conversation,
    ConversationMessage,
    ConversationSender,
    ConversationToolApproval,
    PortalTurn,
    PortalTurnStatus,
)
from apps.llm.llm_provider import load_default_provider
from apps.mcp.sanitizer import sanitize_with_diagnostics
from apps.mcp.tool_artifacts import store_remote_tool_output_artifact
from apps.rag.ai_orchestrator import AiOrchestratorService
from core.tenancy import tenant_context

logger = logging.getLogger(__name__)

TOOL_EVENT_PHASES = {"started", "finished", "approval_requested", "approval_resolved"}


def _business_prefers_mcp(business: object | None, *, conversation: Conversation | None = None) -> bool:
    global_default = getattr(settings, "RAG_USE_MCP_ORCHESTRATOR", False)
    convo_meta = getattr(conversation, "metadata", None)
    if isinstance(convo_meta, dict) and convo_meta.get("mcp_required"):
        return True
    if business is None:
        return global_default
    metadata = getattr(business, "metadata", None)
    if not isinstance(metadata, dict):
        return global_default
    override = metadata.get("mcp_orchestrator_enabled")
    if override is None:
        return global_default
    return bool(override)


def _clip_debug_text(value: object, *, limit: int = 480) -> str:
    text = str(value or "").strip()
    if limit and len(text) > limit:
        return f"{text[: max(0, limit - 1)].rstrip()}…"
    return text


def _json_safe_debug(value: object, *, depth: int = 3, string_limit: int = 240, list_limit: int = 12) -> object:
    def _is_numeric_metric(v: object) -> bool:
        if isinstance(v, bool):
            return False
        if isinstance(v, (int, float)):
            return True
        if isinstance(v, str):
            candidate = v.strip()
            if not candidate:
                return False
            try:
                float(candidate)
            except ValueError:
                return False
            return True
        return False

    if value is None:
        return None
    if depth <= 0:
        return _clip_debug_text(value, limit=string_limit)
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, str):
            return _clip_debug_text(value, limit=string_limit)
        return value
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= list_limit:
                out["…"] = f"+{max(0, len(value) - list_limit)} more keys"
                break
            key_str = str(key or "").strip() or f"key_{idx}"
            lowered = key_str.lower()
            if any(token in lowered for token in ("password", "secret", "api_key", "apikey")):
                out[key_str] = "[REDACTED]"
                continue
            if "token" in lowered:
                safe_token_metrics = {
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "token_limit",
                    "token_count",
                    "tokens",
                }
                if lowered not in safe_token_metrics and not _is_numeric_metric(item):
                    out[key_str] = "[REDACTED]"
                    continue
            out[key_str] = _json_safe_debug(item, depth=depth - 1, string_limit=string_limit, list_limit=list_limit)
        return out
    if isinstance(value, (list, tuple, set)):
        out_list: list[object] = []
        for idx, entry in enumerate(value):
            if idx >= list_limit:
                out_list.append(f"+{max(0, len(value) - list_limit)} more")
                break
            out_list.append(_json_safe_debug(entry, depth=depth - 1, string_limit=string_limit, list_limit=list_limit))
        return out_list
    return _clip_debug_text(value, limit=string_limit)


class PortalTurnEventBuilder:
    def __init__(self, *, turn: PortalTurn, conversation: Conversation) -> None:
        self.turn = turn
        self.conversation = conversation
        self.blocks: list[dict[str, object]] = []
        self.blocks_by_id: dict[str, dict[str, object]] = {}
        self.block_ops_active = False
        self.rich_builder = RichBlockStreamBuilder()
        self.tool_use_block_id_by_event_id: dict[str, str] = {}
        self.reasoning_block_id_by_call_id: dict[str, str] = {}

    def append_event(self, event_type: str, payload: dict | None = None) -> None:
        append_turn_event(turn_id=self.turn.id, event_type=event_type, payload=payload or {})

    def _append_content_block(self, block: dict[str, object]) -> dict[str, object]:
        block_id = str(block.get("block_id") or "").strip()
        if not block_id:
            block_id = new_block_id()
            block["block_id"] = block_id
        self.blocks.append(block)
        self.blocks_by_id[block_id] = block
        return block

    def _get_content_block(self, block_id: str | None) -> dict[str, object] | None:
        key = (block_id or "").strip()
        if not key:
            return None
        return self.blocks_by_id.get(key)

    def _apply_block_event(self, event: Mapping[str, object]) -> None:
        event_type = str(event.get("type") or "").strip().lower()
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        if event_type == "block_start":
            block = payload.get("block")
            if not isinstance(block, Mapping):
                return
            block_id = str(block.get("block_id") or "").strip()
            if not block_id:
                return
            existing = self.blocks_by_id.get(block_id)
            if existing is not None:
                existing.clear()
                existing.update(block)
            else:
                self.blocks.append(dict(block))
                self.blocks_by_id[block_id] = self.blocks[-1]
            return
        if event_type == "block_delta":
            block_id = str(payload.get("block_id") or "").strip()
            if not block_id:
                return
            ops = payload.get("ops")
            if not isinstance(ops, list):
                return
            block = self.blocks_by_id.get(block_id)
            if not block:
                return
            apply_block_ops(block, ops)
            return
        if event_type == "block_end":
            return

    def emit_block_events(self, events: list[dict[str, object]]) -> None:
        for event in events:
            if not isinstance(event, Mapping):
                continue
            event_type = str(event.get("type") or "").strip()
            payload = dict(event.get("payload") or {})
            if event_type in {"block_start", "block_delta", "block_end"}:
                self._apply_block_event({"type": event_type, "payload": payload})
            self.append_event(event_type, payload)

    def on_block_event(self, event: Mapping[str, object] | None) -> None:
        if not event:
            return
        normalized = coerce_block_event(event)
        if not normalized:
            return
        if not self.block_ops_active:
            self.block_ops_active = True
        self.emit_block_events([normalized])

    def on_response_text_delta(self, chunk: str) -> None:
        if not chunk:
            return
        if self.block_ops_active:
            return
        events = self.rich_builder.feed_text(chunk)
        if events:
            self.emit_block_events(events)

    def on_reasoning_event(self, event: Mapping[str, object] | None) -> None:
        if not event or not isinstance(event, Mapping):
            return
        event_type = str(event.get("type") or "").strip().lower()
        if event_type not in {"reasoning_delta", "reasoning_end"}:
            return
        call_id = str(event.get("call_id") or "").strip()
        if not call_id:
            return
        stage = str(event.get("stage") or "").strip() or "llm"
        label = str(event.get("label") or "").strip() or stage.replace("_", " ").strip() or "LLM"
        block_id = self.reasoning_block_id_by_call_id.get(call_id)

        if event_type == "reasoning_delta":
            delta = event.get("delta")
            if not isinstance(delta, str) or not delta:
                return
            if not block_id:
                block = {
                    "block_id": new_block_id(),
                    "type": "reasoning",
                    "created_at": timezone.now().isoformat(),
                    "payload": {
                        "title": label,
                        "stage": stage,
                        "collapsed": False,
                        "code": "",
                    },
                }
                self._append_content_block(block)
                block_id = str(block.get("block_id") or "").strip()
                if not block_id:
                    return
                self.reasoning_block_id_by_call_id[call_id] = block_id
                self.append_event("block_start", {"block": copy.deepcopy(block)})
            block = self._get_content_block(block_id)
            if not block:
                return
            ops = [{"op": "append_code", "text": delta}]
            apply_block_ops(block, ops)
            self.append_event("block_delta", {"block_id": block_id, "ops": ops})
            return

        if event_type == "reasoning_end":
            if not block_id:
                return
            block = self._get_content_block(block_id)
            if block:
                payload_raw = block.get("payload")
                payload = dict(payload_raw) if isinstance(payload_raw, Mapping) else {}
                payload["collapsed"] = True
                payload["completed_at"] = timezone.now().isoformat()
                block["payload"] = payload
            self.append_event("block_end", {"block_id": block_id})

    def on_status_change(self, state: object) -> None:
        if not state:
            return
        code: str | None = None
        label: str | None = None
        meta: dict | None = None
        if isinstance(state, str):
            code = state.strip()
        elif isinstance(state, Mapping):
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
        payload: dict[str, object] = {"state": code}
        if label is not None:
            payload["label"] = label
        if meta:
            payload["meta"] = meta
        self.append_event("status", payload)

    def finalize_text(self) -> None:
        if not self.block_ops_active:
            events = self.rich_builder.finalize()
            if events:
                self.emit_block_events(events)

    def on_tool_event(self, event: Mapping[str, object] | None) -> None:
        if not event or not isinstance(event, Mapping):
            return
        phase = str(event.get("phase") or "").strip().lower()
        if phase not in TOOL_EVENT_PHASES:
            return
        tool_name = str(event.get("tool_name") or "").strip()
        kind = str(event.get("kind") or "").strip() or "tool"
        status_value = str(event.get("status") or "").strip()
        if not status_value:
            if phase == "started":
                status_value = "running"
            elif phase == "approval_requested":
                status_value = "pending_approval"

        event_id_value = str(event.get("event_id") or "").strip()
        tool_call_id_value = str(event.get("tool_call_id") or "").strip()
        candidate_keys: list[str] = []
        if tool_call_id_value:
            candidate_keys.append(tool_call_id_value)
        if event_id_value and event_id_value not in candidate_keys:
            candidate_keys.append(event_id_value)
        if not candidate_keys:
            return

        payload: dict[str, object] = {
            "event_id": event_id_value or candidate_keys[0],
            "phase": phase,
            "status": status_value,
            "tool_call_id": tool_call_id_value,
            "kind": kind,
            "tool_name": tool_name,
        }

        remote = event.get("remote") if isinstance(event.get("remote"), Mapping) else None
        if not remote:
            output_hint = event.get("output") if isinstance(event.get("output"), Mapping) else None
            remote_hint = output_hint.get("remote") if isinstance(output_hint, Mapping) else None
            if isinstance(remote_hint, Mapping):
                remote = remote_hint
        if remote:
            safe_remote: dict[str, object] = {}
            connection_name = remote.get("connection_name") or remote.get("connectionName")
            remote_tool = (
                remote.get("remote_tool")
                or remote.get("remoteTool")
                or remote.get("tool")
                or remote.get("tool_name")
                or remote.get("toolName")
            )
            if connection_name:
                safe_remote["connection_name"] = _clip_debug_text(connection_name, limit=120)
            if remote_tool:
                safe_remote["remote_tool"] = _clip_debug_text(remote_tool, limit=120)
            if safe_remote:
                payload["remote"] = safe_remote

        approval_payload = event.get("approval") if isinstance(event.get("approval"), Mapping) else None
        approval_id = event.get("approval_id") or event.get("approvalId")
        if approval_payload:
            payload["approval"] = _json_safe_debug(approval_payload, depth=4, string_limit=480, list_limit=24)
            if not approval_id:
                approval_id = approval_payload.get("id")
        if approval_id:
            payload["approval_id"] = str(approval_id)
            # Link approval to this turn for traceability.
            try:
                with tenant_context(getattr(self.conversation, "business_profile_id", None)):
                    ConversationToolApproval.objects.filter(id=approval_id).update(turn_id=self.turn.id)
            except Exception:  # pragma: no cover - best effort
                logger.exception("portal turn approval link failed approval=%s", approval_id)
            if phase == "approval_requested":
                PortalTurn.objects.filter(id=self.turn.id).update(status=PortalTurnStatus.WAITING_APPROVAL)
            elif phase == "approval_resolved":
                PortalTurn.objects.filter(id=self.turn.id).update(status=PortalTurnStatus.STREAMING)

        input_payload = event.get("input")
        if input_payload is not None and phase in {"started", "approval_requested", "finished", "approval_resolved"}:
            input_string_limit = 720
            input_list_limit = 32
            if tool_name.strip().lower() == "email_create_draft":
                input_string_limit = 12_000
                input_list_limit = 96
            payload["input"] = _json_safe_debug(
                input_payload,
                depth=3,
                string_limit=input_string_limit,
                list_limit=input_list_limit,
            )

        tool_use_block_id: str | None = None
        for key in candidate_keys:
            tool_use_block_id = self.tool_use_block_id_by_event_id.get(key)
            if tool_use_block_id:
                break
        tool_use_block = self._get_content_block(tool_use_block_id) if tool_use_block_id else None
        if not tool_use_block:
            tool_use_block = {
                "block_id": new_block_id(),
                "type": "tool_use",
                "created_at": timezone.now().isoformat(),
                "payload": {},
            }
            self._append_content_block(tool_use_block)
            tool_use_block_id = str(tool_use_block.get("block_id") or "").strip()
            if tool_use_block_id:
                for key in candidate_keys:
                    self.tool_use_block_id_by_event_id[key] = tool_use_block_id
        if tool_use_block_id:
            for key in candidate_keys:
                self.tool_use_block_id_by_event_id[key] = tool_use_block_id

        existing_payload = tool_use_block.get("payload")
        merged_payload: dict[str, object] = dict(existing_payload) if isinstance(existing_payload, Mapping) else {}
        merged_payload.update(payload)
        tool_use_block["payload"] = merged_payload

        if phase in {"finished", "approval_resolved"}:
            duration = event.get("duration_ms")
            try:
                payload["duration_ms"] = int(duration) if duration is not None else 0
            except (TypeError, ValueError):
                payload["duration_ms"] = 0
            output_payload = event.get("output")
            artifact_id: str | None = None
            output_preview: object | None = None
            if output_payload is not None:
                scrubbed_output: object = output_payload
                if isinstance(output_payload, Mapping):
                    output_copy: dict[str, object] = dict(output_payload)
                    remote_out = output_copy.get("remote")
                    if isinstance(remote_out, Mapping):
                        safe_out_remote: dict[str, object] = {}
                        connection_name = remote_out.get("connection_name")
                        remote_tool = remote_out.get("tool") or remote_out.get("remote_tool")
                        if connection_name:
                            safe_out_remote["connection_name"] = _clip_debug_text(connection_name, limit=120)
                        if remote_tool:
                            safe_out_remote["remote_tool"] = _clip_debug_text(remote_tool, limit=120)
                        if safe_out_remote:
                            output_copy["remote"] = safe_out_remote
                        else:
                            output_copy.pop("remote", None)
                    scrubbed_output = output_copy

                output_preview = _json_safe_debug(scrubbed_output, depth=3, string_limit=720, list_limit=24)
                kind_lower = kind.lower().strip()
                if isinstance(output_payload, Mapping):
                    artifact_raw = output_payload.get("artifact_id") or output_payload.get("artifactId")
                    if isinstance(artifact_raw, str) and artifact_raw.strip():
                        artifact_id = artifact_raw.strip()
                    prompt_view = output_payload.get("prompt_view") or output_payload.get("promptView")
                    if prompt_view is not None:
                        output_preview = _json_safe_debug(prompt_view, depth=3, string_limit=720, list_limit=24)

                if artifact_id is None and kind_lower.startswith("mcp"):
                    try:
                        output_artifact = _json_safe_debug(scrubbed_output, depth=6, string_limit=4800, list_limit=96)
                        with tenant_context(getattr(self.conversation, "business_profile_id", None)):
                            artifact_id = store_remote_tool_output_artifact(
                                conversation=self.conversation,
                                tool_call_id=tool_call_id_value,
                                tool_event_id=event_id_value or candidate_keys[0],
                                invoked_tool=tool_name,
                                remote_event_payload=payload,
                                tool_result=output_artifact
                                if isinstance(output_artifact, Mapping)
                                else {"output": output_artifact},
                            )
                    except Exception:  # pragma: no cover
                        artifact_id = None

            if artifact_id:
                payload["artifact_id"] = artifact_id
            if output_preview is not None:
                payload["output_preview"] = output_preview

            existing_payload = tool_use_block.get("payload")
            merged_payload = dict(existing_payload) if isinstance(existing_payload, Mapping) else {}
            merged_payload.update(payload)
            tool_use_block["payload"] = merged_payload

            self.append_event("block_tool_result", {"block": copy.deepcopy(tool_use_block)})

            try:
                if isinstance(output_payload, Mapping) and str(payload.get("status") or "").strip().lower() in {"ok", "success"}:
                    created_blocks: list[dict[str, object]] = []
                    tool_lower = tool_name.strip().lower()

                    if tool_lower in {"pdf_generate", "pdf_merge", "pdf_extract_pages"}:
                        artifact = output_payload.get("artifact")
                        if isinstance(artifact, Mapping):
                            file_id_raw = artifact.get("file_id") or artifact.get("fileId") or artifact.get("id")
                            filename = str(artifact.get("filename") or "").strip()
                            try:
                                file_uuid = uuid.UUID(str(file_id_raw))
                            except (TypeError, ValueError):
                                file_uuid = None
                            if file_uuid:
                                from apps.conversations.models import ConversationFile
                                from apps.conversations.portal_files import portal_file_block

                                with tenant_context(getattr(self.conversation, "business_profile_id", None)):
                                    file_obj = ConversationFile.objects.filter(id=file_uuid, conversation=self.conversation).first()
                                if file_obj is not None:
                                    label_map = {
                                        "pdf_generate": "Generated",
                                        "pdf_merge": "Merged",
                                        "pdf_extract_pages": "Extracted pages",
                                    }
                                    created_blocks.append(portal_file_block(file_obj, label=label_map.get(tool_lower, "Generated")))
                                else:
                                    created_blocks.append(
                                        {
                                            "block_id": new_block_id(),
                                            "type": "file",
                                            "created_at": timezone.now().isoformat(),
                                            "payload": {
                                                "file_id": str(file_uuid),
                                                "filename": filename or "document.pdf",
                                                "content_type": "application/pdf",
                                                "size_bytes": 0,
                                                "page_count": 0,
                                                "kind": "artifact",
                                                "status": "ready",
                                                "label": "Generated",
                                            },
                                        }
                                    )

                    elif tool_lower == "pdf_extract_text":
                        file_meta = output_payload.get("file")
                        text_value = output_payload.get("text")
                        if isinstance(file_meta, Mapping) and isinstance(text_value, str) and text_value.strip():
                            file_id_raw = file_meta.get("id") or file_meta.get("file_id") or file_meta.get("fileId")
                            filename = str(file_meta.get("filename") or "").strip() or "document.pdf"
                            try:
                                file_uuid = uuid.UUID(str(file_id_raw))
                            except (TypeError, ValueError):
                                file_uuid = None
                            if file_uuid:
                                from apps.conversations.portal_files import portal_file_text_block

                                created_blocks.append(
                                    portal_file_text_block(
                                        file_id=file_uuid,
                                        filename=filename,
                                        page_count=int(file_meta.get("page_count") or 0),
                                        text=text_value.strip(),
                                        title=f"Extracted text from {filename}",
                                        collapsed=True,
                                    )
                                )

                    for block in created_blocks:
                        self._append_content_block(block)
                        self.append_event("block_start", {"block": copy.deepcopy(block)})
            except Exception:  # pragma: no cover
                logger.exception("portal turn file block creation failed for tool=%s", tool_name)
        else:
            self.append_event("block_tool_use", {"block": copy.deepcopy(tool_use_block)})


class PortalTurnRunner:
    def __init__(self, *, turn: PortalTurn, conversation: Conversation) -> None:
        self.turn = turn
        self.conversation = conversation
        self.service = ChatPortalService()
        self.builder = PortalTurnEventBuilder(turn=turn, conversation=conversation)

    def _select_orchestrator(self):
        agent = self.conversation.agent_profile
        if not agent:
            raise ValueError("Agent profile is missing")
        use_mcp = _business_prefers_mcp(self.conversation.business_profile, conversation=self.conversation)
        if use_mcp:
            from apps.llm.llm_provider import load_mcp_provider
            from apps.mcp.orchestrator import McpOrchestratorService

            provider = load_mcp_provider()
            orchestrator = McpOrchestratorService(agent=agent, provider=provider)
            return orchestrator
        provider = load_default_provider()
        return AiOrchestratorService(agent=agent, provider=provider)

    def run(self) -> None:
        close_old_connections()
        orchestrator = self._select_orchestrator()
        user_message = str(self.turn.user_message or "").strip()
        if not user_message:
            raise ValueError("PortalTurn.user_message is empty")

        PortalTurn.objects.filter(id=self.turn.id).update(status=PortalTurnStatus.STREAMING, updated_at=timezone.now())

        stream_kwargs = {
            "conversation": self.conversation,
            "user_message": user_message,
            "on_response_text_delta": self.builder.on_response_text_delta,
            "on_status_change": self.builder.on_status_change,
            "on_placeholder_response": lambda _text: None,
            "on_stream_complete": lambda: None,
            "on_spinner_update": lambda _text: None,
            "on_tool_event": self.builder.on_tool_event,
            "on_block_event": self.builder.on_block_event,
            "on_reasoning_event": self.builder.on_reasoning_event,
            "should_cancel": lambda: False,
        }
        try:
            parameters = inspect.signature(orchestrator.stream_turn).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "wait_for_tool_approval" in parameters:
            stream_kwargs["wait_for_tool_approval"] = True
        stream_context = orchestrator.stream_turn(**stream_kwargs)

        self.builder.finalize_text()

        PortalTurn.objects.filter(id=self.turn.id).update(status=PortalTurnStatus.FINALIZING, updated_at=timezone.now())

        # Materialize content blocks from the event log for deterministic persistence.
        with tenant_context(getattr(self.conversation, "business_profile_id", None)):
            blocks = fold_turn_events(turn_id=self.turn.id)

        body_text = extract_text_from_content_blocks(blocks)
        if not body_text:
            streamed_text = "".join(stream_context.streamed_chunks or ()).strip()
            body_text = streamed_text or "(no content)"

        # Sanitize final text the same way the portal does for persistence.
        body_text, _ = sanitize_with_diagnostics(body_text, conversation=self.conversation, stage="portal_turn_finalize")

        if self.turn.message_id:
            self.service.update_message(
                session_token=self.conversation.session_token,
                message_id=self.turn.message_id,
                body=body_text,
                content_blocks=blocks,
                conversation=self.conversation,
            )
            message_id = self.turn.message_id
        else:
            message = self.service.append_message(
                session_token=self.conversation.session_token,
                sender=ConversationSender.AI,
                body=body_text,
                content_blocks=blocks,
                conversation=self.conversation,
            )
            message_id = message.id
            PortalTurn.objects.filter(id=self.turn.id).update(message_id=message_id)

        PortalTurn.objects.filter(id=self.turn.id).update(
            status=PortalTurnStatus.FINALIZED,
            finalized_at=timezone.now(),
            updated_at=timezone.now(),
        )


def run_turn_background(*, turn_id: uuid.UUID, business_id: object | None = None) -> None:
    def _run() -> None:
        close_old_connections()

        def _run_with_context() -> None:
            turn = PortalTurn.objects.select_related("conversation").filter(id=turn_id).first()
            if not turn:
                return
            runner = PortalTurnRunner(turn=turn, conversation=turn.conversation)
            runner.run()

        if business_id is not None:
            with tenant_context(business_id):
                _run_with_context()
        else:  # pragma: no cover - fallback for non-tenant callers
            logger.warning("portal turn runner missing business_id for turn=%s", turn_id)
            _run_with_context()

    threading.Thread(target=_run, daemon=True).start()
