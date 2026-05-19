from __future__ import annotations

from collections import Counter
from typing import Any, Sequence

from apps.knowledge.ingestion.contracts import PageBlockPayload, PageLayout, TablePayload


class IngestionPdfPromotionRestoreMixin:

    def _restore_consumed_blocks_for_suppressed_tables(
        self,
        pages: Sequence[PageLayout],
        surviving_tables: Sequence[TablePayload],
    ) -> tuple[list[PageLayout], dict[str, Any]]:
        if not pages:
            return [], {"restored_blocks": 0, "pages_touched": 0, "suppressed_table_refs": 0}

        surviving_refs: set[tuple[int, int]] = set()
        for table in surviving_tables:
            try:
                order_index = int(table.order_index)
                page_number = int(table.page_number or 0)
            except (TypeError, ValueError):
                continue
            if order_index >= 0 and page_number > 0:
                surviving_refs.add((page_number, order_index))

        restored_blocks = 0
        pages_touched = 0
        suppressed_refs: set[tuple[int, int]] = set()
        restored_by_reason: Counter[str] = Counter()
        updated_pages: list[PageLayout] = []

        consumed_keys = (
            "canonical_consumed_by_table",
            "canonical_consumed_reason",
            "canonical_consumed_table_order_index",
            "canonical_consumed_table_page_number",
            "canonical_consumed_row_index",
            "canonical_consumed_column_index",
        )

        for page in pages:
            page_number = int(page.page_number or 0)
            page_changed = False
            updated_blocks: list[PageBlockPayload] = []

            for block in page.blocks:
                block_meta = block.metadata if isinstance(block.metadata, dict) else {}
                if not block_meta.get("canonical_consumed_by_table"):
                    updated_blocks.append(block)
                    continue

                raw_order_index = block_meta.get("canonical_consumed_table_order_index")
                raw_page_number = block_meta.get("canonical_consumed_table_page_number")
                reason = str(block_meta.get("canonical_consumed_reason") or "").strip() or "unknown"
                try:
                    owner_order_index = int(raw_order_index)
                except (TypeError, ValueError):
                    owner_order_index = -1
                try:
                    owner_page_number = int(raw_page_number or page_number)
                except (TypeError, ValueError):
                    owner_page_number = page_number

                owner_ref = (owner_page_number, owner_order_index)
                if owner_order_index >= 0 and owner_page_number > 0 and owner_ref in surviving_refs:
                    updated_blocks.append(block)
                    continue

                restored_meta = dict(block_meta)
                for key in consumed_keys:
                    restored_meta.pop(key, None)
                restored_meta["canonical_restored_after_table_suppression"] = True
                restored_meta["canonical_restored_suppressed_table_order_index"] = owner_order_index
                restored_meta["canonical_restored_suppressed_table_page_number"] = owner_page_number
                restored_meta["canonical_restored_from_reason"] = reason

                updated_blocks.append(
                    PageBlockPayload(
                        block_type=block.block_type,
                        order_index=block.order_index,
                        text=block.text,
                        bbox=block.bbox,
                        section_heading=block.section_heading,
                        heading_path=list(block.heading_path or []),
                        detected_language=block.detected_language,
                        confidence=block.confidence,
                        metadata=restored_meta,
                    )
                )
                page_changed = True
                restored_blocks += 1
                restored_by_reason[reason] += 1
                if owner_order_index >= 0 and owner_page_number > 0:
                    suppressed_refs.add(owner_ref)

            if page_changed:
                pages_touched += 1
                updated_pages.append(
                    PageLayout(
                        page_number=page.page_number,
                        width=page.width,
                        height=page.height,
                        rotation=page.rotation,
                        text_density=page.text_density,
                        has_ocr_content=page.has_ocr_content,
                        content_type=page.content_type,
                        blocks=updated_blocks,
                        metadata=dict(page.metadata or {}),
                    )
                )
            else:
                updated_pages.append(page)

        diagnostics: dict[str, Any] = {
            "restored_blocks": restored_blocks,
            "pages_touched": pages_touched,
            "suppressed_table_refs": len(suppressed_refs),
        }
        if restored_by_reason:
            diagnostics["restored_by_reason"] = dict(restored_by_reason)
        return updated_pages, diagnostics
