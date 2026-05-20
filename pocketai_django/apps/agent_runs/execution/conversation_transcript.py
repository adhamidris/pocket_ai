from __future__ import annotations

import uuid
from typing import Mapping

from django.utils import timezone

from apps.agent_runs.models import AgentRun
from apps.conversations.content_blocks import ensure_assistant_text_blocks
from apps.conversations.models import Conversation, ConversationChannel, ConversationMessage, ConversationSender


class AgentRunConversationTranscriptMixin:
    def _resolve_execution_conversation(
        self,
        *,
        run: AgentRun,
        business_id,
        run_metadata: Mapping[str, object],
        anchor_conversation: Conversation | None,
        conversation_summary: str,
    ) -> tuple[Conversation, Mapping[str, object]]:
        execution_conversation: Conversation | None = None
        if run.execution_conversation_id:
            execution_conversation = Conversation.objects.filter(
                id=run.execution_conversation_id,
                business_profile_id=business_id,
            ).first()

        anchor_meta_map = anchor_conversation.metadata if isinstance(getattr(anchor_conversation, "metadata", None), Mapping) else {}
        anchor_source = str(anchor_meta_map.get("source") or "").strip().lower()
        if execution_conversation is None:
            if anchor_conversation is not None and anchor_source == "agent_run":
                execution_conversation = anchor_conversation
            else:
                actor_user_id = run.created_by_id
                if not actor_user_id:
                    actor_raw = str(anchor_meta_map.get("actor_user_id") or anchor_meta_map.get("actorUserId") or "").strip()
                    if actor_raw:
                        try:
                            actor_user_id = uuid.UUID(actor_raw)
                        except (TypeError, ValueError):
                            actor_user_id = None
                if not actor_user_id:
                    actor_user_id = getattr(run.agent_profile, "user_id", None)
                exec_metadata: dict[str, object] = {
                    "source": "agent_run",
                    "agent_run_id": str(run.id),
                }
                if anchor_conversation is not None:
                    exec_metadata["anchor_conversation_id"] = str(anchor_conversation.id)
                if actor_user_id:
                    exec_metadata["actor_user_id"] = str(actor_user_id)
                execution_conversation = Conversation.objects.create(
                    business_profile_id=business_id,
                    agent_profile_id=run.agent_profile_id,
                    channel=ConversationChannel.API,
                    metadata=exec_metadata,
                    summary=conversation_summary,
                )

        if execution_conversation is not None and run.execution_conversation_id != execution_conversation.id:
            next_meta = dict(run_metadata)
            next_meta["execution_conversation_id"] = str(execution_conversation.id)
            AgentRun.objects.filter(id=run.id).update(
                execution_conversation=execution_conversation,
                metadata=next_meta,
                updated_at=timezone.now(),
            )
            run.execution_conversation = execution_conversation
            run.metadata = next_meta
            run_metadata = next_meta

        if execution_conversation is None:  # pragma: no cover - defensive
            raise RuntimeError("Unable to resolve agent run execution conversation.")

        if not str(getattr(execution_conversation, "summary", "") or "").strip() and conversation_summary:
            Conversation.objects.filter(id=execution_conversation.id).update(summary=conversation_summary)
        return execution_conversation, run_metadata

    def _append_execution_message(
        self,
        *,
        execution_conversation: Conversation,
        sender: str,
        body: str,
        metadata: Mapping[str, object] | None = None,
        content_blocks: list[dict[str, object]] | None = None,
    ) -> None:
        text = str(body or "").strip()
        if not text:
            return
        last = execution_conversation.messages.order_by("-sent_at", "-created_at").only("id", "sender", "body").first()
        if last and last.sender == sender and str(last.body or "").strip() == text:
            return
        create_kwargs: dict[str, object] = {
            "conversation": execution_conversation,
            "sender": sender,
            "body": text,
            "metadata": dict(metadata or {}),
        }
        if content_blocks is not None:
            create_kwargs["content_blocks"] = content_blocks
        elif sender == ConversationSender.AI:
            create_kwargs["content_blocks"] = ensure_assistant_text_blocks(text)
        ConversationMessage.objects.create(**create_kwargs)
        Conversation.objects.filter(id=execution_conversation.id).update(last_activity_at=timezone.now())

    def _ensure_execution_seed_prompt(
        self,
        *,
        run: AgentRun,
        execution_conversation: Conversation,
        seed_prompt: str,
    ) -> None:
        if execution_conversation.messages.exists():
            return
        self._append_execution_message(
            execution_conversation=execution_conversation,
            sender=ConversationSender.CUSTOMER,
            body=seed_prompt,
            metadata={"source": "agent_run", "agent_run_id": str(run.id), "type": "run_seed"},
        )

    def _feed_external_inputs(
        self,
        *,
        run: AgentRun,
        execution_conversation: Conversation,
        run_metadata: Mapping[str, object],
        metadata_snapshot: Mapping[str, object],
    ) -> tuple[Mapping[str, object], Mapping[str, object]]:
        cursor = 0
        try:
            cursor = int(metadata_snapshot.get("external_inputs_cursor") or 0)
        except (TypeError, ValueError):
            cursor = 0
        raw_external_inputs = metadata_snapshot.get("external_inputs") if isinstance(metadata_snapshot, Mapping) else None
        external_inputs = raw_external_inputs if isinstance(raw_external_inputs, list) else []
        if cursor < 0:
            cursor = 0
        if cursor > len(external_inputs):
            cursor = len(external_inputs)
        new_external = external_inputs[cursor:]
        if not new_external:
            return run_metadata, metadata_snapshot

        lines: list[str] = []
        for item in new_external[-3:]:
            if not isinstance(item, Mapping):
                continue
            subject = str(item.get("subject") or "").strip()
            resolution = str(item.get("resolution") or "").strip()
            if resolution:
                resolution = resolution[:800].rstrip()
            if subject and resolution:
                lines.append(f"- {subject}: {resolution}")
            elif subject:
                lines.append(f"- {subject}")
            elif resolution:
                lines.append(f"- {resolution}")
        if lines:
            external_message = "External inputs received:\n" + "\n".join(lines)
            self._append_execution_message(
                execution_conversation=execution_conversation,
                sender=ConversationSender.CUSTOMER,
                body=external_message,
                metadata={"source": "agent_run", "agent_run_id": str(run.id), "type": "external_inputs"},
            )
        updated_meta = dict(run_metadata) if isinstance(run_metadata, Mapping) else {}
        updated_meta["external_inputs_cursor"] = len(external_inputs)
        run.metadata = updated_meta
        return updated_meta, updated_meta
