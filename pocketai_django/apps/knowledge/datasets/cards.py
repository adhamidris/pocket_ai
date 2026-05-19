from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from django.conf import settings

from apps.accounts.models import KnowledgeVisibility
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
)
from apps.rag.embeddings import EmbeddingProviderError, build_embedding_service


logger = logging.getLogger(__name__)


def _clip_text(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    normalized = " ".join((text or "").replace("\r", " ").replace("\n", " ").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def _csv_list(items: Sequence[str], *, limit_items: int, limit_chars: int) -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    cleaned = cleaned[: max(0, limit_items)]
    joined = ", ".join(cleaned)
    joined = _clip_text(joined, limit_chars)
    remaining = max(0, len([str(item).strip() for item in items if str(item).strip()]) - len(cleaned))
    if remaining and joined:
        return f"{joined} …+{remaining} more"
    return joined


def _normalize_embedding(vector: Sequence[float] | None) -> list[float] | None:
    if not vector:
        return None
    try:
        values = [float(v) for v in vector]
    except (TypeError, ValueError):
        return None
    expected = getattr(settings, "EMBED_DIM", None)
    if expected:
        if len(values) > expected:
            logger.warning("Embedding dimension mismatch: got %s, expected %s. Trimming.", len(values), expected)
            values = values[:expected]
        elif len(values) < expected:
            logger.warning("Embedding dimension mismatch: got %s, expected %s. Padding.", len(values), expected)
            values = values + [0.0] * (expected - len(values))
    return values


def build_dataset_card_segment_payload(
    *,
    upload: KnowledgeUpload,
    ingestion_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Build a compact "dataset card" chunk so RAG can retrieve dataset metadata
    without loading rows. Tool-name agnostic: it only signals that tabular
    querying is required.
    """

    meta = ingestion_metadata if isinstance(ingestion_metadata, Mapping) else {}
    dataset = meta.get("dataset")
    if not isinstance(dataset, Mapping) or not dataset.get("enabled"):
        return None

    max_columns = int(getattr(settings, "DATASET_CARD_MAX_COLUMNS", 60) or 60)
    max_columns = max(10, min(200, max_columns))
    max_sheets = int(getattr(settings, "DATASET_CARD_MAX_SHEETS", 8) or 8)
    max_sheets = max(1, min(50, max_sheets))
    max_chars_setting = int(getattr(settings, "DATASET_CARD_MAX_CHARS", 900) or 900)
    max_chars_setting = max(300, min(5000, max_chars_setting))
    preview_cap = int(getattr(settings, "RAG_SEARCH_PREVIEW_CHAR_LIMIT", 800) or 800)
    preview_cap = max(200, preview_cap)
    max_chars = min(max_chars_setting, preview_cap)

    filename = str(meta.get("filename") or "").strip()
    format_hint = str(meta.get("format") or "").strip()
    display_name = (upload.display_name or filename or "Dataset").strip()
    row_count = dataset.get("row_count")
    try:
        row_count_int = int(row_count) if row_count is not None else None
    except (TypeError, ValueError):
        row_count_int = None

    lines: list[str] = [
        "[Dataset Card]",
        f"Name: {_clip_text(display_name, 120)}",
        f"document_id: {upload.id}",
        "type: dataset (tabular)",
        "hints: dataset_mode=true; tabular_query_required=true",
    ]
    if format_hint:
        lines.append(f"format: {_clip_text(format_hint, 40)}")
    if row_count_int is not None and row_count_int >= 0:
        lines.append(f"rows: {row_count_int:,}")
    suggested_keys = dataset.get("suggested_key_columns")
    key_columns: list[str] = []
    if isinstance(suggested_keys, list):
        for entry in suggested_keys:
            if not isinstance(entry, Mapping):
                continue
            col = str(entry.get("column") or "").strip()
            if col and col not in key_columns:
                key_columns.append(col)
    if key_columns:
        lines.append(f"key_columns: {_csv_list(key_columns, limit_items=8, limit_chars=240)}")

    sheets = dataset.get("sheets")
    if isinstance(sheets, list) and sheets:
        sheet_entries = [entry for entry in sheets if isinstance(entry, Mapping)]
        lines.append(f"sheets: {len(sheet_entries)}")
        for entry in sheet_entries[:max_sheets]:
            sheet_name = str(entry.get("sheet_name") or "").strip() or "Sheet"
            sheet_index = entry.get("sheet_index")
            try:
                sheet_index_int = int(sheet_index) if sheet_index is not None else None
            except (TypeError, ValueError):
                sheet_index_int = None
            sheet_rows = entry.get("row_count")
            try:
                sheet_rows_int = int(sheet_rows) if sheet_rows is not None else None
            except (TypeError, ValueError):
                sheet_rows_int = None

            sheet_label = f"[{sheet_index_int}]" if sheet_index_int is not None else ""
            sheet_line = f"- {sheet_label} {_clip_text(sheet_name, 80)}"
            if sheet_rows_int is not None and sheet_rows_int >= 0:
                sheet_line += f" (rows: {sheet_rows_int:,})"
            lines.append(sheet_line)

            sheet_columns = entry.get("column_schema")
            if isinstance(sheet_columns, list) and sheet_columns:
                lines.append(
                    f"  columns: {_csv_list([str(c) for c in sheet_columns], limit_items=max_columns, limit_chars=320)}"
                )
            sheet_keys = entry.get("suggested_key_columns")
            sheet_key_cols: list[str] = []
            if isinstance(sheet_keys, list):
                for key_entry in sheet_keys:
                    if not isinstance(key_entry, Mapping):
                        continue
                    col = str(key_entry.get("column") or "").strip()
                    if col and col not in sheet_key_cols:
                        sheet_key_cols.append(col)
            if sheet_key_cols:
                lines.append(f"  key_columns: {_csv_list(sheet_key_cols, limit_items=6, limit_chars=200)}")
        remaining = len(sheet_entries) - max_sheets
        if remaining > 0:
            lines.append(f"- …+{remaining} more sheets")
    else:
        columns = dataset.get("column_schema")
        if isinstance(columns, list) and columns:
            lines.append(
                f"columns: {_csv_list([str(c) for c in columns], limit_items=max_columns, limit_chars=520)}"
            )

    lines.append("usage: ask for a key identifier + column before querying large datasets.")

    text_full = "\n".join(line for line in lines if line).strip()
    if len(text_full) > max_chars:
        text = text_full[: max(0, max_chars - 1)].rstrip() + "…"
    else:
        text = text_full

    return {
        "text": text,
        "metadata": {
            "strategy": "dataset_card",
            "is_table_chunk": False,
            "is_dataset_card": True,
            "dataset_mode": True,
            "visibility": getattr(upload, "visibility", KnowledgeVisibility.PRIVATE),
        },
    }


def refresh_dataset_card_chunk(*, upload: KnowledgeUpload) -> bool:
    payload = build_dataset_card_segment_payload(upload=upload, ingestion_metadata=upload.ingestion_metadata)
    if not payload:
        return False

    chunk = (
        KnowledgeUploadChunk.objects.filter(upload=upload, metadata__strategy="dataset_card")
        .order_by("chunk_index")
        .first()
    )
    if not chunk:
        return False

    content = str(payload.get("text") or "")
    updated_meta = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    merged_meta = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    merged_meta = {**merged_meta, **dict(updated_meta)}

    provider = build_embedding_service()
    vector = None
    if provider:
        try:
            vectors = provider.embed_texts([content])
            vector = vectors[0] if vectors else None
        except EmbeddingProviderError as exc:
            logger.warning("Dataset card embedding failed upload=%s error=%s", upload.id, exc)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Dataset card embedding unexpected failure upload=%s", upload.id)

    normalized = _normalize_embedding(vector) if vector else None
    if normalized:
        chunk.embedding = normalized

    chunk.content = content
    chunk.metadata = merged_meta
    chunk.token_count = len(content.split())
    chunk.save(update_fields=["content", "metadata", "token_count", "embedding", "updated_at"])
    return True
