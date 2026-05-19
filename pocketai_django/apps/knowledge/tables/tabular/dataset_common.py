from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

from django.conf import settings

from apps.accounts.models import KnowledgeBlockType
from apps.knowledge.datasets.cards import build_dataset_card_segment_payload
from apps.knowledge.ingestion.aliases import ALIAS_KEYWORDS
from apps.knowledge.ingestion.contracts import PageBlockPayload, PageLayout, TableCellPayload, TablePayload, TableRowPayload
from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadFile


logger = logging.getLogger(__name__)


class IngestionDatasetCommonMixin:

    def _should_use_dataset_mode(self, *, estimated_rows: int | None) -> bool:
        if not self.dataset_mode_enabled:
            return False
        if estimated_rows is None:
            return False
        return int(estimated_rows) >= int(self.dataset_row_threshold)

    def _dataset_storage_directory(self, upload: KnowledgeUpload) -> tuple[Path, Path]:
        rel_dir = Path("datasets") / str(upload.business_profile_id) / str(upload.id)
        abs_dir = (self.media_root / rel_dir).resolve()
        abs_dir.relative_to(self.media_root)
        return abs_dir, rel_dir

    def _reset_dataset_storage(self, upload: KnowledgeUpload) -> tuple[Path, Path]:
        abs_dir, rel_dir = self._dataset_storage_directory(upload)
        if abs_dir.exists():
            try:
                shutil.rmtree(abs_dir)
            except OSError as exc:
                logger.warning("dataset.cleanup_failed upload=%s error=%s", upload.id, exc)
        abs_dir.mkdir(parents=True, exist_ok=True)
        return abs_dir, rel_dir

    @staticmethod
    def _stringify_dataset_cell(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if hasattr(value, "isoformat"):
            try:
                return value.isoformat()  # datetime/date-like
            except Exception:
                pass
        text = str(value)
        if "\x00" in text:
            text = text.replace("\x00", " ")
        return text

    @staticmethod
    def _estimate_delimited_row_count(path: Path, *, sample_bytes: int = 250_000) -> int | None:
        try:
            file_size = path.stat().st_size
        except OSError:
            return None
        if file_size <= 0:
            return 0
        try:
            with path.open("rb") as handle:
                sample = handle.read(sample_bytes)
        except OSError:
            return None
        lines = sample.count(b"\n")
        if lines <= 1:
            return 1 if file_size else 0
        avg = len(sample) / float(lines)
        if avg <= 0:
            return None
        return max(0, int(file_size / avg))

    def _suggest_dataset_key_columns(
        self,
        *,
        column_schema: Sequence[str],
        sample_rows: Sequence[Sequence[str]],
        max_candidates: int = 8,
    ) -> list[dict[str, Any]]:
        if not column_schema or not sample_rows:
            return []
        candidates: list[dict[str, Any]] = []
        sample_count = len(sample_rows)
        min_samples = min(10, sample_count)

        for idx, column in enumerate(column_schema):
            name = str(column or "").strip()
            canonical = self._canonical_column_name(name)
            if not canonical:
                continue
            values = [
                str(row[idx]).strip()
                for row in sample_rows
                if idx < len(row) and str(row[idx]).strip()
            ]
            non_empty = len(values)
            if non_empty < max(3, min_samples):
                continue
            unique = len(set(values))
            unique_ratio = unique / float(non_empty) if non_empty else 0.0
            keyword_match = any(key in canonical for key in ALIAS_KEYWORDS) or canonical.endswith("_id")
            score = (1.0 if keyword_match else 0.0) + unique_ratio
            candidates.append(
                {
                    "column": name,
                    "normalized": canonical,
                    "non_empty": non_empty,
                    "unique": unique,
                    "unique_ratio": round(unique_ratio, 4),
                    "keyword_match": bool(keyword_match),
                    "score": round(score, 4),
                }
            )

        candidates.sort(key=lambda item: (item.get("score", 0), item.get("non_empty", 0)), reverse=True)
        return candidates[: max_candidates]

    def _dataset_key_index_columns(
        self,
        *,
        upload: KnowledgeUpload,
        sheet_name: str | None,
        column_schema: Sequence[str],
        suggested_keys: Sequence[Mapping[str, Any]] | None,
        rules: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """
        Decide which columns should receive a dataset key index (Bloom filter).

        Preference order:
        1) Suggested key columns from sampling.
        2) Fallback heuristics on column names.
        """

        max_columns = int(getattr(settings, "DATASET_KEY_INDEX_MAX_COLUMNS", 4) or 4)
        max_columns = max(0, min(20, max_columns))
        if max_columns <= 0:
            return []

        allow_sensitive = str(getattr(settings, "DATASET_KEY_INDEX_ALLOW_SENSITIVE", "false")).lower() in {
            "1",
            "true",
            "yes",
        }
        min_suggested_score = float(getattr(settings, "DATASET_KEY_INDEX_SUGGESTED_MIN_SCORE", 0.9) or 0.9)
        min_suggested_score = max(0.0, min(10.0, min_suggested_score))

        schema_canon: list[str] = [self._canonical_column_name(col) for col in column_schema]
        index_map = {canon: idx for idx, canon in enumerate(schema_canon) if canon}
        name_map = {canon: col for canon, col in zip(schema_canon, column_schema) if canon and col}

        def _eligible(column_name: str) -> bool:
            if not column_name:
                return False
            if allow_sensitive or not rules:
                return True
            return not self._column_is_sensitive(column_name, rules)

        chosen: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _add(*, column: str, source: str, **extra: Any) -> None:
            canon = self._canonical_column_name(column)
            if not canon or canon in seen:
                return
            idx = index_map.get(canon)
            if idx is None:
                return
            actual = name_map.get(canon) or column
            if not _eligible(actual):
                return
            seen.add(canon)
            chosen.append(
                {
                    "column": actual,
                    "column_index": idx,
                    "source": source,
                    **extra,
                }
            )

        if suggested_keys:
            for entry in suggested_keys:
                col = str(entry.get("column") or "").strip()
                if not col:
                    continue
                try:
                    score = float(entry.get("score") or 0.0)
                except (TypeError, ValueError):
                    score = 0.0
                if score < min_suggested_score:
                    continue
                _add(column=col, source="suggested", suggested_score=score, suggested_keyword_match=bool(entry.get("keyword_match")))
                if len(chosen) >= max_columns:
                    break

        if len(chosen) >= max_columns:
            return chosen[:max_columns]

        heuristic_terms = ("invoice", "order", "ticket", "serial", "reference", "ref", "email", "phone", "mobile", "code", "sku")
        for col in column_schema:
            canon = self._canonical_column_name(col)
            if not canon or canon in seen:
                continue
            if not any(term in canon for term in heuristic_terms):
                continue
            _add(column=str(col or "").strip(), source="heuristic")
            if len(chosen) >= max_columns:
                break

        return chosen[:max_columns]

    def _build_dataset_card_segment_payload(
        self,
        *,
        upload: KnowledgeUpload,
        ingestion_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return build_dataset_card_segment_payload(upload=upload, ingestion_metadata=ingestion_metadata)

    def _build_dataset_preview_table(
        self,
        *,
        order_index: int,
        sheet_name: str,
        sheet_index: int | None,
        column_schema: list[str],
        preview_rows: Sequence[tuple[int, Sequence[str]]],
        file_detail: KnowledgeUploadFile,
        source_label: str,
        content_type: str,
        rules: Mapping[str, Any] | None,
    ) -> tuple[TablePayload, PageLayout]:
        visible_mask = [
            not self._column_is_sensitive(column, rules)
            for column in column_schema
        ] if rules else [True] * len(column_schema)
        rows: list[TableRowPayload] = []
        for row_index, values in preview_rows:
            attributes = {column_schema[i]: (values[i] if i < len(values) else "") for i in range(len(column_schema))}
            if rules and self._row_is_internal(attributes, rules):
                continue
            cells: list[TableCellPayload] = []
            row_values: list[str] = []
            for col_idx, column_key in enumerate(column_schema):
                if col_idx >= len(values):
                    cell_value = ""
                else:
                    cell_value = str(values[col_idx] or "")
                if visible_mask[col_idx]:
                    cells.append(
                        TableCellPayload(
                            row_index=row_index,
                            column_index=col_idx,
                            column_key=column_key,
                            raw_text=cell_value,
                        )
                    )
                    row_values.append(cell_value)
            rows.append(
                TableRowPayload(
                    row_index=row_index,
                    page_number=sheet_index,
                    raw_text="\t".join(row_values),
                    metadata={"source": source_label, "sheet": sheet_name},
                    cells=cells,
                )
            )

        table = TablePayload(
            order_index=order_index,
            title=sheet_name,
            section_heading=sheet_name,
            page_number=sheet_index,
            column_schema=column_schema,
            metadata={
                "source": source_label,
                "sheet_name": sheet_name,
                "filename": file_detail.filename,
            },
            rows=rows,
        )
        preview = self._table_preview_text([table], rules=rules)
        page_layout = PageLayout(
            page_number=sheet_index or order_index,
            width=612,
            height=792,
            rotation=0,
            text_density=len(preview.strip()) / float(612 * 792),
            has_ocr_content=False,
            content_type=content_type,
            blocks=[
                PageBlockPayload(
                    block_type=KnowledgeBlockType.TABLE,
                    order_index=order_index,
                    text=preview,
                    metadata={"source": source_label, "sheet": sheet_name},
                )
            ],
            metadata={"sheet_name": sheet_name, "dataset_mode": True},
        )
        return table, page_layout
