from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

from django.utils import timezone

from apps.accounts.models import KnowledgeBlockType, KnowledgeIssueSeverity, KnowledgeSourceType
from apps.core.logging_utils import LogEmoji, log_start, log_success
from apps.knowledge.ingestion.azure_di import AzureDocumentIntelligenceExtractor
from apps.knowledge.ingestion.contracts import (
    ExtractionResult,
    IssuePayload,
    KnowledgeIngestionError,
    PageBlockPayload,
    PageLayout,
    PageRendererResult,
    PdfSpan,
    TablePayload,
    UnsupportedFormatError,
)
from apps.knowledge.tables.geometry_tools.reconstructor import GeometryTableReconstructor
from apps.knowledge.ingestion.pdfplumber import PdfPlumberTableExtractor
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadFile,
    KnowledgeUploadText,
)


logger = logging.getLogger(__name__)

try:  # pragma: no cover - dependency failure should be surfaced at runtime
    import fitz  # type: ignore[attr-defined]  # PyMuPDF
except ImportError:  # pragma: no cover - optional dependency
    fitz = None  # type: ignore


class IngestionExtractionMixin:


    # ------------------------------------------------------------------
    # Extraction path

    def _extract_upload(self, upload: KnowledgeUpload) -> ExtractionResult:
        if upload.source_type in {KnowledgeSourceType.FILE, KnowledgeSourceType.INTEGRATION}:
            file_detail = getattr(upload, "file_detail", None)
            if not isinstance(file_detail, KnowledgeUploadFile):
                upload = KnowledgeUpload.objects.select_related("file_detail").get(id=upload.id)
                file_detail = upload.file_detail
            if file_detail is None:
                raise KnowledgeIngestionError("File metadata missing for upload.")
            limit = self._json_entity_limit(upload.business_profile)
            return self._extract_from_file(file_detail, upload=upload, entity_limit=limit)

        if upload.source_type == KnowledgeSourceType.LINK:
            url_detail = getattr(upload, "url_detail", None)
            url = getattr(url_detail, "url", None) or upload.legacy_url
            if not url:
                raise KnowledgeIngestionError("Link upload missing URL.")
            return self._extract_from_link(url)

        if upload.source_type == KnowledgeSourceType.TEXT:
            text_detail = getattr(upload, "text_detail", None)
            if not isinstance(text_detail, KnowledgeUploadText):
                upload = KnowledgeUpload.objects.select_related("text_detail").get(id=upload.id)
                text_detail = upload.text_detail
            if text_detail is None:
                raise KnowledgeIngestionError("Text metadata missing for upload.")
            return self._extract_from_text(text_detail)

        raise KnowledgeIngestionError(f"Ingestion not implemented for {upload.source_type}.")

    def _extract_from_text(self, text_detail: KnowledgeUploadText) -> ExtractionResult:
        text = text_detail.content or ""
        page = PageLayout(
            page_number=1,
            width=612,
            height=792,
            rotation=0,
            text_density=len(text.strip()) / float(612 * 792) if text.strip() else 0.0,
            has_ocr_content=False,
            content_type="text/plain",
            blocks=[
                PageBlockPayload(
                    block_type=KnowledgeBlockType.PARAGRAPH,
                    order_index=0,
                    text=text,
                )
            ],
            metadata={},
        )
        return ExtractionResult(
            text=text,
            format_hint="text",
            metadata={"content_type": "text/plain"},
            pages=[page],
            tables=[],
            issues=[],
        )

    def _extract_from_file(
        self,
        file_detail: KnowledgeUploadFile,
        *,
        upload: KnowledgeUpload | None = None,
        entity_limit: int | None = None,
    ) -> ExtractionResult:
        
        storage_path = Path(file_detail.storage_path)
        absolute = (self.media_root / storage_path).resolve()
        try:
            absolute.relative_to(self.media_root)
        except ValueError as exc:  # pragma: no cover - defensive
            raise KnowledgeIngestionError("File path escapes MEDIA_ROOT.") from exc
        if not absolute.exists():
            raise KnowledgeIngestionError("File not found on disk.")

        format_hint = self._detect_format(file_detail)
        if not format_hint:
            raise UnsupportedFormatError(f"Unsupported file type {format_hint or 'unknown'}.")
        logger.info("extract.file.start path=%s format=%s", absolute, format_hint)
        # New emoji-enhanced logging
        log_start(
            logger,
            "EXTRACT",
            f"File: {Path(absolute).name}",
            {"format": format_hint, "path": str(absolute)},
            emoji=LogEmoji.DOCUMENT,
        )
        if format_hint == "jsonl":
            return self._extract_jsonl_dataset(absolute, file_detail=file_detail, upload=upload)
        if format_hint == "json":
            limit = entity_limit or self.default_json_entity_limit
            return self._extract_json(absolute, entity_limit=limit, upload=upload)
        if format_hint in {"csv", "tsv"}:
            return self._extract_csv(
                absolute,
                format_hint=format_hint,
                file_detail=file_detail,
                upload=upload,
            )
        if format_hint == "xlsx":
            return self._extract_xlsx(
                absolute,
                file_detail=file_detail,
                upload=upload,
            )
        if format_hint == "xls":
            return self._extract_xls(
                absolute,
                file_detail=file_detail,
                upload=upload,
            )
        layout_result: PageRendererResult | None = None
        should_attempt_layout = not (format_hint == "pdf" and fitz is None)
        if should_attempt_layout:
            try:
                layout_result = self.page_renderer.render(
                    absolute,
                    format_hint=format_hint,
                    ocr=self.ocr_reconciler,
                )
                logger.info("extract.file.result path=%s format=%s pages=%s", absolute, format_hint, len(layout_result.pages))
                # New emoji-enhanced logging
                log_success(
                    logger,
                    "EXTRACTED",
                    f"{len(layout_result.pages)} pages from {format_hint.upper()}",
                    {"path": Path(absolute).name},
                    emoji=LogEmoji.DOCUMENT,
                )
            except KnowledgeIngestionError as exc:
                
                logger.warning("Layout extraction failed for %s: %s", absolute, exc)

        if layout_result is None:
            text = self._fallback_text_extraction(absolute, format_hint)
            layout_result = PageRendererResult(
                text=text,
                pages=[
                    PageLayout(
                        page_number=1,
                        width=612,
                        height=792,
                        rotation=0,
                        text_density=len(text.strip()) / (612 * 792),
                        has_ocr_content=False,
                        content_type=f"application/{format_hint}",
                        blocks=[
                            PageBlockPayload(
                                block_type=KnowledgeBlockType.PARAGRAPH,
                                order_index=0,
                                text=text,
                            )
                        ],
                        metadata={"fallback": True},
                    )
                ],
            )
        text = layout_result.text
        page_count = len(layout_result.pages)
        if page_count and int(getattr(file_detail, "page_count", 0) or 0) != page_count:
            KnowledgeUploadFile.objects.filter(id=file_detail.id).update(page_count=page_count, updated_at=timezone.now())
            file_detail.page_count = page_count

        # Geometry-based reconstruction (PDF only, when PyMuPDF available)
        geometry_tables: list[TablePayload] = []
        geom_issues: list[IssuePayload] = []
        page_spans: list[list[PdfSpan]] = []
        if format_hint == "pdf" and fitz is not None:
            try:
                page_spans = self.page_renderer.extract_pdf_spans(absolute)
                for idx, spans in enumerate(page_spans, start=1):
                    logger.info("geometry.spans page=%s count=%s", idx, len(spans))
                recon = GeometryTableReconstructor()
                geometry_tables, geom_issues = recon.reconstruct(page_spans, layout_result.pages)
                logger.info("geometry.tables path=%s count=%s", absolute, len(geometry_tables))
                # New emoji-enhanced logging
                if geometry_tables:
                    log_success(
                        logger,
                        "TABLES DETECTED",
                        f"{len(geometry_tables)} tables found",
                        {"source": "geometry"},
                        emoji=LogEmoji.TABLE,
                    )
            except Exception as exc:  # best-effort guard
                logger.warning("geometry.reconstruct_failed path=%s err=%s", absolute, exc)
                geometry_tables, geom_issues = [], [
                    IssuePayload(
                        code="geometry_failed",
                        severity=KnowledgeIssueSeverity.ERROR.value,
                        description=str(exc),
                    )
                ]

        pdfplumber_candidates: dict[str, list[TablePayload]] = {}
        pdfplumber_issues: list[IssuePayload] = []
        pdfplumber_meta: dict[str, Any] = {}
        if format_hint == "pdf" and self.pdfplumber_enabled:
            extractor = PdfPlumberTableExtractor(table_settings=self.pdfplumber_table_settings)
            pdfplumber_candidates, pdfplumber_issues, pdfplumber_meta = extractor.extract_candidates(absolute)

        azure_tables: list[TablePayload] = []
        azure_issues: list[IssuePayload] = []
        azure_meta: dict[str, Any] = {}
        if format_hint == "pdf" and self.azure_di_enabled:
            azure_extractor = AzureDocumentIntelligenceExtractor(
                endpoint=self.azure_di_endpoint,
                key=self.azure_di_key,
                model=self.azure_di_model,
                api_version=self.azure_di_api_version,
                base_path=self.azure_di_base_path,
                locale=self.azure_di_locale,
                timeout_seconds=self.azure_di_timeout_seconds,
                poll_interval_seconds=self.azure_di_poll_interval_seconds,
                max_polls=self.azure_di_max_polls,
                request_max_attempts=self.azure_di_request_max_attempts,
                poll_request_max_attempts=self.azure_di_poll_request_max_attempts,
                retry_backoff_base_seconds=self.azure_di_retry_backoff_base_seconds,
                retry_backoff_max_seconds=self.azure_di_retry_backoff_max_seconds,
                max_retry_after_seconds=self.azure_di_max_retry_after_seconds,
            )
            azure_tables, azure_issues, azure_meta = azure_extractor.extract_tables(absolute)

        docx_tables: list[TablePayload] = []
        docx_issues: list[IssuePayload] = []
        docx_meta: dict[str, Any] = {}
        if format_hint == "docx":
            docx_tables, docx_issues, docx_meta = self._extract_docx_table_candidates(
                absolute,
                filename=file_detail.filename,
            )

        heuristic_tables, table_issues = self.table_detector.detect_tables(layout_result.pages)
        filtered_heuristics, suppress_issues = self._suppress_list_like_heuristics(heuristic_tables)
        if len(filtered_heuristics) != len(heuristic_tables):
            logger.info(
                "heuristic.suppressed_list_like_tables before=%s after=%s",
                len(heuristic_tables),
                len(filtered_heuristics),
            )

        candidates: dict[str, list[TablePayload]] = {}
        candidates.update(pdfplumber_candidates)
        if azure_tables:
            candidates["azure:layout"] = azure_tables
        if docx_tables:
            candidates["docx:table_xml"] = docx_tables
        if geometry_tables:
            candidates["geometry"] = geometry_tables
        if filtered_heuristics:
            candidates["heuristic"] = filtered_heuristics

        table_runtime_flags = self._table_runtime_flags(upload)
        selection_context = self._build_table_selection_context(
            format_hint=format_hint,
            pages=layout_result.pages,
        )
        if format_hint == "pdf":
            selected_extractor, tables, selection_meta = self._route_pdf_table_candidates(
                candidates,
                selection_context=selection_context,
            )
        else:
            selected_extractor, tables, selection_meta = self._select_table_candidates(
                candidates,
                selection_context=selection_context,
            )
        issues = (
            layout_result.issues
            + table_issues
            + geom_issues
            + suppress_issues
            + pdfplumber_issues
            + azure_issues
            + docx_issues
        )

        structural_repair_meta: dict[str, Any] = {}
        if format_hint == "pdf" and tables:
            tables, structural_repair_meta, structural_repair_issues = self._repair_pdf_table_structure(
                tables,
                candidates=candidates,
                page_spans=page_spans,
                selected_extractor=selected_extractor,
                selection_context=selection_context,
            )
            issues.extend(structural_repair_issues)

        repair_meta: dict[str, Any] = {}
        if tables:
            tables, repair_issues, repair_meta = self._repair_tables_with_vlm(
                absolute,
                tables,
            )
            issues.extend(repair_issues)
            # VLM repair replaces entire TablePayload objects whose rows lack
            # applicability metadata.  Re-run annotation so VLM-repaired tables
            # get the same sparse-row / span enrichment as Azure DI tables.
            if repair_meta.get("repaired"):
                # _annotate_row_applicability lives on the extractor class;
                # create a lightweight instance (no API calls are made).
                _applicability_annotator = AzureDocumentIntelligenceExtractor(
                    endpoint=None, key=None,
                )
                for idx, table in enumerate(tables):
                    detected_via = str((table.metadata or {}).get("detected_via") or "")
                    if "vlm" not in detected_via:
                        continue
                    header_rows: set[int] = set()
                    for row in (table.rows or []):
                        if (row.metadata or {}).get("row_type") == "header":
                            header_rows.add(row.row_index)
                    annotated_rows = _applicability_annotator._annotate_row_applicability(
                        table_rows=table.rows or [],
                        column_schema=table.column_schema or [],
                        header_rows=header_rows,
                    )
                    tables[idx] = TablePayload(
                        order_index=table.order_index,
                        title=table.title,
                        section_heading=table.section_heading,
                        page_number=table.page_number,
                        bbox=table.bbox,
                        column_schema=table.column_schema,
                        data_dictionary=table.data_dictionary,
                        metadata=table.metadata,
                        rows=annotated_rows,
                    )

        chunking_pages = list(layout_result.pages)

        # Canonical table reconstruction (ingestion-time):
        # - reconstruct gridless pseudo-tables from aligned page blocks
        # - attach orphan/residual blocks into the most likely cell
        canonical_reconstruction_meta: dict[str, Any] = {}
        if format_hint == "pdf" and chunking_pages:
            try:
                from apps.knowledge.tables.canonical.reconstruction import CanonicalTableReconstructor

                reconstructor = CanonicalTableReconstructor(
                    PageLayout=PageLayout,
                    PageBlockPayload=PageBlockPayload,
                    TablePayload=TablePayload,
                    TableRowPayload=TableRowPayload,
                    TableCellPayload=TableCellPayload,
                )
                chunking_pages, tables, recon_meta, _recon_issues = reconstructor.run(
                    pages=chunking_pages,
                    tables=tables,
                )
                if recon_meta.reconstructed_tables or recon_meta.attached_blocks:
                    canonical_reconstruction_meta = {
                        "reconstructed_tables": recon_meta.reconstructed_tables,
                        "reconstructed_rows": recon_meta.reconstructed_rows,
                        "attached_blocks": recon_meta.attached_blocks,
                        "attached_cells": recon_meta.attached_cells,
                        "consumed_blocks": recon_meta.consumed_blocks,
                        "modified_tables": recon_meta.modified_tables,
                        "details": recon_meta.details,
                    }
            except Exception as exc:
                logger.warning("canonical_table_reconstruction.failed upload=%s err=%s", upload.id, exc)

        postprocess_meta: dict[str, Any] = {}
        if tables:
            tables, postprocess_issues, postprocess_meta = self._postprocess_tables(tables)
            issues.extend(postprocess_issues)

        table_promotion_meta: dict[str, Any] = {}
        if format_hint == "pdf" and tables:
            tables, table_promotion_meta, promotion_issues = self._apply_pdf_table_promotion_gate(tables)
            issues.extend(promotion_issues)
            chunking_pages, restored_block_meta = self._restore_consumed_blocks_for_suppressed_tables(
                chunking_pages,
                tables,
            )
            if restored_block_meta.get("restored_blocks"):
                table_promotion_meta = dict(table_promotion_meta)
                table_promotion_meta["restored_consumed_blocks"] = restored_block_meta

        ingest_config = self._table_ingest_config(upload)
        tables, table_metrics, limit_issues, table_summary = self._apply_table_limits(
            tables,
            upload=upload,
            config=ingest_config,
        )
        issues = issues + limit_issues

        table_text_overlap_filter_meta: dict[str, Any] = {}
        if (
            format_hint == "pdf"
            and self.pdf_table_text_overlap_filter_enabled
            and tables
            and chunking_pages
        ):
            chunking_pages, table_text_overlap_filter_meta = self._annotate_pdf_blocks_with_table_overlap(
                chunking_pages,
                tables,
            )

        pdf_baseline_metrics: dict[str, Any] = {}
        if format_hint == "pdf":
            pdf_baseline_metrics = self._build_pdf_table_baseline_metrics(
                chunking_pages,
                tables,
                overlap_diagnostics=table_text_overlap_filter_meta,
            )

        # PDFs are documents (not datasets). Indexing per-row "table entities" from a PDF tends to
        # flood retrieval with low-context chunks (e.g. `Table_1: ...`) and mislead downstream
        # prompting into "dataset-like" behavior. We keep structured table artifacts, but skip
        # row-entity generation for PDFs.
        if (format_hint or "").lower() == "pdf":
            table_entities: list[dict[str, Any]] = []
        else:
            table_entities = self._table_row_entities(
                tables,
                business_profile=getattr(upload, "business_profile", None),
                upload=upload,
            )
        table_stats = self._table_stats_summary(
            total_rows=table_summary["total_rows"],
            indexed_rows=table_summary["indexed_rows"],
            row_cap=table_summary.get("row_cap_hint"),
            source_row_count=self._integration_row_count(upload),
            table_count=len(tables),
            partial_tables=table_summary.get("partial_tables", 0),
            row_tier=table_summary.get("row_tier_hint"),
        )
        metadata = {
            "format": format_hint,
            "filename": file_detail.filename,
            "content_type": file_detail.content_type or "",
            "storage_path": file_detail.storage_path,
            "page_count": len(chunking_pages),
            "table_count": len(tables),
            "table_truncation": table_metrics,
            "table_stats": table_stats,
        }
        if format_hint == "pdf":
            extraction_meta = {
                "selected_extractor": selected_extractor,
                "candidate_counts": {key: len(val) for key, val in candidates.items()},
                "candidate_scores": selection_meta.get("scores", {}),
                "candidate_rank_scores": selection_meta.get("rank_scores", {}),
            }
            extraction_meta["selection_mode"] = str(
                selection_meta.get("selection_mode") or self.table_selection_mode or "scored_promotion_v2"
            )
            extraction_meta["runtime_flags"] = table_runtime_flags
            candidate_metrics = selection_meta.get("metrics")
            if isinstance(candidate_metrics, Mapping):
                extraction_meta["candidate_metrics"] = candidate_metrics
            candidate_quality = selection_meta.get("quality_diagnostics")
            if isinstance(candidate_quality, Mapping):
                extraction_meta["candidate_quality"] = candidate_quality
            selection_context_meta = selection_meta.get("selection_context")
            if isinstance(selection_context_meta, Mapping):
                extraction_meta["selection_context"] = dict(selection_context_meta)
            route_meta = selection_meta.get("route")
            if isinstance(route_meta, Mapping):
                extraction_meta["route"] = dict(route_meta)
            selector_debug = {
                "preferred_extractor": selection_meta.get("preferred_extractor"),
                "fallback_chain": selection_meta.get("fallback_chain"),
                "selector_disabled": selection_meta.get("selector_disabled"),
                "ranked_candidates": selection_meta.get("ranked_candidates"),
                "runner_up": selection_meta.get("runner_up"),
                "score_margin_to_runner_up": selection_meta.get("score_margin_to_runner_up"),
            }
            selector_debug = {key: value for key, value in selector_debug.items() if value is not None}
            if selector_debug:
                extraction_meta["selector_debug"] = selector_debug
            if pdf_baseline_metrics:
                extraction_meta["baseline_metrics"] = pdf_baseline_metrics
            if pdfplumber_meta:
                extraction_meta["pdfplumber"] = pdfplumber_meta
            if azure_meta:
                extraction_meta["azure_di"] = azure_meta
            if repair_meta:
                extraction_meta["table_repairs"] = repair_meta
            if structural_repair_meta:
                extraction_meta["structural_repair"] = structural_repair_meta
            if postprocess_meta:
                extraction_meta["table_postprocess"] = postprocess_meta
            if table_promotion_meta:
                extraction_meta["table_promotion"] = table_promotion_meta
            if table_text_overlap_filter_meta:
                extraction_meta["table_text_overlap_filter"] = table_text_overlap_filter_meta
            if canonical_reconstruction_meta:
                extraction_meta["canonical_table_reconstruction"] = canonical_reconstruction_meta
            metadata["table_extraction"] = extraction_meta
        elif format_hint == "docx":
            extraction_meta = {
                "selected_extractor": selected_extractor,
                "candidate_counts": {key: len(val) for key, val in candidates.items()},
                "candidate_scores": selection_meta.get("scores", {}),
                "selection_mode": str(
                    selection_meta.get("selection_mode") or self.table_selection_mode or "scored_promotion_v2"
                ),
            }
            if docx_meta:
                extraction_meta["docx"] = docx_meta
            if postprocess_meta:
                extraction_meta["table_postprocess"] = postprocess_meta
            metadata["table_extraction"] = extraction_meta
        return ExtractionResult(
            text=text,
            format_hint=format_hint or "binary",
            metadata=metadata,
            pages=chunking_pages,
            tables=tables,
            issues=issues,
            entities=table_entities,
        )
