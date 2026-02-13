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
    def _table(self, *, detected_via: str, order_index: int) -> TablePayload:
        header = TableRowPayload(
            row_index=0,
            page_number=1,
            raw_text="service | price",
            metadata={"row_type": "header"},
            cells=[
                TableCellPayload(row_index=0, column_index=0, column_key="service", raw_text="Service"),
                TableCellPayload(row_index=0, column_index=1, column_key="price", raw_text="Price"),
            ],
        )
        data = TableRowPayload(
            row_index=1,
            page_number=1,
            raw_text="A | 10",
            metadata={"row_type": "data"},
            cells=[
                TableCellPayload(row_index=1, column_index=0, column_key="service", raw_text="A"),
                TableCellPayload(row_index=1, column_index=1, column_key="price", raw_text="10"),
            ],
        )
        return TablePayload(
            order_index=order_index,
            title=f"Table {order_index}",
            section_heading="",
            page_number=1,
            bbox={"x0": 1.0, "y0": 1.0, "x1": 10.0, "y1": 10.0},
            column_schema=["service", "price"],
            data_dictionary={},
            metadata={"detected_via": detected_via},
            rows=[header, data],
        )

    def test_candidate_scorer_selector_exposes_selection_mode(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        selected, _tables, meta = service._select_table_candidates(
            {"geometry": [self._table(detected_via="geometry", order_index=1)]}
        )
        self.assertEqual(selected, "geometry")
        self.assertEqual(meta.get("selection_mode"), "candidate_scorer_v2")

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
