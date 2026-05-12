from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import Iterable, Mapping

from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.accounts.models import BusinessProfile, TenantMemoryConfiguration
from apps.conversations.compaction_service import ContextCompactionService
from apps.conversations.models import CompactedHistorySegment, MemoryItem
from core.tenancy import tenant_context


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetentionPurgeResult:
    business_id: uuid.UUID
    max_retention_days: int | None
    skipped: bool
    skip_reason: str | None
    deleted_memory_items: int
    deleted_segments: int
    trimmed_segments: int
    updated_segments: int
    errors: tuple[str, ...]


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "y"}:
        return True
    if token in {"0", "false", "no", "n"}:
        return False
    return default


def _message_dt(payload: Mapping[str, object]) -> timezone.datetime | None:
    """Parse a message timestamp from serialized CompactedHistorySegment.full_messages items."""

    raw = payload.get("sent_at") or payload.get("created_at")
    if raw is None:
        return None
    dt = parse_datetime(str(raw))
    if dt is None:
        return None
    if timezone.is_naive(dt):
        try:
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        except Exception:
            return None
    return dt


def _purge_queryset_in_batches(qs, *, batch_size: int, dry_run: bool) -> int:
    """Delete rows in small batches to avoid long locks/transactions."""

    total = 0
    while True:
        ids = list(qs.values_list("id", flat=True).order_by("id")[:batch_size])
        if not ids:
            break
        total += len(ids)
        if not dry_run:
            qs.model.objects.filter(id__in=ids).delete()
    return total


