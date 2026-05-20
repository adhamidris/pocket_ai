from __future__ import annotations

from collections.abc import MutableMapping, Sequence

from ..knowledge_support.query_helpers import _compute_read_required


def _prepare_snippet_payloads(
    snippet_payloads: Sequence[MutableMapping[str, object]],
    *,
    intent: object,
) -> tuple[bool, set[str]]:
    read_required = False
    read_required_reasons_summary: set[str] = set()
    for payload in snippet_payloads:
        payload_read_required, reasons = _compute_read_required(payload)
        payload["read_required"] = payload_read_required
        if reasons:
            payload["read_required_reasons"] = reasons
            read_required_reasons_summary.update(reasons)
        if payload_read_required:
            read_required = True
        chunk_id = payload.get("chunk_id") or payload.get("id")
        upload_id = payload.get("upload_id")
        chunk_index = payload.get("chunk_index")

        # text.page means PDF page number, not chunk index.
        payload_meta = payload.get("metadata") or {}
        if isinstance(payload_meta, dict):
            actual_page = (
                payload_meta.get("table_page_number")
                or payload_meta.get("chunk_page")
                or payload_meta.get("page_number")
                or payload.get("page_number")
            )
        else:
            actual_page = payload.get("page_number")

        is_table_payload = bool(
            payload.get("is_table_chunk")
            or payload.get("structured_table_count")
            or payload.get("table_read_only")
            or (isinstance(payload_meta, dict) and payload_meta.get("is_table_chunk"))
        )
        read_id = str(chunk_id or "").strip() if is_table_payload else str(upload_id or chunk_id or "").strip()
        mode_hint = "full_page" if is_table_payload else ("full_page" if intent == "identifier" else "excerpt")
        read_hint: dict[str, object] = {
            # For table chunks, prefer the chunk id so readers can upgrade to full-page table content.
            "document_id": read_id,
            "mode": mode_hint,
        }

        if actual_page:
            try:
                page_num = int(actual_page)
                if page_num >= 1:
                    read_hint["page"] = page_num
            except (TypeError, ValueError):
                pass

        if "page" not in read_hint and isinstance(chunk_index, int):
            read_hint["offset"] = chunk_index

        payload["read_hint"] = read_hint

    return read_required, read_required_reasons_summary
