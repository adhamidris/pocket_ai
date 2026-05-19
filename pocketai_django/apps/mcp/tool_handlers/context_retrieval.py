"""
Long-chat context retrieval MCP tool handler.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import timedelta
from typing import Mapping

from django.conf import settings

from apps.conversations.models import Conversation
from apps.rag.rag_logging import structured_log

from ..types import ToolExecutionContext


logger = logging.getLogger(__name__)


def _portal_file_embedding_service():
    if not bool(getattr(settings, "RAG_PORTAL_FILE_SEARCH_ENABLED", True)):
        return None
    try:
        from apps.rag.embeddings import build_embedding_service
    except Exception:
        return None
    return build_embedding_service()


def _retrieve_earlier_context_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext | None = None,
) -> Mapping[str, object]:
    del context
    start = time.perf_counter()

    query = str(arguments.get("query") or "").strip()
    segment_id_raw = str(arguments.get("segment_id") or "").strip()
    timeframe = str(arguments.get("timeframe") or "all").strip().lower()
    include_full_segment = bool(arguments.get("include_full_segment"))
    try:
        max_messages = int(arguments.get("max_messages") or (50 if include_full_segment else 12))
    except (TypeError, ValueError):
        max_messages = 50 if include_full_segment else 12
    max_messages = max(1, min(50, max_messages))

    def _log_performance(
        payload: Mapping[str, object],
        *,
        segments_available: int | None = None,
        semantic_enabled: bool | None = None,
        embeddings_backfilled: int | None = None,
        embeddings_backfill_attempted: int | None = None,
        ranked_candidates: int | None = None,
    ) -> None:
        duration_ms = int((time.perf_counter() - start) * 1000.0)
        warn_ms = int(getattr(settings, "MCP_SLO_RETRIEVE_EARLIER_CONTEXT_WARN_MS", 1200) or 0)
        slow = bool(warn_ms and duration_ms >= warn_ms)
        status_value = str(payload.get("status") or "").strip().lower() or "ok"
        detail: dict[str, object] = {
            "status": payload.get("status"),
            "found": payload.get("found"),
            "match_method": payload.get("match_method"),
            "segment_id": payload.get("segment_id"),
            "vector_distance": payload.get("vector_distance"),
            "duration_ms": duration_ms,
            "timeframe": timeframe,
            "include_full_segment": include_full_segment,
            "max_messages": max_messages,
            "query_chars": len(query),
            "segment_id_provided": bool(segment_id_raw),
            "segments_available": segments_available,
            "semantic_enabled": semantic_enabled,
            "ranked_candidates": ranked_candidates,
            "embeddings_backfilled": embeddings_backfilled,
            "embeddings_backfill_attempted": embeddings_backfill_attempted,
        }
        try:
            messages = payload.get("messages")
            if isinstance(messages, (list, tuple)):
                detail["messages_returned"] = len(messages)
        except Exception:
            pass
        if slow:
            detail["slo"] = "slow"
            detail["slo_warn_ms"] = warn_ms
        structured_log(
            "mcp",
            "retrieve_earlier_context.performance",
            detail,
            context={
                "business": conversation.business_profile_id,
                "conversation": conversation.id,
            },
            level=logging.WARNING if slow or status_value in {"error"} else logging.INFO,
        )

    if segment_id_raw:
        try:
            seg_uuid = uuid.UUID(segment_id_raw)
        except (TypeError, ValueError):
            payload = {
                "tool": "retrieve_earlier_context",
                "status": "error",
                "error": "invalid_segment_id",
                "hint": "segment_id must be a valid UUID.",
            }
            _log_performance(payload, semantic_enabled=False)
            return payload
        segment = conversation.compacted_segments.filter(id=seg_uuid).first()
        if not segment:
            payload = {
                "tool": "retrieve_earlier_context",
                "status": "ok",
                "found": False,
                "message": "Compacted segment not found for this conversation.",
                "segment_id": segment_id_raw,
            }
            _log_performance(payload, semantic_enabled=False)
            return payload

        # Enforce tenant maximum retention even for direct segment fetches.
        business_profile = getattr(conversation, "business_profile", None)
        max_retention_days = None
        if business_profile is not None:
            try:
                config = business_profile.memory_config
            except Exception:
                config = None
            if config and config.maximum_retention_days is not None:
                try:
                    max_retention_days = int(config.maximum_retention_days)
                except (TypeError, ValueError):
                    max_retention_days = None
        if max_retention_days and max_retention_days > 0:
            from django.utils import timezone as django_timezone

            cutoff = django_timezone.now() - timedelta(days=max_retention_days)
            segment_end = getattr(segment, "end_message_sent_at", None) or getattr(segment, "compacted_at", None)
            if segment_end is not None and segment_end < cutoff:
                payload = {
                    "tool": "retrieve_earlier_context",
                    "status": "ok",
                    "found": False,
                    "message": "Compacted segment is outside this tenant's retention window.",
                    "segment_id": segment_id_raw,
                }
                _log_performance(payload, semantic_enabled=False)
                return payload

        messages_out = list(segment.full_messages or [])
        if len(messages_out) > max_messages:
            messages_out = messages_out[:max_messages]
        payload = {
            "tool": "retrieve_earlier_context",
            "status": "ok",
            "found": True,
            "match_method": "direct",
            "segment_id": str(segment.id),
            "segment": str(segment.segment_range or ""),
            "summary": str(segment.summary or ""),
            "facts": segment.extracted_facts if isinstance(segment.extracted_facts, Mapping) else {},
            "decisions": segment.extracted_decisions if isinstance(segment.extracted_decisions, Mapping) else {},
            "messages": messages_out if include_full_segment else [],
        }
        _log_performance(payload, semantic_enabled=False)
        return payload

    if not query:
        payload = {
            "tool": "retrieve_earlier_context",
            "status": "error",
            "error": "missing_query",
            "hint": "Provide a query string (or segment_id) to search compacted history.",
        }
        _log_performance(payload, semantic_enabled=False)
        return payload

    business_profile = getattr(conversation, "business_profile", None)
    max_retention_days = None
    if business_profile is not None:
        try:
            config = business_profile.memory_config
        except Exception:
            config = None
        if config and config.maximum_retention_days is not None:
            try:
                max_retention_days = int(config.maximum_retention_days)
            except (TypeError, ValueError):
                max_retention_days = None

    base_qs = conversation.compacted_segments.all()
    if max_retention_days and max_retention_days > 0:
        from django.db.models import Q as DjangoQ
        from django.utils import timezone as django_timezone

        cutoff = django_timezone.now() - timedelta(days=max_retention_days)
        # Retention is based on the underlying message timestamps, not the compaction timestamp.
        # Fallback to compacted_at for legacy rows that haven't been backfilled yet.
        base_qs = base_qs.filter(
            DjangoQ(end_message_sent_at__gte=cutoff)
            | DjangoQ(end_message_sent_at__isnull=True, compacted_at__gte=cutoff)
        )

    # Apply coarse timeframe narrowing (fine-grained turn-range filtering happens after ranking).
    if timeframe in {"recent"}:
        base_qs = base_qs.order_by("-end_message_sent_at", "-compacted_at")[:15]
    elif timeframe in {"oldest"}:
        base_qs = base_qs.order_by("end_message_sent_at", "compacted_at")[:15]

    segments = list(base_qs)
    if not segments:
        payload = {
            "tool": "retrieve_earlier_context",
            "status": "ok",
            "found": False,
            "message": "No compacted history segments are available yet.",
            "query": query,
        }
        _log_performance(payload, segments_available=0, semantic_enabled=False)
        return payload

    def _parse_range(label: str) -> tuple[int, int] | None:
        match = re.match(r"^turns_(\\d+)_to_(\\d+)$", str(label or "").strip().lower())
        if not match:
            return None
        try:
            start = int(match.group(1))
            end = int(match.group(2))
        except (TypeError, ValueError):
            return None
        if start <= 0 or end <= 0:
            return None
        if end < start:
            start, end = end, start
        return (start, end)

    def _overlaps_turn_range(segment_range: str, start: int, end: int) -> bool:
        parsed = _parse_range(segment_range)
        if not parsed:
            return False
        seg_start, seg_end = parsed
        return not (seg_end < start or seg_start > end)

    desired_turn_range = None
    if timeframe == "first_10_turns":
        desired_turn_range = (1, 10)
    elif timeframe == "turns_10_to_20":
        desired_turn_range = (10, 20)
    if desired_turn_range:
        start, end = desired_turn_range
        range_filtered = [s for s in segments if _overlaps_turn_range(str(s.segment_range or ""), start, end)]
        if range_filtered:
            segments = range_filtered

    query_tokens = {token for token in re.split(r"\\W+", query.lower()) if token}

    def _score_text(text: str) -> int:
        if not text or not query_tokens:
            return 0
        lowered = text.lower()
        score = 0
        for token in query_tokens:
            if token and token in lowered:
                score += 1
        return score

    best_segment = None
    match_method = "lexical"
    vector_distance = None

    embedder = _portal_file_embedding_service()
    query_vector: list[float] | None = None
    if embedder:
        try:
            query_vector = embedder.embed_text(query)
        except Exception:
            query_vector = None

    semantic_enabled = bool(query_vector)
    embeddings_backfill_attempted = 0
    embeddings_backfilled = 0
    ranked_candidates = None

    if query_vector:
        # Opportunistic backfill: ensure recent segments have embeddings.
        expected_dim = int(getattr(settings, "EMBED_DIM", 384) or 384)
        missing = [s for s in segments if getattr(s, "embedding", None) is None and str(getattr(s, "summary", "") or "").strip()]
        for seg in missing[:10]:
            embeddings_backfill_attempted += 1
            try:
                vec = embedder.embed_text(str(seg.summary or "").strip())
            except Exception:
                continue
            if vec and len(vec) == expected_dim:
                try:
                    updated = seg.__class__.objects.filter(id=seg.id).update(embedding=vec)
                    if updated:
                        embeddings_backfilled += 1
                except Exception:
                    continue

        try:
            from pgvector.django import CosineDistance
        except Exception:
            query_vector = None
        else:
            ann_limit = max(20, min(80, len(segments) * 5))
            seg_ids = [s.id for s in segments]
            ranked = (
                conversation.compacted_segments.filter(id__in=seg_ids)
                .exclude(embedding__isnull=True)
                .annotate(distance=CosineDistance("embedding", query_vector))
                .order_by("distance", "id")[:ann_limit]
            )
            ranked_list = list(ranked)
            ranked_candidates = len(ranked_list)
            if ranked_list:
                best_segment = ranked_list[0]
                match_method = "semantic"
                try:
                    vector_distance = float(getattr(best_segment, "distance", None) or 0.0)
                except (TypeError, ValueError):
                    vector_distance = None

    if best_segment is None:
        best_score = 0
        for segment in segments:
            score = _score_text(str(segment.summary or ""))
            if isinstance(segment.extracted_facts, Mapping):
                score += _score_text(json.dumps(segment.extracted_facts, ensure_ascii=False))
            if isinstance(segment.extracted_decisions, Mapping):
                score += _score_text(json.dumps(segment.extracted_decisions, ensure_ascii=False))
            if score > best_score:
                best_score = score
                best_segment = segment
        if not best_segment or best_score == 0:
            payload = {
                "tool": "retrieve_earlier_context",
                "status": "ok",
                "found": False,
                "message": "No matching compacted history found for that query.",
                "query": query,
            }
            _log_performance(
                payload,
                segments_available=len(segments),
                semantic_enabled=semantic_enabled,
                embeddings_backfilled=embeddings_backfilled,
                embeddings_backfill_attempted=embeddings_backfill_attempted,
                ranked_candidates=ranked_candidates,
            )
            return payload

    # Message selection
    if include_full_segment:
        messages_out = list(best_segment.full_messages or [])
        if len(messages_out) > max_messages:
            messages_out = messages_out[:max_messages]
    else:
        matched_messages: list[dict[str, object]] = []
        for msg in best_segment.full_messages or []:
            if not isinstance(msg, Mapping):
                continue
            body = str(msg.get("body") or "")
            meta = msg.get("metadata") if isinstance(msg.get("metadata"), Mapping) else {}
            block_text = ""
            blocks = msg.get("content_blocks")
            if isinstance(blocks, list):
                block_text = json.dumps(blocks, ensure_ascii=False)
            if _score_text(body) > 0 or _score_text(block_text) > 0 or _score_text(json.dumps(meta, ensure_ascii=False)) > 0:
                matched_messages.append(
                    {
                        "id": str(msg.get("id") or ""),
                        "sender": msg.get("sender"),
                        "body": body[:2400],
                        "metadata": meta,
                        "sent_at": msg.get("sent_at"),
                    }
                )
            if len(matched_messages) >= max_messages:
                break
        messages_out = matched_messages

    payload = {
        "tool": "retrieve_earlier_context",
        "status": "ok",
        "found": True,
        "match_method": match_method,
        "query": query,
        "segment_id": str(best_segment.id),
        "segment": str(best_segment.segment_range or ""),
        "summary": str(best_segment.summary or ""),
        "vector_distance": vector_distance,
        "facts": best_segment.extracted_facts if isinstance(best_segment.extracted_facts, Mapping) else {},
        "decisions": best_segment.extracted_decisions if isinstance(best_segment.extracted_decisions, Mapping) else {},
        "messages": messages_out,
    }
    _log_performance(
        payload,
        segments_available=len(segments),
        semantic_enabled=semantic_enabled,
        embeddings_backfilled=embeddings_backfilled,
        embeddings_backfill_attempted=embeddings_backfill_attempted,
        ranked_candidates=ranked_candidates,
    )
    return payload
