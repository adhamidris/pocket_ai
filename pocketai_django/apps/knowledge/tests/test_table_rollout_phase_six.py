from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from apps.knowledge.knowledge_ingestion import (
    KnowledgeIngestionService,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
)


class TableRolloutPhaseSixTests(SimpleTestCase):
    def _table(
        self,
        *,
        detected_via: str,
        order_index: int,
        page_number: int = 1,
        bbox: dict[str, float] | None = None,
        structure_confidence: float | None = None,
        title: str | None = None,
    ) -> TablePayload:
        header = TableRowPayload(
            row_index=0,
            page_number=page_number,
            raw_text="service | price",
            metadata={"row_type": "header"},
            cells=[
                TableCellPayload(row_index=0, column_index=0, column_key="service", raw_text="Service"),
                TableCellPayload(row_index=0, column_index=1, column_key="price", raw_text="Price"),
            ],
        )
        data = TableRowPayload(
            row_index=1,
            page_number=page_number,
            raw_text="A | 10",
            metadata={"row_type": "data"},
            cells=[
                TableCellPayload(row_index=1, column_index=0, column_key="service", raw_text="A"),
                TableCellPayload(row_index=1, column_index=1, column_key="price", raw_text="10"),
            ],
        )
        metadata = {"detected_via": detected_via}
        if structure_confidence is not None:
            metadata["structure_confidence"] = structure_confidence
        return TablePayload(
            order_index=order_index,
            title=title or f"Table {order_index}",
            section_heading="",
            page_number=page_number,
            bbox=bbox or {"x0": 1.0, "y0": 1.0, "x1": 10.0, "y1": 10.0},
            column_schema=["service", "price"],
            data_dictionary={},
            metadata=metadata,
            rows=[header, data],
        )

    def test_candidate_scorer_selector_exposes_scored_selection_mode(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        selected, _tables, meta = service._select_table_candidates(
            {"geometry": [self._table(detected_via="geometry", order_index=1)]}
        )
        self.assertEqual(selected, "geometry")
        self.assertEqual(meta.get("selection_mode"), "scored_promotion_v2")
        self.assertFalse(meta.get("selector_disabled"))

    def test_auto_selection_prefers_stronger_scored_candidate(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        azure_page_one = TablePayload(
            order_index=1,
            title="Azure page 1",
            section_heading="",
            page_number=1,
            bbox={"x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 100.0},
            column_schema=["service", "price"],
            data_dictionary={},
            metadata={"detected_via": "azure_di", "structure_confidence": 0.95},
            rows=[
                TableRowPayload(
                    row_index=0,
                    page_number=1,
                    raw_text="service | price",
                    metadata={"row_type": "header"},
                    cells=[
                        TableCellPayload(row_index=0, column_index=0, column_key="service", raw_text="Service"),
                        TableCellPayload(row_index=0, column_index=1, column_key="price", raw_text="Price"),
                    ],
                ),
                TableRowPayload(
                    row_index=1,
                    page_number=1,
                    raw_text="Row A | 10",
                    metadata={"row_type": "data"},
                    cells=[
                        TableCellPayload(row_index=1, column_index=0, column_key="service", raw_text="Row A"),
                        TableCellPayload(row_index=1, column_index=1, column_key="price", raw_text="10"),
                    ],
                ),
            ],
        )
        geometry_page_one = TablePayload(
            order_index=1,
            title="Geometry page 1",
            section_heading="",
            page_number=1,
            bbox={"x0": 2.0, "y0": 2.0, "x1": 102.0, "y1": 102.0},
            column_schema=["service", "price"],
            data_dictionary={},
            metadata={"detected_via": "geometry", "structure_confidence": 0.55},
            rows=[
                TableRowPayload(
                    row_index=0,
                    page_number=1,
                    raw_text="service | price",
                    metadata={"row_type": "header"},
                    cells=[
                        TableCellPayload(row_index=0, column_index=0, column_key="service", raw_text="Service"),
                        TableCellPayload(row_index=0, column_index=1, column_key="price", raw_text="Price"),
                    ],
                ),
                TableRowPayload(
                    row_index=1,
                    page_number=1,
                    raw_text="Row A | 10",
                    metadata={"row_type": "data"},
                    cells=[
                        TableCellPayload(row_index=1, column_index=0, column_key="service", raw_text="Row A"),
                        TableCellPayload(row_index=1, column_index=1, column_key="price", raw_text="10"),
                    ],
                ),
            ],
        )
        geometry_page_two = TablePayload(
            order_index=2,
            title="Geometry page 2",
            section_heading="",
            page_number=2,
            bbox={"x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 100.0},
            column_schema=["service", "price"],
            data_dictionary={},
            metadata={"detected_via": "geometry", "structure_confidence": 0.9},
            rows=[
                TableRowPayload(
                    row_index=0,
                    page_number=2,
                    raw_text="service | price",
                    metadata={"row_type": "header"},
                    cells=[
                        TableCellPayload(row_index=0, column_index=0, column_key="service", raw_text="Service"),
                        TableCellPayload(row_index=0, column_index=1, column_key="price", raw_text="Price"),
                    ],
                ),
                TableRowPayload(
                    row_index=1,
                    page_number=2,
                    raw_text="Row B | 20",
                    metadata={"row_type": "data"},
                    cells=[
                        TableCellPayload(row_index=1, column_index=0, column_key="service", raw_text="Row B"),
                        TableCellPayload(row_index=1, column_index=1, column_key="price", raw_text="20"),
                    ],
                ),
            ],
        )

        candidates = {
            "geometry": [geometry_page_one, geometry_page_two],
            "azure:layout": [azure_page_one],
        }

        def _score_table_set(tables: list[TablePayload]) -> float:
            if tables is candidates["azure:layout"]:
                return 10.0
            return 5.0

        with mock.patch.object(service, "_score_table_set", side_effect=_score_table_set):
            selected, tables, meta = service._select_table_candidates(candidates)

        self.assertEqual(selected, "azure:layout")
        self.assertEqual(meta.get("selection_mode"), "scored_promotion_v2")
        self.assertFalse(meta.get("selector_disabled"))
        self.assertEqual([table.page_number for table in tables], [1])
        self.assertEqual([table.title for table in tables], ["Azure page 1"])

    def test_region_blend_does_not_promote_nested_heuristic_fragment(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        azure = self._table(
            detected_via="azure_di",
            order_index=1,
            page_number=1,
            bbox={"x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 100.0},
            structure_confidence=0.95,
            title="Azure canonical",
        )
        heuristic = self._table(
            detected_via="heuristic",
            order_index=2,
            page_number=1,
            bbox={"x0": 60.0, "y0": 60.0, "x1": 70.0, "y1": 70.0},
            structure_confidence=0.9,
            title="Nested heuristic",
        )

        selected, tables, meta = service._select_table_candidates(
            {
                "azure:layout": [azure],
                "heuristic": [heuristic],
            }
        )

        self.assertEqual(selected, "azure:layout")
        self.assertEqual(meta.get("selection_mode"), "scored_promotion_v2")
        self.assertFalse(meta.get("selector_disabled"))
        self.assertEqual([table.title for table in tables], ["Azure canonical"])

    def test_native_text_selection_prefers_collapsed_pdfplumber_when_azure_readability_is_poor(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)

        pdfplumber_table = TablePayload(
            order_index=1,
            title="Collapsed factual table",
            section_heading="",
            page_number=1,
            bbox={"x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 100.0},
            column_schema=["column_1"],
            data_dictionary={},
            metadata={"detected_via": "pdfplumber:lines_text", "structure_confidence": 0.35},
            rows=[
                TableRowPayload(
                    row_index=1,
                    page_number=1,
                    raw_text="Issuance and Renewal Fees EGP 500 EGP 250 EGP 300 EGP 350",
                    metadata={"row_type": "data"},
                    cells=[
                        TableCellPayload(
                            row_index=1,
                            column_index=0,
                            column_key="column_1",
                            raw_text="Issuance and Renewal Fees EGP 500 EGP 250 EGP 300 EGP 350",
                        )
                    ],
                ),
                TableRowPayload(
                    row_index=2,
                    page_number=1,
                    raw_text="Replacement Fees Free Free Free Free",
                    metadata={"row_type": "data"},
                    cells=[
                        TableCellPayload(
                            row_index=2,
                            column_index=0,
                            column_key="column_1",
                            raw_text="Replacement Fees Free Free Free Free",
                        )
                    ],
                ),
                TableRowPayload(
                    row_index=3,
                    page_number=1,
                    raw_text="Supplementary Fees EGP 25 Free Free Free",
                    metadata={"row_type": "data"},
                    cells=[
                        TableCellPayload(
                            row_index=3,
                            column_index=0,
                            column_key="column_1",
                            raw_text="Supplementary Fees EGP 25 Free Free Free",
                        )
                    ],
                ),
            ],
        )
        azure_table = TablePayload(
            order_index=2,
            title="Azure layout table",
            section_heading="",
            page_number=1,
            bbox={"x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 100.0},
            column_schema=["card_typu", "white", "classic", "gold"],
            data_dictionary={},
            metadata={"detected_via": "azure:layout", "structure_confidence": 0.95},
            rows=[
                TableRowPayload(
                    row_index=0,
                    page_number=1,
                    raw_text="Card Typu | White | Classic | Gold",
                    metadata={"row_type": "header"},
                    cells=[
                        TableCellPayload(row_index=0, column_index=0, column_key="card_typu", raw_text="Card Typu"),
                        TableCellPayload(row_index=0, column_index=1, column_key="white", raw_text="White"),
                        TableCellPayload(row_index=0, column_index=2, column_key="classic", raw_text="Classic"),
                        TableCellPayload(row_index=0, column_index=3, column_key="gold", raw_text="Gold"),
                    ],
                ),
                TableRowPayload(
                    row_index=1,
                    page_number=1,
                    raw_text="Ruplacimarz Fossi | Frist | Frist | Frist",
                    metadata={"row_type": "data"},
                    cells=[
                        TableCellPayload(row_index=1, column_index=0, column_key="card_typu", raw_text="Ruplacimarz Fossi"),
                        TableCellPayload(row_index=1, column_index=1, column_key="white", raw_text="Frist"),
                        TableCellPayload(row_index=1, column_index=2, column_key="classic", raw_text="Frist"),
                        TableCellPayload(row_index=1, column_index=3, column_key="gold", raw_text="Frist"),
                    ],
                ),
            ],
        )

        def _assessment(table: TablePayload) -> dict[str, object]:
            if table is pdfplumber_table:
                return {
                    "quality_score": 0.78,
                    "signals": {
                        "effective_column_count": 1,
                        "structured_row_ratio": 1.0,
                        "placeholder_cell_ratio": 0.0,
                        "header_confidence": 0.55,
                        "paragraph_like_table": True,
                    },
                }
            return {
                "quality_score": 0.83,
                "signals": {
                    "effective_column_count": 4,
                    "structured_row_ratio": 0.75,
                    "header_confidence": 0.2,
                    "row_misalignment": True,
                    "fragmented_logical_rows": True,
                },
            }

        candidates = {
            "pdfplumber:lines_text": [pdfplumber_table],
            "azure:layout": [azure_table],
        }
        with mock.patch.object(service, "_assess_table_quality", side_effect=_assessment):
            native_selected, _tables, native_meta = service._select_table_candidates(
                candidates,
                selection_context={"format_hint": "pdf", "native_text_pdf": True},
            )
            fallback_selected, _tables, _fallback_meta = service._select_table_candidates(
                candidates,
                selection_context={"format_hint": "pdf", "native_text_pdf": False},
            )

        self.assertEqual(native_selected, "pdfplumber:lines_text")
        self.assertEqual(fallback_selected, "azure:layout")
        self.assertTrue((native_meta.get("selection_context") or {}).get("native_text_pdf"))

    def test_native_text_selection_penalizes_azure_oversegmentation(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)

        pdfplumber_tables = [
            self._table(
                detected_via="pdfplumber:lines",
                order_index=index,
                title=f"pdfplumber {index}",
                structure_confidence=0.45,
            )
            for index in range(1, 6)
        ]
        azure_tables = [
            self._table(
                detected_via="azure:layout",
                order_index=index,
                title=f"azure {index}",
                structure_confidence=0.75,
            )
            for index in range(11, 21)
        ]

        assessments: dict[int, dict[str, object]] = {}
        for table in pdfplumber_tables:
            assessments[table.order_index] = {
                "quality_score": 0.86,
                "signals": {
                    "effective_column_count": 1,
                    "structured_row_ratio": 1.0,
                    "placeholder_cell_ratio": 0.0,
                    "header_confidence": 0.55,
                    "paragraph_like_table": True,
                },
            }
        for table in azure_tables:
            assessments[table.order_index] = {
                "quality_score": 0.75,
                "signals": {
                    "effective_column_count": 4,
                    "structured_row_ratio": 0.72,
                    "header_confidence": 0.82,
                },
            }

        def _score_table_set(tables: list[TablePayload]) -> float:
            if tables is azure_tables:
                return 10.2602
            return 8.7315

        with (
            mock.patch.object(service, "_assess_table_quality", side_effect=lambda table: assessments[table.order_index]),
            mock.patch.object(service, "_score_table_set", side_effect=_score_table_set),
        ):
            selected, _tables, meta = service._select_table_candidates(
                {
                    "pdfplumber:lines": pdfplumber_tables,
                    "azure:layout": azure_tables,
                },
                selection_context={"format_hint": "pdf", "native_text_pdf": True},
            )

        self.assertEqual(selected, "pdfplumber:lines")
        self.assertGreater(
            float((meta.get("rank_scores") or {}).get("pdfplumber:lines") or 0.0),
            float((meta.get("rank_scores") or {}).get("azure:layout") or 0.0),
        )

    def test_native_text_route_prefers_pdfplumber_primary_chain_before_azure_fallback(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)

        pdfplumber_lines = [self._table(detected_via="pdfplumber:lines", order_index=index) for index in range(1, 6)]
        pdfplumber_lines_text = [
            self._table(detected_via="pdfplumber:lines_text", order_index=index)
            for index in range(11, 16)
        ]
        azure_tables = [self._table(detected_via="azure:layout", order_index=index) for index in range(21, 31)]

        def _select(candidates, selection_context=None):
            name = next(iter(candidates.keys()))
            if name == "pdfplumber:lines":
                return (
                    "pdfplumber:lines",
                    pdfplumber_lines,
                    {
                        "scores": {"pdfplumber:lines": 8.7},
                        "quality_diagnostics": {
                            "pdfplumber:lines": {
                                "table_count": 5,
                                "avg_quality_score": 0.85,
                                "avg_readability_score": 0.91,
                                "collapsed_factual_count": 3,
                            }
                        },
                    },
                )
            if name == "pdfplumber:lines_text":
                return (
                    "pdfplumber:lines_text",
                    pdfplumber_lines_text,
                    {
                        "scores": {"pdfplumber:lines_text": 7.9},
                        "quality_diagnostics": {
                            "pdfplumber:lines_text": {
                                "table_count": 5,
                                "avg_quality_score": 0.72,
                                "avg_readability_score": 0.84,
                                "collapsed_factual_count": 2,
                            }
                        },
                    },
                )
            return (
                "azure:layout",
                azure_tables,
                {
                    "scores": {"azure:layout": 10.2},
                    "quality_diagnostics": {
                        "azure:layout": {
                            "table_count": 10,
                            "avg_quality_score": 0.75,
                            "avg_readability_score": 0.85,
                            "collapsed_factual_count": 0,
                        }
                    },
                },
            )

        with mock.patch.object(service, "_select_table_candidates", side_effect=_select):
            selected, tables, meta = service._route_pdf_table_candidates(
                {
                    "pdfplumber:lines": pdfplumber_lines,
                    "pdfplumber:lines_text": pdfplumber_lines_text,
                    "azure:layout": azure_tables,
                },
                selection_context={"format_hint": "pdf", "native_text_pdf": True, "pdf_lane": "native_text"},
            )

        self.assertEqual(selected, "pdfplumber:lines")
        self.assertEqual(tables, pdfplumber_lines)
        self.assertEqual((meta.get("route") or {}).get("pdf_lane"), "native_text")
        self.assertFalse((meta.get("route") or {}).get("fallback_triggered"))
        self.assertEqual((meta.get("route") or {}).get("primary_selected"), "pdfplumber:lines")

    def test_native_text_route_falls_back_to_azure_when_pdfplumber_chain_is_unacceptable(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)

        pdfplumber_lines = [self._table(detected_via="pdfplumber:lines", order_index=1)]
        pdfplumber_lines_text = [self._table(detected_via="pdfplumber:lines_text", order_index=2)]
        azure_tables = [self._table(detected_via="azure:layout", order_index=index) for index in range(21, 31)]

        def _select(candidates, selection_context=None):
            name = next(iter(candidates.keys()))
            if name.startswith("pdfplumber"):
                return (
                    name,
                    candidates[name],
                    {
                        "scores": {name: 1.0},
                        "quality_diagnostics": {
                            name: {
                                "table_count": len(candidates[name]),
                                "avg_quality_score": 0.2,
                                "avg_readability_score": 0.4,
                                "collapsed_factual_count": 0,
                            }
                        },
                    },
                )
            return (
                "azure:layout",
                azure_tables,
                {
                    "scores": {"azure:layout": 10.2},
                    "quality_diagnostics": {
                        "azure:layout": {
                            "table_count": 10,
                            "avg_quality_score": 0.75,
                            "avg_readability_score": 0.85,
                            "collapsed_factual_count": 0,
                        }
                    },
                },
            )

        with mock.patch.object(service, "_select_table_candidates", side_effect=_select):
            selected, tables, meta = service._route_pdf_table_candidates(
                {
                    "pdfplumber:lines": pdfplumber_lines,
                    "pdfplumber:lines_text": pdfplumber_lines_text,
                    "azure:layout": azure_tables,
                },
                selection_context={"format_hint": "pdf", "native_text_pdf": True, "pdf_lane": "native_text"},
            )

        self.assertEqual(selected, "azure:layout")
        self.assertEqual(tables, azure_tables)
        self.assertTrue((meta.get("route") or {}).get("fallback_triggered"))
        self.assertEqual((meta.get("route") or {}).get("fallback_selected"), "azure:layout")
        self.assertEqual(
            (meta.get("route") or {}).get("primary_attempts"),
            [
                {
                    "candidate": "pdfplumber:lines",
                    "selected": "pdfplumber:lines",
                    "acceptable": False,
                    "reason": "readability_below_threshold",
                },
                {
                    "candidate": "pdfplumber:lines_text",
                    "selected": "pdfplumber:lines_text",
                    "acceptable": False,
                    "reason": "readability_below_threshold",
                },
            ],
        )

    def test_table_runtime_flags_report_shadow_and_eval_states(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        upload = SimpleNamespace(
            business_profile=SimpleNamespace(
                metadata={"cohort": "canary"},
            )
        )
        mocked_state = SimpleNamespace(
            rag_shadow_ingestion=True,
            rag_eval_logging=False,
        )
        with mock.patch(
            "apps.knowledge.knowledge_ingestion.FeatureFlagService.snapshot",
            return_value=mocked_state,
        ):
            runtime_flags = service._table_runtime_flags(upload)

        self.assertEqual(runtime_flags.get("cohort"), "canary")
        self.assertTrue(runtime_flags.get("shadow_ingestion_enabled"))
        self.assertFalse(runtime_flags.get("eval_logging_enabled"))
