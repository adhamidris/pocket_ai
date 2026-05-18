from __future__ import annotations

import re
from typing import Any

from apps.accounts.models import KnowledgeBlockType, KnowledgeIssueSeverity
from apps.knowledge.ingestion_contracts import (
    IssuePayload,
    PageBlockPayload,
    PageLayout,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
)


class TableDetector:
    """
    Heuristic table extraction that promotes structured storage even when advanced
    detectors (Camelot, pdfplumber, etc.) are not available. Designed to be easily
    swapped with more capable detectors later.
    """

    def detect_tables(
        self,
        pages: list[PageLayout],
        *,
        format_hint: str | None = None,
    ) -> tuple[list[TablePayload], list[IssuePayload]]:
        tables: list[TablePayload] = []
        issues: list[IssuePayload] = []
        order_index = 0
        for page in pages:
            for block in page.blocks:
                if self._looks_like_table_block(block):
                    order_index += 1
                    table_payload, block_issues = self._build_table_from_block(
                        block,
                        page_number=page.page_number,
                        order_index=order_index,
                        section_heading=block.section_heading,
                    )
                    tables.append(table_payload)
                    issues.extend(block_issues)
        return tables, issues

    def _looks_like_table_block(self, block: PageBlockPayload) -> bool:
        text = block.text or ""
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return False
        if block.block_type == KnowledgeBlockType.TABLE:
            return True

        # Quick bullet-list rejection: first 3 non-empty lines start with bullets
        heads = ["".join(line.strip().split()[:1]) for line in lines[:3] if line.strip()]
        bullet_heads = {"*", "**", "***", "****", "*****", "-", "•", "—"}
        if heads and all(h in bullet_heads for h in heads):
            return False

        # Original signals (pipes, tabs, or multi-spaces)
        sample = lines[0]
        return ("|" in sample) or ("\t" in sample) or bool(re.search(r"\s{2,}", sample))


    def _build_table_from_block(
        self,
        block: PageBlockPayload,
        *,
        page_number: int,
        order_index: int,
        section_heading: str,
    ) -> tuple[TablePayload, list[IssuePayload]]:
        lines = [line for line in (block.text or "").splitlines() if line.strip()]
        delimiter = self._detect_delimiter(lines[0])
        rows = [self._split_row(line, delimiter) for line in lines]
        header = rows[0] if rows else []
        issues: list[IssuePayload] = []
        if not header:
            issues.append(
                IssuePayload(
                    code="table_missing_header",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description="Table block did not contain a header row.",
                    page_number=page_number,
                    table_order_index=order_index,
                )
            )
        column_schema = [self._normalize_header_cell(cell, idx) for idx, cell in enumerate(header)]
        expected_columns = len(column_schema) or len(rows[1]) if len(rows) > 1 else 0
        table_rows: list[TableRowPayload] = []
        for idx, row_cells in enumerate(rows):
            row_index = idx
            normalized_cells: list[TableCellPayload] = []
            if expected_columns and len(row_cells) != expected_columns:
                issues.append(
                    IssuePayload(
                        code="table_column_mismatch",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description="Row column count mismatch.",
                        page_number=page_number,
                        table_order_index=order_index,
                        row_index=row_index,
                        details={
                            "expected": expected_columns,
                            "observed": len(row_cells),
                        },
                    )
                )
            for col_idx, cell_text in enumerate(row_cells):
                column_key = column_schema[col_idx] if col_idx < len(column_schema) else f"column_{col_idx+1}"
                normalized_value = self._normalize_cell_value(cell_text)
                normalized_cells.append(
                    TableCellPayload(
                        row_index=row_index,
                        column_index=col_idx,
                        column_key=column_key,
                        raw_text=cell_text,
                        normalized_value=normalized_value,
                        bbox=block.bbox,
                        confidence=None,
                    )
                )
            table_rows.append(
                TableRowPayload(
                    row_index=row_index,
                    page_number=page_number,
                    bbox=block.bbox,
                    raw_text=" | ".join(row_cells),
                    metadata={"row_type": "header" if idx == 0 else "data"},
                    cells=normalized_cells,
                )
            )
        table_payload = TablePayload(
            order_index=order_index,
            title=section_heading or f"Table {order_index}",
            section_heading=section_heading,
            page_number=page_number,
            bbox=block.bbox,
            column_schema=column_schema,
            data_dictionary={},
            metadata={"detected_via": "heuristic"},
            rows=table_rows,
        )
        return table_payload, issues

    @staticmethod
    def _detect_delimiter(sample: str) -> str:
        if "|" in sample:
            return "|"
        if "\t" in sample:
            return "\t"
        return "  "

    @staticmethod
    def _split_row(line: str, delimiter: str) -> list[str]:
        if delimiter == "  ":
            return [cell.strip() for cell in re.split(r"\s{2,}", line) if cell.strip()]
        return [cell.strip() for cell in line.split(delimiter)]

    @staticmethod
    def _normalize_header_cell(cell: str, index: int) -> str:
        raw = (cell or "").strip()

        # collapse letter-by-letter headers: "W H I T E" -> "WHITE"
        if re.fullmatch(r"(?:[A-Za-z]\s+){2,}[A-Za-z]", raw):
            raw = raw.replace(" ", "")

        # collapse spaced underscores: "valid_thru_12_28" is okay; but reduce noise
        s = re.sub(r"\s+", " ", raw)
        s = s.lower()
        s = re.sub(r"[^a-z0-9%$€£]+", "_", s)
        s = re.sub(r"_+", "_", s).strip("_")
        return s or f"column_{index+1}"


    @staticmethod
    def _normalize_cell_value(cell: str) -> dict[str, Any]:
        text = (cell or "")
        # Normalize whitespace incl. thin/nb spaces; preserve decimals/commas
        text = text.replace("\u00A0", " ").replace("\u2009", " ").replace("\u202F", " ")
        text = " ".join(text.strip().split())
        if not text:
            return {}

        out: dict[str, Any] = {}

        # Percent first (captures "3.99%" or "2 %")
        m_pct = re.search(r"(\d+(?:[\.,]\d+)?)\s*%", text)
        if m_pct:
            try:
                pct_val = float(m_pct.group(1).replace(",", "."))
                out["percent"] = pct_val / 100.0
            except ValueError:
                pass

        # Currency normalization map
        currency_alias = {
            "EG£": "EGP",
            "LE": "EGP",
            "EGF": "EGP",
            "E6P": "EGP",
        }
        currency_codes = r"(EGP|EG£|EGF|E6P|USD|EUR|AED|SAR|GBP|LE)"
        symbol = r"[$€£]"

        # Ranges: "EGP 100–200" / "EGP 100-200" / "100–200 EGP"
        m_range = re.search(
            rf"(?:(?:{currency_codes}|{symbol})\s*)?([+-]?\d[\d,]*(?:\.\d+)?)\s*[-–]\s*([+-]?\d[\d,]*(?:\.\d+)?)(?:\s*(?:{currency_codes}|{symbol}))?",
            text,
        )
        if m_range:
            try:
                a = float(m_range.group(2 if m_range.group(2) else 1).replace(",", ""))
            except Exception:
                a = None
            try:
                b = float(m_range.group(3 if m_range.group(3) else 2).replace(",", ""))
            except Exception:
                b = None
            cur_match = re.search(rf"{currency_codes}|{symbol}", text)
            cur = (cur_match.group(0) if cur_match else "").upper()
            cur = currency_alias.get(cur, cur or "")
            if a is not None and b is not None:
                out["range"] = {"min": min(a, b), "max": max(a, b)}
                if cur:
                    out["currency"] = "EGP" if cur in {"EG£", "LE"} else cur

        # Currency amount (single)
        m_amt = re.search(
            rf"(?:{currency_codes}|{symbol})\s*([+-]?\d[\d,]*(?:\.\d+)?)", text, flags=re.IGNORECASE
        )
        if m_amt:
            try:
                amount = float(m_amt.group(2).replace(",", "")) if m_amt.lastindex and m_amt.lastindex >= 2 else float(m_amt.group(1).replace(",", ""))
                cur_match = re.search(rf"{currency_codes}|{symbol}", text, flags=re.IGNORECASE)
                cur = (cur_match.group(0).upper() if cur_match else "") or ""
                cur = currency_alias.get(cur, cur)
                if cur in {"EG£", "LE"}:
                    cur = "EGP"
                if cur:
                    out["currency"] = cur
                out["amount"] = amount
            except ValueError:
                pass

        # Minimum amount (e.g., "min. EGP 100")
        m_min = re.search(
            rf"\bmin(?:imum)?\.?\s+(?:{currency_codes}|{symbol})\s*([+-]?\d[\d,]*(?:\.\d+)?)",
            text,
            flags=re.IGNORECASE,
        )
        if m_min:
            try:
                amount = float(m_min.group(2).replace(",", "")) if m_min.lastindex and m_min.lastindex >= 2 else float(m_min.group(1).replace(",", ""))
                cur_match = re.search(rf"{currency_codes}|{symbol}", text, flags=re.IGNORECASE)
                cur = (cur_match.group(0).upper() if cur_match else "") or ""
                cur = currency_alias.get(cur, cur)
                if cur in {"EG£", "LE"}:
                    cur = "EGP"
                out["min"] = {"amount": amount, "currency": cur or out.get("currency")}
            except ValueError:
                pass

        # Fallback number (no currency)
        if "amount" not in out and "range" not in out:
            m_num = re.search(r"([+-]?\d[\d,]*(?:\.\d+)?)", text)
            if m_num:
                try:
                    out["number"] = float(m_num.group(1).replace(",", ""))
                except ValueError:
                    pass

        return out
