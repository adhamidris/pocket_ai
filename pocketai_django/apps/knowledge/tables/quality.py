from __future__ import annotations

import re
from typing import Any, Mapping

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.ingestion.contracts import IssuePayload, TablePayload


class IngestionTableQualityMixin:

    def _suppress_list_like_heuristics(
        self, tables: list[TablePayload]
    ) -> tuple[list[TablePayload], list[IssuePayload]]:
        """
        Drop heuristic tables that are actually bullet lists:
        - mainly 2 columns,
        - first col looks like bullets (*, **, -, •, —),
        - second col is long paragraph-like text for the majority of sampled rows.
        """
        out: list[TablePayload] = []
        issues: list[IssuePayload] = []
        bullet_re = re.compile(r"^(\*{1,5}|[-•—])+$")
        for t in tables:
            rows = t.rows or []
            if len(rows) < 2:
                out.append(t)
                continue
            sample = rows[: min(10, len(rows))]
            bullety = 0
            long_second = 0
            examined = 0
            for r in sample:
                cells = r.cells or []
                if not cells:
                    continue
                c0 = (cells[0].raw_text or "").strip() if len(cells) >= 1 else ""
                c1 = (cells[1].raw_text or "").strip() if len(cells) >= 2 else ""
                examined += 1
                if bullet_re.fullmatch(c0):
                    bullety += 1
                if len(c1) >= 60:
                    long_second += 1
            if examined >= 3 and (len(t.column_schema or []) <= 2) and (bullety / examined >= 0.5) and (long_second / examined >= 0.5):
                issues.append(
                    IssuePayload(
                        code="list_promoted_suppressed",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description="Heuristic table looked like a bullet/list; suppressed in favor of plain text.",
                        page_number=t.page_number,
                        table_order_index=t.order_index,
                        details={"rows_checked": examined},
                    )
                )
                continue
            out.append(t)
        return out, issues

    def _suppress_sparse_geometry_tables(
        self, tables: list[TablePayload]
    ) -> tuple[list[TablePayload], list[IssuePayload]]:
        """
        Drop geometry tables that are too sparse:
        - >60% of cells are empty across sampled rows,
        - >50% of columns are mostly empty (>60% empty in that column).
        
        This prevents giant noisy tables from low-quality geometry detection.
        """
        out: list[TablePayload] = []
        issues: list[IssuePayload] = []
        
        CELL_EMPTY_THRESHOLD = 0.6      # 60% empty cells
        COLUMN_EMPTY_THRESHOLD = 0.6    # 60% empty in a column = "mostly empty"
        COLUMN_SPARSE_RATIO = 0.5       # 50% of columns mostly empty
        
        for t in tables:
            rows = t.rows or []
            if len(rows) < 2:
                out.append(t)
                continue
            
            # Sample first N rows (excluding header if present)
            sample_size = min(10, len(rows))
            sample = rows[:sample_size]
            
            # Skip if no column schema
            num_cols = len(t.column_schema or [])
            if num_cols == 0:
                out.append(t)
                continue
            
            # Count empty cells overall
            total_cells = 0
            empty_cells = 0
            
            # Track emptiness per column
            column_empty_counts = [0] * num_cols
            column_total_counts = [0] * num_cols
            
            for r in sample:
                cells = r.cells or []
                for c_idx in range(num_cols):
                    if c_idx < len(cells):
                        cell_text = (cells[c_idx].raw_text or "").strip()
                        total_cells += 1
                        column_total_counts[c_idx] += 1
                        
                        if not cell_text:
                            empty_cells += 1
                            column_empty_counts[c_idx] += 1
                    else:
                        # Missing cell counts as empty
                        total_cells += 1
                        empty_cells += 1
                        column_total_counts[c_idx] += 1
                        column_empty_counts[c_idx] += 1
            
            if total_cells == 0:
                out.append(t)
                continue
            
            # Calculate overall empty ratio
            overall_empty_ratio = empty_cells / total_cells
            
            # Calculate per-column empty ratios
            mostly_empty_columns = 0
            for c_idx in range(num_cols):
                if column_total_counts[c_idx] > 0:
                    col_empty_ratio = column_empty_counts[c_idx] / column_total_counts[c_idx]
                    if col_empty_ratio > COLUMN_EMPTY_THRESHOLD:
                        mostly_empty_columns += 1
            
            column_sparse_ratio = mostly_empty_columns / num_cols if num_cols > 0 else 0
            
            # Suppress if both conditions met
            if overall_empty_ratio > CELL_EMPTY_THRESHOLD and column_sparse_ratio > COLUMN_SPARSE_RATIO:
                issues.append(
                    IssuePayload(
                        code="geometry_suppressed_sparse",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description=f"Geometry table was too sparse ({overall_empty_ratio:.1%} empty cells, {column_sparse_ratio:.1%} sparse columns); suppressed.",
                        page_number=t.page_number,
                        table_order_index=t.order_index,
                        details={
                            "rows_checked": sample_size,
                            "overall_empty_ratio": round(overall_empty_ratio, 3),
                            "mostly_empty_columns": mostly_empty_columns,
                            "total_columns": num_cols,
                            "column_sparse_ratio": round(column_sparse_ratio, 3),
                        },
                    )
                )
                continue
            
            out.append(t)
        
        return out, issues


    def _assess_table_quality(self, table_payload) -> dict[str, Any]:
        """
        Assess table quality to detect decorative/garbage tables.
        
        Returns dict with:
        - quality_score: float 0.0-1.0 (0=garbage, 1=high quality)
        - is_decorative: bool (True if likely decorative)
        - signals: dict of detected quality signals
        """
        signals: dict[str, Any] = {}
        penalties = 0
        max_penalties = 18
        
        # Get table data
        column_schema = table_payload.column_schema or []
        rows = table_payload.rows or []
        page_number = table_payload.page_number or 0
        order_index = table_payload.order_index or 0
        table_meta = table_payload.metadata if isinstance(getattr(table_payload, "metadata", None), Mapping) else {}
        promoted_headers = int(table_meta.get("embedded_header_rows_promoted") or 0)
        carried_labels = int(table_meta.get("merged_parent_label_cells_carried_down") or 0)
        if promoted_headers > 0:
            signals["embedded_header_rows_promoted"] = promoted_headers
        if carried_labels > 0:
            signals["merged_parent_label_cells_carried_down"] = carried_labels

        def _row_type(row: Any) -> str:
            meta = row.metadata if isinstance(getattr(row, "metadata", None), Mapping) else {}
            return str(meta.get("row_type") or "").strip().lower()

        # "Readable" rows must match read_knowledge's visible-row filter.
        readable_rows = [row for row in rows if _row_type(row) not in {"header", "section_header"}]
        section_header_rows = [row for row in rows if _row_type(row) == "section_header"]

        effective_columns = self._table_effective_column_count(table_payload)
        signals["effective_column_count"] = effective_columns
        
        # Heuristic 1: Nonsense column names
        nonsense_patterns = [
            r'^column_\d+$',  # Generic column_1, column_2
            r'^col\d+$',      # col1, col2
            r'^\d+$',         # Just numbers
            r'^[a-z]$',       # Single letters
        ]
        nonsense_count = 0
        for col in column_schema:
            col_str = str(col).strip().lower()
            for pattern in nonsense_patterns:
                if re.match(pattern, col_str):
                    nonsense_count += 1
                    break
        
        if nonsense_count >= len(column_schema) * 0.75 and len(column_schema) > 0:
            signals['nonsense_columns'] = True
            penalties += 3
        
        # Heuristic 2: Spaced characters detection (e.g., "W H I T E")
        spaced_char_count = 0
        total_cells = 0
        
        for row in rows[:10]:  # Check first 10 rows
            for cell in (row.cells or []):
                raw_text = str(cell.raw_text or "").strip()
                total_cells += 1
                
                # Check for spaced single characters: "A B C D"
                if re.match(r'^([A-Z]\s){2,}[A-Z]$', raw_text) or re.match(r'^(\w\s){2,}\w$', raw_text):
                    spaced_char_count += 1
                    signals.setdefault('spaced_char_examples', []).append(raw_text[:50])
        
        if total_cells > 0 and spaced_char_count / total_cells >= 0.3:
            signals['spaced_characters'] = True
            penalties += 4
        
        # Heuristic 3: Row/column consistency (use readable rows only).
        row_lengths: list[int] = []
        non_empty_cells = 0
        expected_columns = len(column_schema)
        for row in readable_rows:
            cell_list = list(row.cells or [])
            row_lengths.append(len(cell_list))
            for cell in cell_list:
                if str(cell.raw_text or "").strip():
                    non_empty_cells += 1
        if not expected_columns and row_lengths:
            expected_columns = max(row_lengths)
        if rows and expected_columns:
            matching = sum(1 for length in row_lengths if length == expected_columns)
            row_consistency = matching / max(1, len(row_lengths))
            fill_ratio = non_empty_cells / max(1, expected_columns * max(1, len(readable_rows)))
            signals["row_consistency"] = round(row_consistency, 2)
            signals["cell_fill_ratio"] = round(fill_ratio, 2)
            if row_consistency < 0.6:
                signals["row_misalignment"] = True
                penalties += 2
            if fill_ratio < 0.4:
                signals["sparse_table"] = True
                penalties += 1

        # Heuristic 3c: long prose cells inside narrow tables.
        non_empty_texts: list[str] = []
        long_cell_count = 0
        short_cell_count = 0
        max_cell_word_count = 0
        paragraph_like_rows = 0
        structured_rows = 0
        multi_cell_rows = 0
        value_rows = 0
        scaffold_rows = 0
        placeholder_cells = 0
        for row in readable_rows:
            row_has_long_cell = False
            row_texts: list[str] = []
            compact_cell_count = 0
            label_like_cells = 0
            row_placeholder_cells = 0
            row_has_numeric = False
            for cell in row.cells or []:
                raw_text = str(cell.raw_text or "").strip()
                if not raw_text:
                    continue
                row_texts.append(raw_text)
                non_empty_texts.append(raw_text)
                word_count = len(re.findall(r"\w+", raw_text))
                max_cell_word_count = max(max_cell_word_count, word_count)
                if self._pdf_cell_looks_placeholder(raw_text):
                    placeholder_cells += 1
                    row_placeholder_cells += 1
                if self._pdf_cell_looks_label_like(raw_text):
                    label_like_cells += 1
                if self._has_numeric_table_signal(raw_text):
                    row_has_numeric = True
                if word_count <= 2 or len(raw_text) <= 10:
                    short_cell_count += 1
                if 0 < word_count <= 6 and len(raw_text) <= 40:
                    compact_cell_count += 1
                if word_count >= self.pdf_table_paragraph_long_cell_words:
                    long_cell_count += 1
                    row_has_long_cell = True
            if row_has_long_cell:
                paragraph_like_rows += 1
            populated_cell_count = len(row_texts)
            if populated_cell_count:
                if populated_cell_count >= 2:
                    multi_cell_rows += 1
                row_has_value_evidence = (
                    (row_has_numeric or compact_cell_count >= 2)
                    and row_placeholder_cells < populated_cell_count
                    and label_like_cells < populated_cell_count
                    and not row_has_long_cell
                )
                if row_has_value_evidence:
                    value_rows += 1
                if (
                    row_has_numeric
                    or populated_cell_count >= 3
                    or (
                        populated_cell_count >= 2
                        and compact_cell_count >= 2
                        and label_like_cells < populated_cell_count
                    )
                ):
                    structured_rows += 1
                if (
                    populated_cell_count == 1
                    or row_has_long_cell
                    or row_placeholder_cells > 0
                    or label_like_cells >= populated_cell_count
                ):
                    scaffold_rows += 1
        long_cell_ratio = long_cell_count / max(1, len(non_empty_texts))
        short_cell_ratio = short_cell_count / max(1, len(non_empty_texts))
        signals["long_cell_ratio"] = round(long_cell_ratio, 2)
        signals["short_cell_ratio"] = round(short_cell_ratio, 2)
        signals["max_cell_word_count"] = max_cell_word_count
        signals["paragraph_like_rows"] = paragraph_like_rows
        data_row_count = len(readable_rows)
        structured_row_ratio = structured_rows / max(1, data_row_count)
        scaffold_row_ratio = scaffold_rows / max(1, data_row_count)
        placeholder_cell_ratio = placeholder_cells / max(1, len(non_empty_texts))
        multi_cell_row_ratio = multi_cell_rows / max(1, data_row_count)
        value_row_ratio = value_rows / max(1, data_row_count)
        signals["structured_row_ratio"] = round(structured_row_ratio, 2)
        signals["scaffold_row_ratio"] = round(scaffold_row_ratio, 2)
        signals["placeholder_cell_ratio"] = round(placeholder_cell_ratio, 2)
        signals["multi_cell_row_ratio"] = round(multi_cell_row_ratio, 2)
        signals["value_row_ratio"] = round(value_row_ratio, 2)
        if (
            effective_columns <= 2
            and len(readable_rows) >= self.pdf_table_paragraph_min_rows
            and long_cell_ratio >= self.pdf_table_paragraph_long_cell_ratio
        ):
            signals["paragraph_like_table"] = True
            penalties += 3
        if (
            effective_columns >= self.pdf_table_micro_fragment_min_columns
            and short_cell_ratio >= self.pdf_table_micro_fragment_short_cell_ratio
            and len(readable_rows) >= self.pdf_table_paragraph_min_rows
        ):
            signals["micro_fragment_table"] = True
            penalties += 4

        # Heuristic 3d: leading blank rows often indicate layout fragments or fake tables.
        leading_blank_rows = 0
        for row in rows:
            row_cells = list(row.cells or [])
            populated = sum(1 for cell in row_cells if str(cell.raw_text or "").strip())
            if populated == 0:
                leading_blank_rows += 1
                continue
            if populated <= 1 and all(len(str(cell.raw_text or "").strip()) <= 2 for cell in row_cells if str(cell.raw_text or "").strip()):
                leading_blank_rows += 1
                continue
            break
        signals["leading_blank_rows"] = leading_blank_rows
        if leading_blank_rows >= self.pdf_table_leading_blank_row_limit:
            penalties += 2

        # Heuristic 3b: logical-row fragmentation.
        # Penalize tables whose rows are structurally "consistent" but semantically shattered
        # into many tiny descriptor/value fragments across adjacent rows.
        def _leading_text(row: Any) -> str:
            values: list[str] = []
            for cell in list(row.cells or [])[:2]:
                value = str(cell.raw_text or "").strip()
                if value:
                    values.append(value)
            return " ".join(values).strip()

        def _non_empty_texts(row: Any) -> list[str]:
            return [str(cell.raw_text or "").strip() for cell in (row.cells or []) if str(cell.raw_text or "").strip()]

        descriptor_fragment_rows = 0
        value_fragment_rows = 0
        fragmented_streak = 0
        fragment_row_sequences = 0
        for row in readable_rows:
            non_empty = _non_empty_texts(row)
            leading = _leading_text(row)
            non_empty_count = len(non_empty)
            has_numeric = any(self._has_numeric_table_signal(text) for text in non_empty)
            descriptor_fragment = (
                0 < non_empty_count <= 2
                and bool(leading)
                and not has_numeric
                and len(re.findall(r"\w+", leading)) <= 5
            )
            value_fragment = any(
                text.endswith(("+", "/", "-"))
                or text.lower().startswith(("correspondent", "courier", "max.", "min.", "+"))
                for text in non_empty
            )
            if descriptor_fragment:
                descriptor_fragment_rows += 1
            if value_fragment:
                value_fragment_rows += 1
            if descriptor_fragment or value_fragment:
                fragmented_streak += 1
                if fragmented_streak == 2:
                    fragment_row_sequences += 1
            else:
                fragmented_streak = 0

        if len(readable_rows) >= 6 and (
            descriptor_fragment_rows >= 3
            or value_fragment_rows >= 3
            or fragment_row_sequences >= 2
        ):
            signals["fragmented_logical_rows"] = True
            signals["descriptor_fragment_rows"] = descriptor_fragment_rows
            signals["value_fragment_rows"] = value_fragment_rows
            signals["fragment_row_sequences"] = fragment_row_sequences
            penalties += 3

        # Heuristic 4: Header confidence
        header_cells = None
        header_rows = [row for row in rows if str((row.metadata or {}).get("row_type") or "").strip().lower() == "header"]
        header_column_coverage: list[str] = []
        if header_rows:
            max_header_columns = max(len(list(row.cells or [])) for row in header_rows)
            header_column_coverage = []
            for col_idx in range(max_header_columns):
                labels: list[str] = []
                seen_labels: set[str] = set()
                for row in header_rows:
                    row_cells = list(row.cells or [])
                    if col_idx >= len(row_cells):
                        continue
                    label = str(row_cells[col_idx].raw_text or "").strip()
                    if not label:
                        continue
                    dedupe_key = re.sub(r"\s+", " ", label).strip().lower()
                    if dedupe_key in seen_labels:
                        continue
                    seen_labels.add(dedupe_key)
                    labels.append(label)
                header_column_coverage.append(" | ".join(labels).strip())
            header_cells = [str(cell.raw_text or "") for cell in (header_rows[0].cells or [])]
        if header_cells:
            joined = " ".join(header_cells).strip()
            alnum = [c for c in joined if c.isalnum()]
            digits = sum(1 for c in joined if c.isdigit())
            letters = sum(1 for c in joined if c.isalpha())
            non_numeric = sum(1 for cell in header_cells if not re.search(r"\d", cell or ""))
            non_numeric_ratio = non_numeric / max(1, len(header_cells))
            digit_ratio = digits / max(1, len(alnum))
            alpha_ratio = letters / max(1, len(alnum))
            length_ratio = sum(1 for cell in header_cells if len(cell.strip()) >= 3) / max(1, len(header_cells))
            header_word_counts = [len(re.findall(r"\w+", str(cell or ""))) for cell in header_cells if str(cell or "").strip()]
            header_long_cells = sum(
                1
                for cell in header_cells
                if len(re.findall(r"\w+", str(cell or ""))) >= max(10, self.pdf_table_paragraph_long_cell_words)
                or len(str(cell or "").strip()) >= 80
            )
            header_confidence = 0.0
            if non_numeric_ratio >= 0.6:
                header_confidence += 0.4
            if alpha_ratio >= 0.4:
                header_confidence += 0.3
            if digit_ratio < 0.3:
                header_confidence += 0.2
            if length_ratio >= 0.5:
                header_confidence += 0.1
            signals["header_confidence"] = round(min(header_confidence, 1.0), 2)
            if header_long_cells > 0 and header_word_counts:
                long_ratio = header_long_cells / max(1, len(header_word_counts))
                avg_words = sum(header_word_counts) / float(len(header_word_counts))
                if long_ratio >= 0.5 or avg_words >= 10.0:
                    signals["header_paragraph_like"] = True
            if header_confidence < 0.3:
                penalties += 1
        else:
            signals["header_confidence"] = 0.0
            penalties += 1
        if (
            effective_columns <= 3
            and data_row_count <= 12
            and structured_row_ratio <= 0.45
            and scaffold_row_ratio >= 0.5
        ):
            signals["low_structure_table"] = True
            penalties += 3
        if (
            effective_columns <= 3
            and 0 < data_row_count <= 4
            and bool(signals.get("nonsense_columns"))
            and float(signals.get("header_confidence") or 0.0) == 0.0
            and multi_cell_row_ratio >= 0.8
            and structured_row_ratio >= 0.8
            and scaffold_row_ratio >= 0.5
            and placeholder_cell_ratio >= 0.2
        ):
            signals["compact_banner_table"] = True
            penalties += 3

        # Heuristic 5: Repeating patterns
        cell_values: list[str] = []
        for row in rows[:5]:
            for cell in (row.cells or []):
                raw_text = str(cell.raw_text or "").strip().lower()
                if raw_text:
                    cell_values.append(raw_text)
        
        if len(cell_values) >= 3:
            unique_values = len(set(cell_values))
            if unique_values / len(cell_values) < 0.3:  # Less than 30% unique
                signals['high_repetition'] = True
                signals['unique_ratio'] = round(unique_values / len(cell_values), 2)
                penalties += 2
        
        # Heuristic 6: Too few readable rows (header + section_header excluded).
        if data_row_count < 2:
            signals['insufficient_rows'] = True
            penalties += 2

        if (
            0 < data_row_count <= self.pdf_table_bridge_max_rows
            and effective_columns <= 3
            and (
                long_cell_ratio >= self.pdf_table_bridge_long_cell_ratio
                or max_cell_word_count >= max(10, self.pdf_table_paragraph_long_cell_words - 2)
            )
            and (
                max_cell_word_count >= (self.pdf_table_paragraph_long_cell_words * 2)
                or float(signals.get("header_confidence") or 0.0) == 0.0
                or bool(signals.get("nonsense_columns"))
            )
        ):
            signals["bridge_like_table"] = True
            penalties += 3

        # Heuristic 6b: No readable rows at all.
        # If our postprocess classified everything as section_header/header, the
        # table will be unreadable at runtime (read_knowledge excludes them).
        if not readable_rows and section_header_rows:
            signals["no_readable_rows"] = True
            penalties += 5
        
        # Heuristic 7: Header/footer position (first/last page)
        if page_number == 1 and order_index == 0:
            # First table on first page = might be header decoration
            signals['first_page_first_table'] = True
            penalties += 1
        
        # Heuristic 8: Card-like patterns (e.g., credit card mockups)
        card_keywords = ['valid', 'thru', 'expires', 'cvv', 'card number', 'cardholder']
        keyword_matches = 0
        card_number_hits = 0
        
        for row in rows[:5]:
            for cell in (row.cells or []):
                raw_text = str(cell.raw_text or "").strip().lower()
                if "valid" in raw_text and "thru" in raw_text:
                    signals["valid_thru"] = True
                if re.search(r"\b(?:\d{4}[\s-]?){3}\d{4}\b", raw_text):
                    card_number_hits += 1
                for keyword in card_keywords:
                    if keyword in raw_text:
                        keyword_matches += 1
                        signals.setdefault('card_keywords', []).append(keyword)
        
        if card_number_hits:
            signals["card_number_pattern"] = True
            penalties += 2
        if keyword_matches >= 3 and data_row_count <= 2:
            signals['card_mockup'] = True
            penalties += 3
        
        # Heuristic 9: Column misalignment detection
        # Detects when header cells are empty but corresponding data cells have values
        # This is a common Azure DI extraction error for complex tables
        if header_column_coverage and rows:
            data_rows_for_check = readable_rows[:5]
            empty_header_with_data: list[int] = []
            
            for col_idx, header_val in enumerate(header_column_coverage):
                header_empty = not str(header_val or "").strip()
                if header_empty:
                    # Check if any data rows have values in this column
                    for row in data_rows_for_check:
                        cell_list = list(row.cells or [])
                        for cell in cell_list:
                            if cell.column_index == col_idx:
                                cell_text = str(cell.raw_text or "").strip()
                                if cell_text and len(cell_text) > 2:
                                    if not self._quality_misalignment_is_legitimate(
                                        col_idx=col_idx,
                                        header_column_coverage=header_column_coverage,
                                        data_rows=data_rows_for_check,
                                    ):
                                        empty_header_with_data.append(col_idx)
                                    break
                        if col_idx in empty_header_with_data:
                            break
            
            if empty_header_with_data:
                signals['column_misalignment'] = True
                signals['misaligned_columns'] = empty_header_with_data[:5]
                # Significant penalty - this causes wrong data attribution
                penalties += 3
        
        # Calculate quality score (0.0 = garbage, 1.0 = high quality)
        quality_score = max(0.0, 1.0 - (penalties / max_penalties))
        
        # Determine if decorative (threshold: quality < 0.5)
        is_decorative = quality_score < 0.5
        
        return {
            'quality_score': round(quality_score, 2),
            'is_decorative': is_decorative,
            'signals': signals,
            'penalties': penalties,
        }
