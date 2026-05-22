from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from apps.conversations.models import Conversation
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadTable,
    KnowledgeUploadTableRow,
)

from ..types import ToolExecutionContext
from .artifacts.segments import _read_artifact_segment as _read_artifact_segment_impl
from .cursors import (
    _cursor_payload_base as _cursor_payload_base_for_conversation,
    _decode_cursor as _decode_cursor_for_conversation,
    _resolve_cursor_from_handle as _resolve_cursor_from_context,
    _store_cursor_handle as _store_cursor_handle_for_context,
)
from .segments.chunk_windows import _read_chunk_window_segment as _read_chunk_window_segment_impl
from .segments.section_blocks import (
    _load_ordered_page_blocks as _load_ordered_page_blocks_with_cache,
    _resolve_text_section_span as _resolve_text_section_span_with_cache,
)
from .table_reading.anchor_manifest import _load_table_anchor_manifest as _load_table_anchor_manifest_for_context
from .table_reading.anchors import _read_tabular_rows_with_anchor as _read_tabular_rows_with_anchor_impl
from .target_resolution import (
    _enforce_access as _enforce_access_impl,
    _resolve_target as _resolve_target_impl,
)
from .segments.text import (
    _read_page_blocks_segment as _read_page_blocks_segment_impl,
    _read_section_span_segment as _read_section_span_segment_impl,
)


@dataclass
class KnowledgeReadRuntimeContext:
    conversation: Conversation
    context: ToolExecutionContext
    upload_block_cache: dict[str, list[dict[str, object]]] = field(default_factory=dict)

    def load_ordered_page_blocks(self, upload_id: str) -> list[dict[str, object]]:
        return _load_ordered_page_blocks_with_cache(
            upload_id,
            upload_block_cache=self.upload_block_cache,
        )

    def resolve_text_section_span(
        self,
        *,
        upload_id: str,
        chunk_record: KnowledgeUploadChunk | None,
        chunk_meta: Mapping[str, object],
        fallback_page_number: int,
    ) -> dict[str, int] | None:
        return _resolve_text_section_span_with_cache(
            upload_id=upload_id,
            upload_block_cache=self.upload_block_cache,
            chunk_record=chunk_record,
            chunk_meta=chunk_meta,
            fallback_page_number=fallback_page_number,
        )

    def cursor_payload_base(self, *, item_id: str, kind: str) -> dict[str, object]:
        return _cursor_payload_base_for_conversation(
            conversation=self.conversation,
            item_id=item_id,
            kind=kind,
        )

    def decode_cursor(
        self,
        item_id: str,
        cursor: str,
    ) -> tuple[dict[str, object] | None, dict[str, object] | None]:
        return _decode_cursor_for_conversation(self.conversation, item_id, cursor)

    def resolve_cursor_from_handle(self, cursor_token: str | None) -> str | None:
        return _resolve_cursor_from_context(self.context, cursor_token)

    def store_cursor_handle(self, cursor_signed: str | None) -> str | None:
        return _store_cursor_handle_for_context(self.context, cursor_signed)

    def load_table_anchor_manifest(
        self,
        *,
        ref_id: str,
        table_id: str | None = None,
    ) -> dict[str, object] | None:
        return _load_table_anchor_manifest_for_context(self.context, ref_id=ref_id, table_id=table_id)

    def read_page_blocks_segment(
        self,
        *,
        item_id: str,
        upload: KnowledgeUpload,
        upload_id: str,
        page_number: int,
        start_order: int,
        start_offset: int,
        budget_chars: int,
        prepend_sep: bool = False,
    ) -> tuple[str, dict[str, object] | None, bool]:
        return _read_page_blocks_segment_impl(
            item_id=item_id,
            cursor_payload_base=self.cursor_payload_base,
            upload_id=upload_id,
            page_number=page_number,
            start_order=start_order,
            start_offset=start_offset,
            budget_chars=budget_chars,
            prepend_sep=prepend_sep,
        )

    def read_section_span_segment(
        self,
        *,
        item_id: str,
        upload_id: str,
        start_page_number: int,
        start_block_order: int,
        end_page_number: int,
        end_block_order: int,
        current_page_number: int,
        current_block_order: int,
        start_offset: int,
        budget_chars: int,
        prepend_sep: bool = False,
    ) -> tuple[str, dict[str, object] | None, bool]:
        return _read_section_span_segment_impl(
            load_ordered_page_blocks=self.load_ordered_page_blocks,
            cursor_payload_base=self.cursor_payload_base,
            item_id=item_id,
            upload_id=upload_id,
            start_page_number=start_page_number,
            start_block_order=start_block_order,
            end_page_number=end_page_number,
            end_block_order=end_block_order,
            current_page_number=current_page_number,
            current_block_order=current_block_order,
            start_offset=start_offset,
            budget_chars=budget_chars,
            prepend_sep=prepend_sep,
        )

    def read_chunk_window_segment(
        self,
        *,
        item_id: str,
        upload_id: str,
        start_index: int,
        end_index: int,
        current_index: int,
        start_offset: int,
        budget_chars: int,
        prepend_sep: bool = False,
        business_profile,
    ) -> tuple[str, dict[str, object] | None, bool]:
        return _read_chunk_window_segment_impl(
            cursor_payload_base=self.cursor_payload_base,
            item_id=item_id,
            upload_id=upload_id,
            start_index=start_index,
            end_index=end_index,
            current_index=current_index,
            start_offset=start_offset,
            budget_chars=budget_chars,
            prepend_sep=prepend_sep,
            business_profile=business_profile,
        )

    def read_artifact_segment(
        self,
        *,
        item_id: str,
        artifact_id: str,
        start_offset: int,
        budget_chars: int,
    ) -> tuple[str, dict[str, object] | None, bool, dict[str, object] | None]:
        return _read_artifact_segment_impl(
            conversation=self.conversation,
            cursor_payload_base=self.cursor_payload_base,
            item_id=item_id,
            artifact_id=artifact_id,
            start_offset=start_offset,
            budget_chars=budget_chars,
        )

    def read_tabular_rows_with_anchor(
        self,
        *,
        item_id: str,
        upload_id: str,
        table_id: str,
        start_row_index: int,
        budget_chars: int,
        max_rows: int | None,
        business_profile,
        use_anchor: bool,
    ) -> tuple[dict[str, object], dict[str, object] | None, bool, bool, bool]:
        return _read_tabular_rows_with_anchor_impl(
            load_table_anchor_manifest=self.load_table_anchor_manifest,
            item_id=item_id,
            upload_id=upload_id,
            table_id=table_id,
            start_row_index=start_row_index,
            budget_chars=budget_chars,
            max_rows=max_rows,
            business_profile=business_profile,
            use_anchor=use_anchor,
        )

    def resolve_target(
        self,
        item_id: str,
    ) -> tuple[
        KnowledgeUploadChunk | None,
        KnowledgeUpload | None,
        KnowledgeUploadTable | None,
        KnowledgeUploadTableRow | None,
        dict[str, object] | None,
    ]:
        return _resolve_target_impl(item_id, conversation=self.conversation)

    def enforce_access(self, upload_id: str, *, item_id: str) -> dict[str, object] | None:
        return _enforce_access_impl(
            upload_id,
            item_id=item_id,
            conversation=self.conversation,
            context=self.context,
        )
