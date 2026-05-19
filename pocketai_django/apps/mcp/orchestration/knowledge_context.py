from __future__ import annotations

import json
import logging
import uuid
from typing import Mapping, MutableMapping, Sequence

from django.utils import timezone

from apps.conversations.models import Conversation
from apps.rag.rag_logging import structured_log

from ..types import ToolExecutionContext


logger = logging.getLogger(__name__)


class McpKnowledgeContextMixin:


    @staticmethod
    def _search_result_is_table(tool_result: Mapping[str, object]) -> bool:
        snippets = tool_result.get("snippets")
        if not isinstance(snippets, list) or not snippets:
            results = tool_result.get("refs")
            if not isinstance(results, list) or not results:
                results = tool_result.get("results")
            if not isinstance(results, list) or not results:
                return False
            table_results = 0
            non_table_results = 0
            for result in results:
                if not isinstance(result, Mapping):
                    continue
                if str(result.get("type") or "").strip().lower() == "table":
                    table_results += 1
                else:
                    non_table_results += 1
            return bool(table_results and non_table_results == 0)
        table_snippets = 0
        non_table_snippets = 0
        for snippet in snippets:
            if not isinstance(snippet, Mapping):
                continue
            if snippet.get("is_table_chunk"):
                table_snippets += 1
            else:
                non_table_snippets += 1
        return bool(table_snippets and non_table_snippets == 0)

    @staticmethod
    def _record_knowledge_outputs(context: ToolExecutionContext, tool_result: Mapping[str, object]) -> None:
        tool_name = str(tool_result.get("tool") or "").strip()

        if tool_name == "search_knowledge":
            refs_raw = tool_result.get("refs")
            if not isinstance(refs_raw, list):
                refs_raw = tool_result.get("results")
            if not isinstance(refs_raw, list):
                refs_raw = tool_result.get("snippets")
            if isinstance(refs_raw, list):
                refs = [ref for ref in refs_raw if isinstance(ref, Mapping)]
                context.set_recent_search_refs(refs)
                for ref in refs:
                    context.add_model_visible_ref(ref)
            return

        if tool_name != "read_knowledge":
            return

        evidence_raw = tool_result.get("evidence")
        if not isinstance(evidence_raw, list):
            evidence_raw = tool_result.get("contents")
        if not isinstance(evidence_raw, list):
            return

        # Canonical evidence items (agentic v2).
        for item in evidence_raw[:50]:
            if not isinstance(item, Mapping):
                continue
            context.add_read_evidence(item)
            content_id = item.get("id")
            title = item.get("title") or item.get("label") or "Knowledge"
            content_type = str(item.get("type") or "").strip().lower()
            truncated = bool(item.get("truncated"))
            upload_id = item.get("document_id") if item.get("document_id") not in {None, ""} else None
            coverage_entry = {
                "id": content_id,
                "title": title,
                "label": title,
                "read_state": "partial" if truncated else "full",
                "coverage": (),
                "search_stage": "read_knowledge",
                "chunk_id": content_id,
                "upload_id": upload_id,
                "page_mode": None,
                "is_table_chunk": content_type == "table",
                "suppress_in_prompt": False,
            }
            context.add_coverage_entry(coverage_entry)
    @staticmethod
    def _suppress_table_previews(context: ToolExecutionContext, upload_id: str) -> None:
        if not upload_id:
            return
        for entry in context.knowledge_results:
            if not isinstance(entry, Mapping):
                continue
            if str(entry.get("upload_id") or "").strip() != upload_id:
                continue
            if entry.get("page_mode") == "structured_table":
                continue
            if entry.get("is_table_chunk"):
                entry["suppress_in_prompt"] = True

    @staticmethod
    def _mark_upload_as_satisfied(context: ToolExecutionContext, upload_id: str) -> None:
        if not upload_id:
            return
        normalized_id = str(upload_id).strip()
        if not normalized_id:
            return
        for entry in context.knowledge_results:
            if not isinstance(entry, MutableMapping):
                continue
            if str(entry.get("upload_id") or "").strip() != normalized_id:
                continue
            if entry.get("read_required"):
                entry["read_required"] = False
            entry.setdefault("read_state", "full")
        for coverage in context.coverage_ledger:
            if not isinstance(coverage, MutableMapping):
                continue
            if str(coverage.get("upload_id") or "").strip() != normalized_id:
                continue
            if coverage.get("read_required"):
                coverage["read_required"] = False

    @staticmethod
    def _satisfy_transcript_snippets(
        transcript: list[MutableMapping[str, object]],
        upload_id: str,
        context: ToolExecutionContext,
    ) -> None:
        normalized_id = str(upload_id or "").strip()
        if not normalized_id:
            return
        for entry in transcript:
            if entry.get("role") != "tool":
                continue
            if entry.get("name") != "search_knowledge":
                continue
            content_raw = entry.get("content")
            if not isinstance(content_raw, str):
                continue
            try:
                payload = json.loads(content_raw)
            except json.JSONDecodeError:
                continue
            snippets = payload.get("snippets")
            if not isinstance(snippets, list):
                continue
            updated = False
            for snippet in snippets:
                if not isinstance(snippet, MutableMapping):
                    continue
                snippet_upload = str(snippet.get("upload_id") or "").strip()
                if snippet_upload != normalized_id:
                    continue
                if snippet.get("read_required"):
                    snippet["read_required"] = False
                    updated = True
                snippet.setdefault("read_state", "full")
            if updated:
                entry["content"] = json.dumps(payload, ensure_ascii=False)
        for coverage in context.coverage_ledger:
            if str(coverage.get("upload_id") or "").strip() != upload_id:
                continue
            if coverage.get("page_mode") == "structured_table":
                continue
            if coverage.get("is_table_chunk"):
                coverage["suppress_in_prompt"] = True

    @staticmethod
    def _normalized_column_entries(columns: Sequence[object] | object) -> list[str]:
        normalized: list[str] = []
        if isinstance(columns, str):
            iterable: Sequence[object] = [columns]
        elif isinstance(columns, Sequence):
            iterable = columns
        else:
            return normalized
        for entry in iterable:
            if entry is None:
                continue
            text = str(entry).strip()
            if not text:
                continue
            normalized.append(text)
        return normalized

    def _hydrate_seen_items(self, conversation: Conversation, context: ToolExecutionContext) -> None:
        """Load previously-shown chunk/row IDs from conversation metadata.

        This enables "are there more?" follow-up queries by tracking what has already
        been shown to the user, allowing the system to return NEW items on subsequent queries.
        """
        metadata = conversation.metadata if isinstance(conversation.metadata, Mapping) else {}

        # Hydrate seen items (existing behavior)
        seen_data = metadata.get("mcp_seen_items")
        if isinstance(seen_data, Mapping):
            chunk_ids = seen_data.get("chunk_ids")
            if isinstance(chunk_ids, list):
                context.seen_chunk_ids = {str(cid) for cid in chunk_ids if cid}

            row_ids = seen_data.get("row_ids")
            if isinstance(row_ids, list):
                context.seen_row_ids = {str(rid) for rid in row_ids if rid}

        # Hydrate recent search refs (cross-turn read continuity)
        recent_refs_data = metadata.get("mcp_recent_search_refs")
        if isinstance(recent_refs_data, (Mapping, list)):
            context.hydrate_recent_search_refs(recent_refs_data)

        if (
            context.seen_chunk_ids
            or context.seen_row_ids
            or context.recent_search_refs
        ):
            structured_log(
                "mcp",
                "cache.seen_items_hydrate",
                {
                    "seen_chunks": len(context.seen_chunk_ids),
                    "seen_rows": len(context.seen_row_ids),
                    "recent_search_refs": len(context.recent_search_refs),
                },
                indent=1,
                context={
                    "conversation": conversation.id,
                    "business": conversation.business_profile_id,
                },
                logger_obj=logger,
            )

    def _persist_seen_items(self, conversation: Conversation, context: ToolExecutionContext) -> None:
        """Save newly-shown chunk/row IDs to conversation metadata.

        Combines items from previous turns with items shown this turn, capped
        to prevent unbounded growth.
        """
        MAX_SEEN_ITEMS = 200  # Cap to prevent metadata bloat

        all_shown = context.get_all_shown_this_conversation()
        new_chunk_ids = all_shown.get("chunk_ids", set())
        new_row_ids = all_shown.get("row_ids", set())

        has_seen_items = context.newly_shown_chunk_ids or context.newly_shown_row_ids
        has_document_context = False
        has_recent_search_refs = bool(context.recent_search_refs_updated)

        if not has_seen_items and not has_document_context and not has_recent_search_refs:
            return

        metadata = conversation.metadata if isinstance(conversation.metadata, Mapping) else {}
        new_metadata = dict(metadata)

        # Persist seen items (existing behavior)
        if has_seen_items:
            # Cap the lists to prevent unbounded growth (keep most recent)
            chunk_list = list(new_chunk_ids)[-MAX_SEEN_ITEMS:]
            row_list = list(new_row_ids)[-MAX_SEEN_ITEMS:]

            new_metadata["mcp_seen_items"] = {
                "chunk_ids": chunk_list,
                "row_ids": row_list,
                "updated_at": timezone.now().isoformat(),
            }

        new_metadata.pop("mcp_document_context", None)

        # Persist cross-turn recent refs used by read_knowledge follow-ups.
        if has_recent_search_refs:
            if context.recent_search_refs:
                refs_payload = context.get_recent_search_refs_for_persistence()
                refs_payload["updated_at"] = timezone.now().isoformat()
                new_metadata["mcp_recent_search_refs"] = refs_payload
            else:
                new_metadata.pop("mcp_recent_search_refs", None)

        conversation.metadata = new_metadata
        conversation.save(update_fields=["metadata"])

        structured_log(
            "mcp",
            "cache.seen_items_persist",
            {
                "newly_shown_chunks": len(context.newly_shown_chunk_ids),
                "newly_shown_rows": len(context.newly_shown_row_ids),
                "total_chunks": len(list(new_chunk_ids)[-MAX_SEEN_ITEMS:]) if has_seen_items else 0,
                "total_rows": len(list(new_row_ids)[-MAX_SEEN_ITEMS:]) if has_seen_items else 0,
                "recent_search_refs": len(context.recent_search_refs) if has_recent_search_refs else 0,
            },
            indent=1,
            context={
                "conversation": conversation.id,
                "business": conversation.business_profile_id,
            },
            logger_obj=logger,
        )

    @staticmethod
    def _is_uuid_like(value: object) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        try:
            uuid.UUID(text)
            return True
        except (TypeError, ValueError):
            return False

    def _repair_read_knowledge_refs_from_context(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> dict[str, object]:
        """
        Repair read_knowledge refs when the model supplies invented non-UUID IDs.

        We only repair when unambiguous (single recent ref, or all recent refs
        point to one document), to avoid silently changing user intent.
        """

        repaired = dict(arguments or {})
        raw_refs = repaired.get("refs")
        if not isinstance(raw_refs, list):
            legacy_items = repaired.get("items")
            if isinstance(legacy_items, list):
                raw_refs = legacy_items
                repaired["refs"] = legacy_items
                repaired.pop("items", None)
        if not isinstance(raw_refs, list) or not raw_refs:
            return repaired

        invalid_positions: list[int] = []
        for idx, ref in enumerate(raw_refs):
            if not isinstance(ref, Mapping):
                continue
            ref_id = str(ref.get("id") or ref.get("ref") or "").strip()
            if not self._is_uuid_like(ref_id):
                invalid_positions.append(idx)
        if not invalid_positions:
            return repaired

        recent_refs = [
            ref
            for ref in (getattr(context, "recent_search_refs", None) or [])
            if isinstance(ref, Mapping) and self._is_uuid_like(ref.get("id"))
        ]
        if not recent_refs:
            return repaired

        replacement_id = ""
        if len(recent_refs) == 1:
            replacement_id = str(recent_refs[0].get("id") or "").strip()
        else:
            document_ids = {
                str(ref.get("document_id") or ref.get("document") or "").strip()
                for ref in recent_refs
                if str(ref.get("document_id") or ref.get("document") or "").strip()
            }
            if len(document_ids) == 1:
                replacement_id = str(recent_refs[0].get("id") or "").strip()

        if not replacement_id:
            return repaired

        refs_out: list[object] = []
        for idx, ref in enumerate(raw_refs):
            if not isinstance(ref, Mapping):
                refs_out.append(ref)
                continue
            item = dict(ref)
            if idx in invalid_positions:
                item["id"] = replacement_id
                item.pop("ref", None)
            refs_out.append(item)
        repaired["refs"] = refs_out
        repaired.pop("items", None)
        return repaired

    @staticmethod
    def _search_budget_remaining(context: ToolExecutionContext | None) -> int | None:
        """
        Return remaining search_knowledge calls for this turn.

        None means "unlimited" (budget disabled). 0 means exhausted.
        """

        if context is None:
            return None
        try:
            limit = int(getattr(context, "_effective_max_searches"))
        except Exception:
            limit = int(getattr(context, "max_searches_per_turn", 0) or 0)
        if limit <= 0:
            return None
        used = int(getattr(context, "searches_used", 0) or 0)
        return max(0, limit - used)
