from __future__ import annotations

import logging
import uuid

from apps.accounts.models import KnowledgeStatus
from apps.knowledge.access.visibility import apply_customer_visible_chunks
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadPageBlock,
    KnowledgeUploadTable,
)

logger = logging.getLogger(__name__)


class PageWindowSourceMixin:

    @staticmethod
    def _get_structured_table_content_for_page(
        upload: KnowledgeUpload,
        page_number: int,
        max_chars: int | None = None,
    ) -> tuple[str, bool, bool]:
        """
        Get structured table content for a page from table row chunks.

        Returns (content, truncated_flag, has_tables).
        """
        try:
            table_ids = list(
                KnowledgeUploadTable.objects.filter(
                    upload=upload,
                    page__page_number=page_number,
                )
                .order_by("order_index")
                .values_list("id", flat=True)
            )

            if not table_ids:
                return ("", False, False)

            row_chunks = list(
                KnowledgeUploadChunk.objects.filter(
                    upload=upload,
                    metadata__table_id__in=[str(tid) for tid in table_ids],
                    metadata__table_chunk_role="row",
                )
                .order_by("chunk_index")
            )

            if not row_chunks:
                return ("", False, False)

            content_parts: list[str] = []
            current_table_id: str | None = None

            for chunk in row_chunks:
                chunk_meta = chunk.metadata if isinstance(chunk.metadata, dict) else {}
                table_id = chunk_meta.get("table_id")

                if table_id != current_table_id and current_table_id is not None:
                    content_parts.append("\n---\n")
                current_table_id = table_id

                if chunk.content:
                    content_parts.append(chunk.content.strip())

            combined = "\n\n".join(content_parts).strip()
            truncated = False

            if max_chars and len(combined) > max_chars:
                combined = combined[:max_chars]
                truncated = True

            return (combined, truncated, True)

        except Exception as exc:
            logger.warning("Failed to get structured table content: %s", exc)
            return ("", False, False)

    @staticmethod
    def _get_page_text_from_blocks(
        upload: KnowledgeUpload,
        page_number: int,
        max_chars: int | None = None,
    ) -> tuple[str, bool]:
        """
        Extract actual page text from KnowledgeUploadPageBlock entries.
        Returns (text, truncated_flag).
        """
        try:
            blocks = list(
                KnowledgeUploadPageBlock.objects.filter(
                    upload=upload,
                    page__page_number=page_number,
                )
                .select_related("page")
                .order_by("order_index")
            )

            if not blocks:
                return ("", False)

            page_parts: list[str] = []
            for block in blocks:
                if block.text:
                    page_parts.append(block.text)

            combined = "\n\n".join(page_parts).strip()

            if max_chars and len(combined) > max_chars:
                return (combined[:max_chars], True)

            return (combined, False)

        except Exception:
            return ("", False)

    @staticmethod
    def _resolve_upload_page_chunk(
        *,
        business_profile,
        upload_id: uuid.UUID,
        page_index: int,
    ) -> KnowledgeUploadChunk | None:
        """
        Locate a chunk for the requested page index (1-based) within an upload.
        Falls back to the closest available chunk when the requested index is out
        of range.
        """

        target_index = max(0, page_index - 1)
        base_qs = apply_customer_visible_chunks(
            KnowledgeUploadChunk.objects.filter(
                upload__business_profile=business_profile,
                upload__status=KnowledgeStatus.ACTIVE,
                upload_id=upload_id,
            ).select_related("upload")
        )

        try:
            chunk = base_qs.get(chunk_index=target_index)
            return chunk
        except KnowledgeUploadChunk.DoesNotExist:
            pass

        chunk = base_qs.filter(chunk_index__gte=target_index).order_by("chunk_index").first()
        if chunk:
            return chunk
        return base_qs.order_by("-chunk_index").first()
