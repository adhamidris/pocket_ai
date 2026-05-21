from __future__ import annotations

import copy
import logging
import uuid
from typing import Mapping

from django.utils import timezone

from apps.conversations.content_blocks import new_block_id
from apps.conversations.models import ConversationToolApproval, PortalTurn, PortalTurnStatus
from apps.conversations.portal_turn.debug import _clip_debug_text, _json_safe_debug
from apps.mcp.runtime.tool_artifacts import store_remote_tool_output_artifact
from core.tenancy import tenant_context

logger = logging.getLogger(__name__)

TOOL_EVENT_PHASES = {"started", "finished", "approval_requested", "approval_resolved"}


class PortalTurnToolEventMixin:
    def on_tool_event(self, event: Mapping[str, object] | None) -> None:
        if not event or not isinstance(event, Mapping):
            return
        self.had_tool_events = True
        phase = str(event.get("phase") or "").strip().lower()
        if phase not in TOOL_EVENT_PHASES:
            return
        self.on_tool_decision("used")
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
        spinner_text = event.get("spinner_text")
        if spinner_text is not None:
            spinner_label = _clip_debug_text(spinner_text, limit=160)
            if spinner_label:
                payload["spinner_text"] = spinner_label

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
                    ConversationToolApproval.objects.filter(
                        id=approval_id,
                        turn_id__isnull=True,
                    ).update(turn_id=self.turn.id)
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
            # Tools are first-class UI blocks. If we're still streaming assistant text using
            # the RichBlockStreamBuilder (text → block ops), we must end the active text flow
            # before inserting the tool card so post-tool text cannot append "above" it.
            if not self.block_ops_active:
                try:
                    boundary_events = self.rich_builder.break_flow()
                except Exception:  # pragma: no cover - defensive
                    boundary_events = []
                if boundary_events:
                    self.emit_block_events(boundary_events)
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
        had_prior_phase = False
        if isinstance(existing_payload, Mapping):
            prior_phase = str(existing_payload.get("phase") or "").strip().lower()
            had_prior_phase = bool(prior_phase)
        merged_payload: dict[str, object] = dict(existing_payload) if isinstance(existing_payload, Mapping) else {}
        merged_payload.update(payload)
        tool_use_block["payload"] = merged_payload

        if phase in {"finished", "approval_resolved"}:
            if not had_prior_phase:
                # Some calls (including scope clarification repairs) may surface only a
                # terminal phase. Emit a synthetic "started" snapshot first so the
                # portal keeps the same spinner/tool lifecycle as every other tool.
                synthetic_payload = dict(merged_payload)
                synthetic_payload["phase"] = "started"
                synthetic_payload["status"] = "running"
                synthetic_tool_use_block = copy.deepcopy(tool_use_block)
                synthetic_tool_use_block["payload"] = synthetic_payload
                self.append_event("block_tool_use", {"block": synthetic_tool_use_block})
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