class TenantRetentionPurgeService:
    """
    Phase 7: retention enforcement (delete derived memory beyond max retention).

    Scope: deletes MemoryItem + CompactedHistorySegment rows that are older than the
    tenant's maximum_retention_days. (ConversationMessage retention is intentionally out of scope.)
    """

    def __init__(self) -> None:
        self._compaction_service = ContextCompactionService()

    def purge_business(
        self,
        business: BusinessProfile,
        *,
        dry_run: bool = True,
        batch_size: int = 1000,
        now=None,
    ) -> RetentionPurgeResult:
        if now is None:
            now = timezone.now()

        config: TenantMemoryConfiguration | None
        try:
            config = business.memory_config
        except Exception:
            config = None

        max_retention_days: int | None = None
        min_retention_days: int = 0
        purge_enabled = True
        legal_hold = False

        if config:
            try:
                min_retention_days = int(config.minimum_retention_days or 0)
            except (TypeError, ValueError):
                min_retention_days = 0
            try:
                max_retention_days = int(config.maximum_retention_days) if config.maximum_retention_days is not None else None
            except (TypeError, ValueError):
                max_retention_days = None
            purge_enabled = _coerce_bool(getattr(config, "purge_enabled", True), True)
            legal_hold = _coerce_bool(getattr(config, "legal_hold", False), False)

        # No max retention configured -> nothing to purge.
        if max_retention_days is None:
            return RetentionPurgeResult(
                business_id=business.id,
                max_retention_days=None,
                skipped=True,
                skip_reason="no_maximum_retention_days",
                deleted_memory_items=0,
                deleted_segments=0,
                trimmed_segments=0,
                updated_segments=0,
                errors=tuple(),
            )

        if not purge_enabled:
            return RetentionPurgeResult(
                business_id=business.id,
                max_retention_days=max_retention_days,
                skipped=True,
                skip_reason="purge_disabled",
                deleted_memory_items=0,
                deleted_segments=0,
                trimmed_segments=0,
                updated_segments=0,
                errors=tuple(),
            )

        if legal_hold:
            return RetentionPurgeResult(
                business_id=business.id,
                max_retention_days=max_retention_days,
                skipped=True,
                skip_reason="legal_hold",
                deleted_memory_items=0,
                deleted_segments=0,
                trimmed_segments=0,
                updated_segments=0,
                errors=tuple(),
            )

        errors: list[str] = []
        if max_retention_days < 0:
            return RetentionPurgeResult(
                business_id=business.id,
                max_retention_days=max_retention_days,
                skipped=True,
                skip_reason="invalid_maximum_retention_days",
                deleted_memory_items=0,
                deleted_segments=0,
                trimmed_segments=0,
                updated_segments=0,
                errors=("maximum_retention_days must be >= 0",),
            )

        if min_retention_days < 0:
            min_retention_days = 0

        if max_retention_days < min_retention_days:
            return RetentionPurgeResult(
                business_id=business.id,
                max_retention_days=max_retention_days,
                skipped=True,
                skip_reason="invalid_retention_window",
                deleted_memory_items=0,
                deleted_segments=0,
                trimmed_segments=0,
                updated_segments=0,
                errors=(
                    f"maximum_retention_days ({max_retention_days}) is less than minimum_retention_days ({min_retention_days})",
                ),
            )

        cutoff = now - timedelta(days=max_retention_days)

        deleted_memory_items = 0
        deleted_segments = 0
        trimmed_segments = 0
        updated_segments = 0

        with tenant_context(business.id):
            # ------------------------------------------------------------------
            # Purge run memory items
            # ------------------------------------------------------------------
            mem_qs = MemoryItem.objects.filter(
                business_profile_id=business.id,
                created_at__lt=cutoff,
            )
            deleted_memory_items = _purge_queryset_in_batches(mem_qs, batch_size=batch_size, dry_run=dry_run)

            # ------------------------------------------------------------------
            # Purge compacted segments fully outside retention window
            # ------------------------------------------------------------------
            expired_segments = CompactedHistorySegment.objects.filter(conversation__business_profile_id=business.id)
            expired_segments = expired_segments.filter(
                end_message_sent_at__isnull=False,
                end_message_sent_at__lt=cutoff,
            )
            deleted_segments = _purge_queryset_in_batches(expired_segments, batch_size=batch_size, dry_run=dry_run)

            # ------------------------------------------------------------------
            # Trim segments that straddle the retention boundary
            # ------------------------------------------------------------------
            overlap_qs = CompactedHistorySegment.objects.filter(conversation__business_profile_id=business.id)
            overlap_qs = overlap_qs.filter(
                start_message_sent_at__isnull=False,
                end_message_sent_at__isnull=False,
                start_message_sent_at__lt=cutoff,
                end_message_sent_at__gte=cutoff,
            )

            for segment in overlap_qs.iterator(chunk_size=200):
                if not isinstance(segment.full_messages, list):
                    continue

                original_messages = [msg for msg in segment.full_messages if isinstance(msg, Mapping)]
                kept_messages: list[dict[str, object]] = []
                for msg in original_messages:
                    dt = _message_dt(msg)
                    # If we can't parse a message timestamp, drop it to stay compliant.
                    if dt is None:
                        continue
                    if dt >= cutoff:
                        kept_messages.append(dict(msg))

                if not kept_messages:
                    trimmed_segments += 1
                    deleted_segments += 1
                    if not dry_run:
                        segment.delete()
                    continue

                if len(kept_messages) == len(original_messages):
                    # Nothing to trim (should be rare if start/end sent_at are correct).
                    continue

                trimmed_segments += 1

                # Rebuild summary/facts/embedding from the retained slice only.
                fake_msgs = [
                    SimpleNamespace(sender=msg.get("sender"), body=str(msg.get("body") or ""))
                    for msg in kept_messages
                ]

                # Deterministic summary: we avoid LLM calls in purge jobs.
                try:
                    transcript = self._compaction_service._build_transcript(fake_msgs)
                    max_chars = int(getattr(settings, "MCP_COMPACTION_SUMMARY_MAX_CHARS", 4000) or 4000)
                    summary_text = self._compaction_service._clip_text(transcript, max_chars)
                except Exception:
                    summary_text = ""

                try:
                    extracted_facts, extracted_decisions = self._compaction_service._extract_critical_context(fake_msgs)
                except Exception:
                    extracted_facts, extracted_decisions = {}, {}

                try:
                    embedding_vector = self._compaction_service._embed_segment(summary_text, extracted_facts, extracted_decisions)
                except Exception:
                    embedding_vector = None

                try:
                    token_count_original = self._compaction_service._estimate_tokens_for_messages(fake_msgs)
                    token_count_summary = self._compaction_service._estimate_tokens_for_text(summary_text)
                    compression_ratio = (
                        float(token_count_summary) / float(token_count_original) if token_count_original else 0.0
                    )
                except Exception:
                    token_count_original = int(segment.token_count_original or 0)
                    token_count_summary = int(segment.token_count_summary or 0)
                    compression_ratio = float(segment.compression_ratio or 0.0)

                # Update segment bounds to match the retained slice (end bound stays the same
                # to preserve the "last compacted message id" invariant used by the compactor).
                start_id_raw = str(kept_messages[0].get("id") or "").strip()
                start_sent_at = _message_dt(kept_messages[0])
                try:
                    start_id = uuid.UUID(start_id_raw) if start_id_raw else segment.start_message_id
                except (TypeError, ValueError):
                    start_id = segment.start_message_id

                if not dry_run:
                    segment.full_messages = kept_messages
                    segment.summary = summary_text
                    segment.extracted_facts = extracted_facts
                    segment.extracted_decisions = extracted_decisions
                    segment.embedding = embedding_vector
                    segment.start_message_id = start_id
                    segment.start_message_sent_at = start_sent_at
                    segment.token_count_original = token_count_original
                    segment.token_count_summary = token_count_summary
                    segment.compression_ratio = compression_ratio
                    try:
                        segment.save(
                            update_fields=[
                                "full_messages",
                                "summary",
                                "extracted_facts",
                                "extracted_decisions",
                                "embedding",
                                "start_message_id",
                                "start_message_sent_at",
                                "token_count_original",
                                "token_count_summary",
                                "compression_ratio",
                            ]
                        )
                        updated_segments += 1
                    except IntegrityError:
                        # If trimming causes a duplicate bounds conflict, drop the redundant row.
                        errors.append(
                            f"segment_trim_conflict segment={segment.id} start={start_id} end={segment.end_message_id}"
                        )
                        segment.delete()
                        deleted_segments += 1
                    except Exception as exc:
                        errors.append(f"segment_trim_failed segment={segment.id} error={exc}")

        return RetentionPurgeResult(
            business_id=business.id,
            max_retention_days=max_retention_days,
            skipped=False,
            skip_reason=None,
            deleted_memory_items=deleted_memory_items,
            deleted_segments=deleted_segments,
            trimmed_segments=trimmed_segments,
            updated_segments=updated_segments,
            errors=tuple(errors),
        )
