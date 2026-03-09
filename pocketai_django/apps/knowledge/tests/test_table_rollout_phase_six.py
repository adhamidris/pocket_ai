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

    def test_candidate_scorer_selector_exposes_selection_mode(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        selected, _tables, meta = service._select_table_candidates(
            {"geometry": [self._table(detected_via="geometry", order_index=1)]}
        )
        self.assertEqual(selected, "geometry")
        self.assertEqual(meta.get("selection_mode"), "candidate_scorer_v2")

    def test_auto_selection_blends_regions_across_extractors(self) -> None:
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

        selected, tables, meta = service._select_table_candidates(
            {
                "geometry": [geometry_page_one, geometry_page_two],
                "azure:layout": [azure_page_one],
            }
        )

        self.assertEqual(selected, "auto:region_blend")
        self.assertEqual(meta.get("selection_mode"), "candidate_region_blend_v1")
        self.assertTrue(meta.get("region_blend_applied"))
        self.assertEqual(meta.get("region_blend_extractors_used"), ["azure:layout", "geometry"])
        self.assertEqual([table.page_number for table in tables], [1, 2])
        self.assertEqual([table.title for table in tables], ["Azure page 1", "Geometry page 2"])

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
        self.assertEqual(meta.get("selection_mode"), "candidate_scorer_v2")
        self.assertEqual([table.title for table in tables], ["Azure canonical"])

    def test_region_blend_uses_containment_to_merge_nested_regions(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        outer = self._table(
            detected_via="azure_di",
            order_index=1,
            page_number=1,
            bbox={"x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 100.0},
            structure_confidence=0.9,
        )
        nested = self._table(
            detected_via="geometry",
            order_index=2,
            page_number=1,
            bbox={"x0": 40.0, "y0": 40.0, "x1": 50.0, "y1": 50.0},
            structure_confidence=0.7,
        )

        membership = service._table_region_membership_score(outer, nested)

        self.assertGreaterEqual(membership, 0.35)
        self.assertLess(service._table_region_overlap_ratio(outer, nested), 0.35)

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
