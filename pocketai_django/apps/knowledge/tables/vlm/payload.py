from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Mapping

from apps.knowledge.ingestion.contracts import TableCellPayload, TablePayload, TableRowPayload
from apps.knowledge.tables.detection import TableDetector


logger = logging.getLogger(__name__)

try:  # pragma: no cover - dependency failure should be surfaced at runtime
    import fitz  # type: ignore[attr-defined]  # PyMuPDF
except ImportError:  # pragma: no cover - optional dependency
    fitz = None  # type: ignore


class IngestionTableVlmPayloadMixin:


    @staticmethod
    def _render_table_crop(path: Path, page_number: int, bbox: dict[str, float]) -> bytes | None:
        if fitz is None:
            return None
        doc = None
        try:
            x0 = float(bbox.get("x0", 0.0))
            y0 = float(bbox.get("y0", 0.0))
            x1 = float(bbox.get("x1", 0.0))
            y1 = float(bbox.get("y1", 0.0))
            if x1 <= x0 or y1 <= y0:
                return None

            doc = fitz.open(path)
            page = doc[page_number - 1]
            rect = fitz.Rect(x0, y0, x1, y1)
            pix = page.get_pixmap(clip=rect, dpi=200)
            return pix.tobytes("png")
        except Exception:
            return None
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    @staticmethod
    def _render_full_page(path: Path, page_number: int) -> bytes | None:
        """Render entire PDF page as PNG for VLM repair when bbox is unavailable."""
        if fitz is None:
            return None
        doc = None
        try:
            doc = fitz.open(path)
            if page_number < 1 or page_number > len(doc):
                return None
            page = doc[page_number - 1]
            # Use lower DPI for full page to keep token cost reasonable
            pix = page.get_pixmap(dpi=150)
            return pix.tobytes("png")
        except Exception:
            return None
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    def _run_vlm_table_repair(
        self,
        client: Any,
        image_bytes: bytes,
        render_mode: str = "crop",
        table_hint: str | None = None,
    ) -> dict[str, Any] | None:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        
        _merged_cell_instruction = (
            "IMPORTANT: If a cell value is visually centered across multiple columns "
            "(i.e. it spans or merges several column positions), you MUST repeat that "
            "same value in EVERY column it applies to.  Do NOT leave the other columns "
            "empty — duplicate the value so each spanned column contains it."
        )

        if render_mode == "full_page" and table_hint:
            prompt = (
                "Extract the table from this page image.\n"
                "Select the table that best matches the hint below (it includes expected columns/row labels).\n"
                "HINT:\n"
                f"{table_hint}\n\n"
                "Return strict JSON with keys: columns (array of column header strings) and rows "
                "(array of arrays with cell values). Rows should contain only data rows (no header row). "
                "Make sure to capture ALL columns and ALL values correctly.\n\n"
                f"{_merged_cell_instruction}"
            )
            max_tokens = 4000  # Full page may have more data
        else:
            prompt = (
                "Extract the table from this image. "
                "Return strict JSON with keys: columns (array of strings) and rows "
                "(array of arrays). Rows should contain only data rows (no header row).\n\n"
                f"{_merged_cell_instruction}"
            )
            max_tokens = 1200
        
        try:
            response = client.chat.completions.create(
                model=self.table_vlm_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{encoded}"},
                            },
                        ],
                    }
                ],
                max_tokens=max_tokens,
            )
        except Exception as exc:
            logger.warning("table.vlm.repair_failed error=%s", exc)
            return None

        content = ""
        try:
            content = response.choices[0].message.content or ""
        except Exception:
            content = ""
        if not content:
            return None
        return self._parse_vlm_table_json(content)

    @staticmethod
    def _parse_vlm_table_json(text: str) -> dict[str, Any] | None:
        if not text:
            return None
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        if "columns" not in payload or "rows" not in payload:
            return None
        return payload

    def _table_payload_from_vlm(
        self,
        *,
        payload: Mapping[str, Any],
        order_index: int,
        page_number: int,
        bbox: dict[str, float],
        title: str,
        section_heading: str,
        source_metadata: Mapping[str, Any] | None = None,
    ) -> TablePayload | None:
        columns = payload.get("columns") or []
        rows = payload.get("rows") or []
        if not isinstance(columns, list) or not isinstance(rows, list):
            return None

        column_schema: list[str] = []
        for idx, col in enumerate(columns):
            column_schema.append(TableDetector._normalize_header_cell(str(col), idx))
        if not column_schema:
            max_cols = max((len(r) for r in rows if isinstance(r, list)), default=0)
            column_schema = [f"column_{i+1}" for i in range(max_cols)]

        table_rows: list[TableRowPayload] = []
        header_cells: list[TableCellPayload] = []
        for col_idx, label in enumerate(columns):
            header_cells.append(
                TableCellPayload(
                    row_index=0,
                    column_index=col_idx,
                    column_key=column_schema[col_idx],
                    raw_text=str(label),
                    normalized_value=TableDetector._normalize_cell_value(str(label)),
                    bbox=bbox,
                    confidence=None,
                )
            )
        if header_cells:
            table_rows.append(
                TableRowPayload(
                    row_index=0,
                    page_number=page_number,
                    bbox=bbox,
                    raw_text=" | ".join(str(c.raw_text) for c in header_cells),
                    metadata={"row_type": "header"},
                    cells=header_cells,
                )
            )

        for row_idx, row in enumerate(rows, start=1):
            if not isinstance(row, list):
                continue
            # Detect runs of identical non-empty adjacent values — these
            # indicate the VLM correctly duplicated a spanning/merged value.
            # We tag each cell in such a run with the true column_span so
            # downstream applicability annotation can treat them as explicit
            # spans rather than independent values.
            str_values = [str(v) if v is not None else "" for v in row]
            span_for_col: dict[int, int] = {}
            col_cursor = 0
            while col_cursor < len(str_values):
                val = str_values[col_cursor].strip()
                if val:
                    run_end = col_cursor + 1
                    while run_end < len(str_values) and str_values[run_end].strip() == val:
                        run_end += 1
                    run_length = run_end - col_cursor
                    if run_length > 1:
                        for ci in range(col_cursor, run_end):
                            span_for_col[ci] = run_length
                    col_cursor = run_end
                else:
                    col_cursor += 1
            cells: list[TableCellPayload] = []
            for col_idx, value in enumerate(row):
                raw_text = str(value) if value is not None else ""
                column_key = column_schema[col_idx] if col_idx < len(column_schema) else f"column_{col_idx+1}"
                cell_meta: dict[str, Any] = {}
                if col_idx in span_for_col:
                    cell_meta["column_span"] = span_for_col[col_idx]
                cells.append(
                    TableCellPayload(
                        row_index=row_idx,
                        column_index=col_idx,
                        column_key=column_key,
                        raw_text=raw_text,
                        normalized_value=TableDetector._normalize_cell_value(raw_text),
                        bbox=bbox,
                        confidence=None,
                        metadata=cell_meta,
                    )
                )
            table_rows.append(
                TableRowPayload(
                    row_index=row_idx,
                    page_number=page_number,
                    bbox=bbox,
                    raw_text=" | ".join(str(c.raw_text) for c in cells),
                    metadata={"row_type": "data"},
                    cells=cells,
                )
            )

        merged_meta: dict[str, Any] = dict(source_metadata or {})
        base_detected_via = str(merged_meta.get("detected_via") or "").strip() or "vlm"
        if "vlm" not in base_detected_via.lower():
            merged_meta["detected_via"] = f"{base_detected_via}+vlm"
        merged_meta["vlm_model"] = self.table_vlm_model
        try:
            existing_conf = float(merged_meta.get("structure_confidence") or 0.0)
        except (TypeError, ValueError):
            existing_conf = 0.0
        merged_meta["structure_confidence"] = max(0.0, min(1.0, max(existing_conf, 0.9)))

        return TablePayload(
            order_index=order_index,
            title=title or f"Table {order_index}",
            section_heading=section_heading or "",
            page_number=page_number,
            bbox=bbox,
            column_schema=column_schema,
            data_dictionary={},
            metadata=merged_meta,
            rows=table_rows,
        )
