from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

try:  # pragma: no cover - dependency failure should be surfaced at runtime
    from docx import Document as DocxDocument
except ImportError:  # pragma: no cover - fallback handled via runtime check
    DocxDocument = None  # type: ignore

from apps.accounts.models import KnowledgeBlockType
from apps.knowledge.ingestion_contracts import (
    IssuePayload,
    KnowledgeIngestionError,
    PageBlockPayload,
    PageLayout,
    PageRendererResult,
    PdfSpan,
)
from apps.knowledge.ingestion_ocr import OCRReconciler


class PageRenderer:
    """
    Produces layout-aware payloads using PyMuPDF when available.
    Fallbacks collapse documents into a single page with coarse metadata so downstream
    persistence can still operate.
    """

    def __init__(self, *, pymupdf_module: Any | None = None):
        self._fitz = pymupdf_module

    def render(self, path: Path, *, format_hint: str, ocr: OCRReconciler | None = None) -> PageRendererResult:
        if format_hint == "pdf" and self._fitz is not None:
            return self._render_pdf(path, ocr=ocr)
        if format_hint == "docx":
            return self._render_docx(path)
        text = path.read_text(encoding="utf-8", errors="ignore").replace("\x00", " ")
        page = PageLayout(
            page_number=1,
            width=612,
            height=792,
            rotation=0,
            text_density=len(text.strip()) / (612 * 792),
            has_ocr_content=False,
            content_type="text/plain",
            blocks=[
                PageBlockPayload(
                    block_type=KnowledgeBlockType.PARAGRAPH,
                    order_index=0,
                    text=text,
                )
            ],
        )
        return PageRendererResult(text=text, pages=[page])


    def _render_pdf(self, path: Path, *, ocr: OCRReconciler | None = None) -> PageRendererResult:
        """
        Enhanced PDF rendering with:
        1. Block-based reading order (top-to-bottom, left-to-right)
        2. Inline TSV representation for tables
        3. Better structure preservation
        """
        try:
            document = self._fitz.open(path)
        except Exception as exc:
            raise KnowledgeIngestionError(f"Unable to open PDF for layout parsing: {exc}") from exc

        pages: list[PageLayout] = []
        fragments: list[str] = []
        issues: list[IssuePayload] = []
        
        for index, page in enumerate(document, start=1):
            # Get raw blocks for structured extraction
            raw_blocks = page.get_text("blocks") or []
            
            # Sort blocks by reading order: top-to-bottom (y0), then left-to-right (x0)
            sorted_blocks = sorted(
                raw_blocks,
                key=lambda b: (round(b[1] / 5) * 5, b[0])
            )
            
            # NEW: Group blocks into rows based on vertical position
            block_rows = self._group_blocks_into_rows(sorted_blocks)
            
            # Assemble text from rows with inline table formatting
            block_texts = []
            decorative_fragments_filtered = 0
            for row_blocks in block_rows:
                # Check if this row looks like a table row (multiple columns)
                if len(row_blocks) >= 3:  # 3+ blocks in same row = likely table
                    # Format as TSV
                    row_cells = [block[4].strip() for block in row_blocks if len(block) > 4]
                    row_cells = [cell for cell in row_cells if cell]  # Remove empty
                    if row_cells:
                        row_text = "\t".join(row_cells)
                        signals = self._decorative_text_signals(row_text)
                        if self._is_decorative_text(row_text, signals):
                            decorative_fragments_filtered += 1
                            continue
                        block_texts.append(row_text)
                else:
                    # Regular text - just concatenate
                    for block in row_blocks:
                        if len(block) > 4:
                            text_fragment = block[4].strip()
                            if text_fragment:
                                signals = self._decorative_text_signals(text_fragment)
                                if self._is_decorative_text(text_fragment, signals):
                                    decorative_fragments_filtered += 1
                                    continue
                                block_texts.append(text_fragment)
            
            # Join blocks with appropriate spacing
            plain_text = "\n".join(block_texts) if block_texts else ""
            
            # Calculate metrics
            raw_char_count = len(plain_text.strip())
            rect = page.rect
            area = max(rect.width * rect.height, 1.0)
            density = raw_char_count / area
            
            # OCR reconciliation
            has_ocr = False
            reconciled_text = plain_text
            if ocr:
                reconciled_text, has_ocr, ocr_issues = ocr.reconcile_pdf_page(
                    page,
                    page_number=index,
                    extracted_text=plain_text,
                    text_density=density,
                )
                issues.extend(ocr_issues)
            
            final_text = reconciled_text or ""
            final_char_count = len(final_text.strip())
            final_density = final_char_count / area if final_char_count else 0.0
            
            fragments.append(final_text)
            
            # Build structured blocks (keep your existing logic)
            blocks = self._build_pdf_blocks(page, index)
            if has_ocr and final_text.strip() and not any((block.text or "").strip() for block in blocks):
                blocks = [
                    PageBlockPayload(
                        block_type=KnowledgeBlockType.PARAGRAPH,
                        order_index=0,
                        text=final_text,
                        metadata={"source": "ocr"},
                    )
                ]
            
            pages.append(
                PageLayout(
                    page_number=index,
                    width=float(rect.width),
                    height=float(rect.height),
                    rotation=int(page.rotation or 0),
                    text_density=final_density,
                    has_ocr_content=has_ocr,
                    content_type="application/pdf",
                    blocks=blocks,
                    metadata={
                        "char_count": final_char_count,
                        "raw_char_count": raw_char_count,
                        "raw_text_density": density,
                        "ocr_render_dpi": getattr(ocr, "render_dpi", None) if has_ocr else None,
                        "block_count": len(raw_blocks),
                        "decorative_fragments_filtered": decorative_fragments_filtered,
                        "extraction_method": "block_sorted_with_row_detection"
                    },
                )
            )
        
        return PageRendererResult(
            text="\n\n".join(fragments),
            pages=pages,
            issues=issues
        )

            # [ADD] Helper: collect PdfSpan from 'rawdict'/'dict' shapes
    def _collect_spans_from_textdict(self, page_index: int, obj: dict) -> list[PdfSpan]:
        """
        Walk text 'dict'/'rawdict' structure: blocks(type=0)->lines->spans and collect PdfSpan.
        Safely handles missing keys; prefers span bbox, falls back to line bbox, else zeros.
        """
        page_spans: list[PdfSpan] = []
        if not obj:
            return page_spans

        blocks = (obj.get("blocks") or [])
        for block in blocks:
            if (block or {}).get("type") != 0:
                continue
            lines = (block.get("lines") or [])
            for line in lines:
                line_bbox = line.get("bbox") or [0, 0, 0, 0]
                spans = (line.get("spans") or [])
                for span in spans:
                    text = (span.get("text") or "").strip()
                    if not text:
                        continue
                    bbox = span.get("bbox") or line_bbox or [0, 0, 0, 0]
                    size_val = span.get("size")
                    try:
                        size = float(size_val) if size_val is not None else None
                    except Exception:
                        size = None
                    page_spans.append(
                        PdfSpan(
                            page_number=page_index,
                            text=text,
                            x0=float(bbox[0]),
                            y0=float(bbox[1]),
                            x1=float(bbox[2]),
                            y1=float(bbox[3]),
                            font=span.get("font"),
                            size=size,
                        )
                    )
        return page_spans


    def extract_pdf_spans(self, path: Path) -> list[list[PdfSpan]]:
        """
        Returns a list per page; each page is a list of PdfSpan with geometry + font/size.
        Robust: tries 'rawdict', falls back to 'dict', then to 'words'.
        """
        if self._fitz is None:
            return []
        try:
            doc = self._fitz.open(path)
        except Exception:
            return []

        results: list[list[PdfSpan]] = []
        for page_index, page in enumerate(doc, start=1):
            # --- Fast path: 'rawdict'
            page_spans: list[PdfSpan] = []
            try:
                raw = page.get_text("rawdict") or {}
                page_spans = self._collect_spans_from_textdict(page_index, raw)
            except Exception:
                page_spans = []

            # --- Fallback 1: 'dict'
            if not page_spans:
                try:
                    dct = page.get_text("dict") or {}
                    page_spans = self._collect_spans_from_textdict(page_index, dct)
                except Exception:
                    page_spans = []

            # --- Fallback 2: 'words'
            if not page_spans:
                try:
                    words = page.get_text("words") or []
                    word_spans: list[PdfSpan] = []
                    for w in words:
                        # words tuple: x0, y0, x1, y1, "word", block_no, line_no, word_no
                        if not w or len(w) < 5:
                            continue
                        x0, y0, x1, y1 = float(w[0]), float(w[1]), float(w[2]), float(w[3])
                        wtext = (w[4] or "").strip()
                        if not wtext:
                            continue
                        word_spans.append(
                            PdfSpan(
                                page_number=page_index,
                                text=wtext,
                                x0=x0,
                                y0=y0,
                                x1=x1,
                                y1=y1,
                                font=None,
                                size=None,  # no size from 'words'; header logic still has non-size cues
                            )
                        )
                    page_spans = word_spans
                except Exception:
                    page_spans = []

            results.append(page_spans)
        return results


    def _group_blocks_into_rows(self, sorted_blocks: list) -> list[list]:
        """
        Group blocks that are on the same horizontal line (same Y position)
        Returns list of rows, where each row is a list of blocks
        """
        if not sorted_blocks:
            return []
        
        rows = []
        current_row = []
        current_y = None
        tolerance = 5  # Vertical position tolerance in points
        
        for block in sorted_blocks:
            if len(block) <= 1:
                continue
            
            block_y = block[1]  # Y position
            
            if current_y is None:
                # First block
                current_y = block_y
                current_row = [block]
            elif abs(block_y - current_y) <= tolerance:
                # Same row
                current_row.append(block)
            else:
                # New row
                if current_row:
                    rows.append(current_row)
                current_row = [block]
                current_y = block_y
        
        # Add last row
        if current_row:
            rows.append(current_row)
        
        return rows

    @staticmethod
    def _block_anchor(page_number: int, order_index: int) -> str:
        return f"p{page_number}-b{order_index}"

    @staticmethod
    def _decorative_text_signals(text: str) -> dict[str, Any]:
        signals: dict[str, Any] = {}
        if not text:
            return signals
        lowered = text.lower()
        if re.search(r"\bvalid\s*thru\b", lowered):
            signals["valid_thru"] = True
        if re.search(r"\b(?:\d{4}[\s-]?){3}\d{4}\b", lowered):
            signals["card_number"] = True
        stripped = text.strip()
        if re.fullmatch(r"(?:[A-Za-z]\s+){2,}[A-Za-z]", stripped):
            signals["spaced_characters"] = True
        alnum = [c for c in stripped if c.isalnum()]
        digits = sum(1 for c in stripped if c.isdigit())
        if alnum and (digits / len(alnum)) >= 0.6 and len(stripped.split()) <= 4:
            signals["mostly_digits"] = True
        return signals

    @staticmethod
    def _is_decorative_text(text: str, signals: Mapping[str, Any]) -> bool:
        if not text:
            return False
        if signals.get("card_number") or signals.get("valid_thru"):
            return True
        if signals.get("spaced_characters") and len(text.split()) <= 6:
            return True
        if signals.get("mostly_digits") and len(text.split()) <= 4:
            return True
        return False

    @staticmethod
    def _page_region(bbox: Mapping[str, Any], page_height: float | None) -> str | None:
        if not page_height:
            return None
        y0 = float(bbox.get("y0") or 0.0)
        y1 = float(bbox.get("y1") or 0.0)
        if y1 <= page_height * 0.08:
            return "header"
        if y0 >= page_height * 0.92:
            return "footer"
        return None

    def _looks_like_table_text(self, text: str) -> bool:
        """Check if text block appears to be tabular data"""
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return False
        
        # Check for common table indicators
        sample = lines[0]
        has_pipes = "|" in sample
        has_tabs = "\t" in sample
        has_multi_spaces = bool(re.search(r"\s{3,}", sample))
        
        return has_pipes or has_tabs or has_multi_spaces

    def _format_table_as_tsv(self, text: str) -> str:
        """
        Convert table text to TSV format for better embedding/retrieval
        
        Example output:
        Card Type\tIssuance Fee\tInterest Rate
        Classic\tEGP 250\t3.99%
        Gold\tEGP 300\t3.99%
        """
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return text
        
        # Detect delimiter
        first_line = lines[0]
        if "|" in first_line:
            delimiter = "|"
        elif "\t" in first_line:
            delimiter = "\t"
        else:
            # Multiple spaces - use regex split
            delimiter = None
        
        formatted_rows = []
        for line in lines:
            if delimiter:
                cells = [cell.strip() for cell in line.split(delimiter) if cell.strip()]
            else:
                # Split on 2+ spaces
                cells = [cell.strip() for cell in re.split(r"\s{2,}", line) if cell.strip()]
            
            # Join with tab for consistent TSV format
            formatted_rows.append("\t".join(cells))
        
        return "\n".join(formatted_rows)


    def _render_docx(self, path: Path) -> PageRendererResult:
        if DocxDocument is None:
            raise KnowledgeIngestionError("DOCX ingestion requires the python-docx package.")
        try:
            document = DocxDocument(str(path))
        except Exception as exc:
            raise KnowledgeIngestionError(f"Unable to open DOCX for layout parsing: {exc}") from exc

        paragraphs = [paragraph.text for paragraph in document.paragraphs]
        text = "\n".join(paragraphs)
        blocks: list[PageBlockPayload] = []
        heading_context: list[str] = []
        for idx, paragraph in enumerate(paragraphs):
            stripped = paragraph.strip()
            block_type = KnowledgeBlockType.PARAGRAPH
            if self._looks_like_heading(stripped):
                block_type = KnowledgeBlockType.HEADING
                heading_context = [stripped]
                section_heading = stripped
            else:
                section_heading = heading_context[-1] if heading_context else ""
            blocks.append(
                PageBlockPayload(
                    block_type=block_type,
                    order_index=idx,
                    text=paragraph,
                    section_heading=section_heading,
                    heading_path=list(heading_context),
                )
            )
        page = PageLayout(
            page_number=1,
            width=612,
            height=792,
            rotation=0,
            text_density=len(text.strip()) / (612 * 792),
            has_ocr_content=False,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            blocks=blocks,
            metadata={"paragraph_count": len(paragraphs)},
        )
        return PageRendererResult(text=text, pages=[page])

    def _build_pdf_blocks(self, page: Any, page_number: int) -> list[PageBlockPayload]:
        try:
            raw_blocks = page.get_text("blocks") or []  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - fallback to full page block
            raw_blocks = []
        page_height = None
        try:
            page_height = float(page.rect.height)
        except Exception:
            page_height = None
        payloads: list[PageBlockPayload] = []
        heading_context: list[str] = []
        for order_index, block in enumerate(raw_blocks):
            text_fragment = block[4] if len(block) > 4 else ""
            bbox = {
                "x0": float(block[0]) if len(block) > 0 else 0.0,
                "y0": float(block[1]) if len(block) > 1 else 0.0,
                "x1": float(block[2]) if len(block) > 2 else 0.0,
                "y1": float(block[3]) if len(block) > 3 else 0.0,
            }
            stripped = text_fragment.strip()
            raw_block_type = None
            if len(block) > 6 and block[6] is not None:
                try:
                    raw_block_type = int(block[6])
                except (TypeError, ValueError):
                    raw_block_type = None
            block_type = self._resolve_block_type(block, stripped)
            if raw_block_type == 1:
                block_type = KnowledgeBlockType.IMAGE
            elif raw_block_type in {2, 3}:
                block_type = KnowledgeBlockType.FIGURE
            if self._looks_like_heading(stripped):
                heading_context = [stripped]
                section_heading = stripped
            else:
                section_heading = heading_context[-1] if heading_context else ""
            decorative_signals = self._decorative_text_signals(stripped) if stripped else {}
            is_decorative = self._is_decorative_text(stripped, decorative_signals)
            region_role = "text"
            if block_type == KnowledgeBlockType.TABLE:
                region_role = "table"
            elif block_type in {KnowledgeBlockType.IMAGE, KnowledgeBlockType.FIGURE}:
                region_role = "figure"
            elif is_decorative:
                region_role = "decorative"
            page_region = self._page_region(bbox, page_height) if stripped else None
            block_metadata: dict[str, Any] = {
                "anchor": self._block_anchor(page_number, order_index),
                "region_role": region_role,
            }
            if raw_block_type is not None:
                block_metadata["raw_block_type"] = raw_block_type
            if page_region:
                block_metadata["page_region"] = page_region
            if decorative_signals:
                block_metadata["decorative_signals"] = decorative_signals
            if is_decorative:
                block_metadata["is_decorative"] = True
            payloads.append(
                PageBlockPayload(
                    block_type=block_type,
                    order_index=order_index,
                    text=text_fragment,
                    bbox=bbox,
                    section_heading=section_heading,
                    heading_path=list(heading_context),
                    detected_language="",
                    confidence=None,
                    metadata=block_metadata,
                )
            )
        if not payloads:
            payloads.append(
                PageBlockPayload(
                    block_type=KnowledgeBlockType.PARAGRAPH,
                    order_index=0,
                    text=page.get_text("text") or "",
                    metadata={"fallback": True, "anchor": self._block_anchor(page_number, 0), "region_role": "text"},
                )
            )
        return payloads

    @staticmethod
    def _resolve_block_type(block: Any, text_fragment: str) -> str:
        stripped = (text_fragment or "").strip()
        if not stripped:
            return KnowledgeBlockType.IMAGE

        # Strong table signals: pipes / tabs / multi-spaces in the first line
        first_line = stripped.splitlines()[0] if "\n" in stripped else stripped
        looks_tabular = ("|" in first_line) or ("\t" in first_line) or bool(re.search(r"\s{2,}", first_line))
        if looks_tabular:
            return KnowledgeBlockType.TABLE

        # Headings: short and mostly uppercase or trailing colon
        if stripped.endswith(":"):
            return KnowledgeBlockType.HEADING
        if stripped.isupper() and len(stripped) < 80:
            return KnowledgeBlockType.HEADING

        # Default
        return KnowledgeBlockType.PARAGRAPH

    @staticmethod
    def _looks_like_heading(content: str) -> bool:
        if not content:
            return False
        stripped = content.strip()
        if len(stripped) > 80:
            return False
        if stripped.endswith(":"):
            return True
        uppercase_ratio = sum(1 for c in stripped if c.isupper()) / max(len(stripped), 1)
        return uppercase_ratio > 0.6
