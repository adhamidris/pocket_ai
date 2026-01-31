"""
Conversation compaction service.

Summarizes older message segments into compacted history records so long-running
conversations can retain context without overflowing prompts.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any, Iterable, Mapping

from django.conf import settings
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _compaction_embedding_service():
    """
    Lazily construct the shared embedding service for compacted-history retrieval.

    Uses the same provider selection as the knowledge base (OpenAI → local → None).
    """

    try:
        from apps.rag.embeddings import build_embedding_service
    except Exception:  # pragma: no cover - defensive
        return None
    return build_embedding_service()


class ContextCompactionService:
    def __init__(self, provider: Any | None = None) -> None:
        self._provider = provider

    def should_compact(self, conversation) -> bool:
        if not getattr(settings, "MCP_COMPACTION_ENABLED", True):
            return False
        metadata = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
        if metadata.get("compaction_in_progress"):
            return False

        messages = self._fetch_uncompacted_messages(conversation)
        preserve_last_n = self._preserve_last_n()
        if len(messages) <= preserve_last_n:
            return False
        candidates = messages[:-preserve_last_n]
        if not candidates:
            return False

        if metadata.get("pending_compaction"):
            return True

        total_tokens = self._estimate_tokens_for_messages(messages)
        trigger_tokens = int(self._context_window_tokens() * self._trigger_ratio())
        return total_tokens >= max(1, trigger_tokens)

    def is_safe_to_compact(self, conversation) -> bool:
        from apps.conversations.models import AgentRun, AgentRunStatus, ConversationToolApprovalStatus

        # Avoid compaction while tool approvals are pending.
        if conversation.tool_approvals.filter(status=ConversationToolApprovalStatus.PENDING).exists():
            return False

        metadata = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
        if metadata.get("compaction_in_progress"):
            return False

        source = str(metadata.get("source") or "").strip().lower()
        run_id = str(metadata.get("agent_run_id") or metadata.get("agentRunId") or "").strip()
        if source == "agent_run" and run_id:
            run = AgentRun.objects.filter(id=run_id).only("status").first()
            if run and run.status in {
                AgentRunStatus.RUNNING,
                AgentRunStatus.QUEUED,
                AgentRunStatus.PAUSED,
                AgentRunStatus.WAITING_USER,
            }:
                return False

        return True

    def mark_pending(self, conversation) -> None:
        metadata = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
        metadata = dict(metadata)
        metadata["pending_compaction"] = True
        metadata["pending_compaction_at"] = timezone.now().isoformat()
        conversation.metadata = metadata
        conversation.__class__.objects.filter(id=conversation.id).update(metadata=metadata)

    def compact(self, *, conversation, preserve_last_n: int | None = None):
        close_old_connections()
        if not getattr(settings, "MCP_COMPACTION_ENABLED", True):
            return None

        if not self.is_safe_to_compact(conversation):
            self.mark_pending(conversation)
            return None

        from apps.conversations.models import CompactedHistorySegment

        metadata = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
        if metadata.get("compaction_in_progress"):
            return None

        metadata = dict(metadata)
        metadata["compaction_in_progress"] = True
        conversation.metadata = metadata
        conversation.__class__.objects.filter(id=conversation.id).update(metadata=metadata)

        try:
            messages = self._fetch_uncompacted_messages(conversation)
            preserve_last_n = preserve_last_n if preserve_last_n is not None else self._preserve_last_n()
            if len(messages) <= preserve_last_n:
                self._clear_pending(conversation)
                return None

            candidates = messages[:-preserve_last_n]
            if not candidates:
                self._clear_pending(conversation)
                return None

            total_tokens = self._estimate_tokens_for_messages(messages)
            target_tokens = int(self._context_window_tokens() * self._target_ratio())
            pending = bool(metadata.get("pending_compaction"))
            if total_tokens <= max(1, target_tokens) and not pending:
                self._clear_pending(conversation)
                return None

            candidates = self._slice_candidates_for_target(candidates, total_tokens, target_tokens)
            if not candidates:
                self._clear_pending(conversation)
                return None

            full_messages = [self._serialize_message(msg) for msg in candidates]
            summary_text = self._generate_summary(candidates)
            extracted_facts, extracted_decisions = self._extract_critical_context(candidates)
            embedding_vector = self._embed_segment(summary_text, extracted_facts, extracted_decisions)

            start_pos = self._message_position(conversation, candidates[0])
            end_pos = self._message_position(conversation, candidates[-1])
            segment_range = f"turns_{start_pos}_to_{end_pos}"

            token_count_original = self._estimate_tokens_for_messages(candidates)
            token_count_summary = self._estimate_tokens_for_text(summary_text)
            compression_ratio = (
                float(token_count_summary) / float(token_count_original) if token_count_original else 0.0
            )

            segment = CompactedHistorySegment.objects.create(
                conversation=conversation,
                segment_range=segment_range,
                start_message_id=candidates[0].id,
                end_message_id=candidates[-1].id,
                summary=summary_text,
                full_messages=full_messages,
                embedding=embedding_vector,
                extracted_facts=extracted_facts,
                extracted_decisions=extracted_decisions,
                token_count_original=token_count_original,
                token_count_summary=token_count_summary,
                compression_ratio=compression_ratio,
            )

            self._clear_pending(conversation, last_end_message_id=str(candidates[-1].id))
            return segment
        except Exception:  # pragma: no cover - defensive
            logger.exception("conversation_compaction_failed conversation=%s", conversation.id)
            self._clear_pending(conversation)
            return None

    def _clear_pending(self, conversation, *, last_end_message_id: str | None = None) -> None:
        metadata = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
        metadata = dict(metadata)
        metadata.pop("pending_compaction", None)
        metadata.pop("pending_compaction_at", None)
        metadata.pop("compaction_in_progress", None)
        if last_end_message_id:
            metadata["last_compacted_message_id"] = last_end_message_id
            metadata["last_compacted_at"] = timezone.now().isoformat()
        conversation.metadata = metadata
        conversation.__class__.objects.filter(id=conversation.id).update(metadata=metadata)

    def _fetch_uncompacted_messages(self, conversation):
        from apps.conversations.models import CompactedHistorySegment, ConversationMessage

        qs = (
            ConversationMessage.objects.filter(conversation=conversation)
            .order_by("sent_at", "created_at")
            .only("id", "sender", "body", "metadata", "content_blocks", "sent_at", "created_at")
        )
        last_segment = (
            CompactedHistorySegment.objects.filter(conversation=conversation)
            .order_by("-compacted_at")
            .only("end_message_id")
            .first()
        )
        if last_segment and last_segment.end_message_id:
            end_msg = ConversationMessage.objects.filter(
                conversation=conversation,
                id=last_segment.end_message_id,
            ).only("sent_at", "created_at").first()
            if end_msg:
                qs = qs.filter(
                    Q(sent_at__gt=end_msg.sent_at)
                    | (Q(sent_at=end_msg.sent_at) & Q(created_at__gt=end_msg.created_at))
                )
        return list(qs)

    def _slice_candidates_for_target(self, candidates, total_tokens: int, target_tokens: int):
        max_messages = self._max_messages_per_segment()
        if total_tokens <= max(1, target_tokens):
            return candidates[:max_messages] if max_messages else candidates

        tokens_to_remove = max(1, total_tokens - max(1, target_tokens))
        removed = 0
        batch: list[Any] = []
        for msg in candidates:
            removed += self._estimate_tokens_for_message(msg)
            batch.append(msg)
            if max_messages and len(batch) >= max_messages:
                break
            if removed >= tokens_to_remove:
                break
        return batch

    def _generate_summary(self, messages) -> str:
        max_chars = int(getattr(settings, "MCP_COMPACTION_SUMMARY_MAX_CHARS", 4000) or 0)
        transcript = self._build_transcript(messages)
        if not transcript:
            return ""

        provider = self._provider
        if provider is None:
            try:
                from apps.llm.llm_provider import load_default_provider

                provider = load_default_provider()
            except Exception:
                provider = None

        if provider:
            prompt = (
                "Summarize the conversation segment below. Preserve key facts, amounts, "
                "dates, identifiers, and decisions. Use concise bullet points. "
                f"Keep under {max_chars} characters.\n\nSegment:\n{transcript}"
            )
            messages_payload = [
                {"role": "system", "content": "You are a concise summarizer of chat history."},
                {"role": "user", "content": prompt},
            ]
            try:
                response = provider.chat(
                    messages=messages_payload,
                    temperature=0.1,
                    max_tokens=min(800, max(200, max_chars // 4 + 50)),
                )
                content = str(response.get("content") or "").strip()
                if content:
                    return self._clip_text(content, max_chars)
            except Exception:
                logger.exception("compaction_summary_failed")

        fallback = transcript
        if max_chars:
            fallback = self._clip_text(fallback, max_chars)
        return fallback

    def _embed_segment(
        self,
        summary_text: str,
        extracted_facts: Mapping[str, object] | None,
        extracted_decisions: Mapping[str, object] | None,
    ) -> list[float] | None:
        embedder = _compaction_embedding_service()
        if not embedder:
            return None
        summary_clean = str(summary_text or "").strip()
        if not summary_clean:
            return None

        parts: list[str] = [summary_clean]
        if extracted_facts:
            try:
                parts.append("Facts: " + json.dumps(extracted_facts, ensure_ascii=False, separators=(",", ":"), default=str))
            except Exception:
                parts.append("Facts: " + str(extracted_facts))
        if extracted_decisions:
            try:
                parts.append(
                    "Decisions: "
                    + json.dumps(extracted_decisions, ensure_ascii=False, separators=(",", ":"), default=str)
                )
            except Exception:
                parts.append("Decisions: " + str(extracted_decisions))
        text = "\n".join(parts)

        max_chars = int(getattr(settings, "MCP_COMPACTION_EMBED_TEXT_MAX_CHARS", 8000) or 0)
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars].rstrip()

        try:
            vector = embedder.embed_text(text)
        except Exception:
            logger.exception("compaction_embedding_failed")
            return None
        if not vector:
            return None
        try:
            expected_dim = int(getattr(settings, "EMBED_DIM", 384) or 0)
        except (TypeError, ValueError):
            expected_dim = 0
        if expected_dim and len(vector) != expected_dim:
            logger.warning("compaction_embedding_dim_mismatch expected=%s got=%s", expected_dim, len(vector))
            return None
        return vector

    def _build_transcript(self, messages) -> str:
        max_chars = int(getattr(settings, "MCP_COMPACTION_TRANSCRIPT_MAX_CHARS", 12000) or 0)
        lines: list[str] = []
        total = 0
        for msg in messages:
            sender = str(getattr(msg, "sender", "") or "").strip().lower()
            role = "assistant" if sender == "ai" else "user"
            body = str(getattr(msg, "body", "") or "").strip()
            if not body:
                continue
            line = f"{role.upper()}: {body}"
            if max_chars and total + len(line) > max_chars:
                remaining = max_chars - total
                if remaining > 0:
                    lines.append(line[:remaining].rstrip())
                lines.append("... [truncated]")
                break
            lines.append(line)
            total += len(line) + 1
        return "\n".join(lines).strip()

    def _extract_critical_context(self, messages) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        text_chunks = []
        for msg in messages:
            body = str(getattr(msg, "body", "") or "").strip()
            if body:
                text_chunks.append(body)
        text = " ".join(text_chunks)[:20000]

        def _uniq(items: Iterable[str]) -> list[str]:
            seen = set()
            out: list[str] = []
            for item in items:
                if item in seen:
                    continue
                seen.add(item)
                out.append(item)
            return out

        amounts = _uniq(
            re.findall(r"(?:EGP|USD|EUR|GBP|AED)\s*\d+(?:,\d{3})*(?:\.\d{2})?", text, re.IGNORECASE)
        )
        percentages = _uniq(re.findall(r"\b\d+(?:\.\d+)?%\b", text))
        dates = _uniq(re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", text))
        emails = _uniq(re.findall(r"\b[\w.\-]+@[\w.\-]+\.\w+\b", text))
        uuids = _uniq(
            re.findall(
                r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
                text,
                re.IGNORECASE,
            )
        )

        decision_lines: list[str] = []
        for msg in messages:
            body = str(getattr(msg, "body", "") or "").strip()
            if not body:
                continue
            lowered = body.lower()
            if any(term in lowered for term in ("approved", "denied", "completed", "sent", "created", "scheduled")):
                decision_lines.append(self._clip_text(body, 200))

        facts = {
            "amounts": amounts[:20],
            "percentages": percentages[:20],
            "dates": dates[:20],
            "emails": emails[:20],
            "ids": uuids[:20],
        }
        decisions = {"events": _uniq(decision_lines)[:20]}
        return facts, decisions

    def _serialize_message(self, msg) -> dict[str, object]:
        payload = {
            "id": str(msg.id),
            "sender": str(getattr(msg, "sender", "") or ""),
            "body": str(getattr(msg, "body", "") or ""),
            "metadata": dict(getattr(msg, "metadata", {}) or {}),
            "content_blocks": list(getattr(msg, "content_blocks", []) or []),
            "sent_at": getattr(msg, "sent_at", None).isoformat() if getattr(msg, "sent_at", None) else None,
            "created_at": getattr(msg, "created_at", None).isoformat() if getattr(msg, "created_at", None) else None,
        }
        return payload

    def _message_position(self, conversation, msg) -> int:
        from apps.conversations.models import ConversationMessage

        before_count = (
            ConversationMessage.objects.filter(conversation=conversation)
            .filter(
                Q(sent_at__lt=msg.sent_at) | (Q(sent_at=msg.sent_at) & Q(created_at__lt=msg.created_at))
            )
            .count()
        )
        return before_count + 1

    def _estimate_tokens_for_messages(self, messages) -> int:
        total_chars = 0
        for msg in messages:
            total_chars += len(str(getattr(msg, "body", "") or ""))
            blocks = getattr(msg, "content_blocks", None)
            if blocks:
                try:
                    total_chars += len(json.dumps(blocks, ensure_ascii=False, default=str))
                except Exception:
                    total_chars += len(str(blocks))
            total_chars += 12
        padded = int(total_chars * 1.2)
        return (padded + 3) // 4 if padded else 0

    def _estimate_tokens_for_message(self, msg) -> int:
        total_chars = len(str(getattr(msg, "body", "") or ""))
        blocks = getattr(msg, "content_blocks", None)
        if blocks:
            try:
                total_chars += len(json.dumps(blocks, ensure_ascii=False, default=str))
            except Exception:
                total_chars += len(str(blocks))
        total_chars += 12
        padded = int(total_chars * 1.2)
        return (padded + 3) // 4 if padded else 0

    def _estimate_tokens_for_text(self, text: str) -> int:
        total_chars = len(text or "")
        padded = int(total_chars * 1.2)
        return (padded + 3) // 4 if padded else 0

    def _context_window_tokens(self) -> int:
        try:
            return int(getattr(settings, "MCP_CONTEXT_WINDOW_TOKENS", 200000) or 200000)
        except (TypeError, ValueError):
            return 200000

    def _trigger_ratio(self) -> float:
        try:
            return float(getattr(settings, "MCP_COMPACTION_TRIGGER_THRESHOLD", 0.70) or 0.70)
        except (TypeError, ValueError):
            return 0.70

    def _target_ratio(self) -> float:
        try:
            return float(getattr(settings, "MCP_COMPACTION_TARGET_THRESHOLD", 0.60) or 0.60)
        except (TypeError, ValueError):
            return 0.60

    def _preserve_last_n(self) -> int:
        try:
            value = int(getattr(settings, "MCP_COMPACTION_PRESERVE_LAST_MESSAGES", 15) or 15)
        except (TypeError, ValueError):
            value = 15
        return max(1, value)

    def _max_messages_per_segment(self) -> int:
        try:
            value = int(getattr(settings, "MCP_COMPACTION_MAX_MESSAGES_PER_SEGMENT", 120) or 120)
        except (TypeError, ValueError):
            value = 120
        return max(1, value)

    @staticmethod
    def _clip_text(value: str, limit: int) -> str:
        if limit <= 0:
            return value
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 1)].rstrip() + "…"
