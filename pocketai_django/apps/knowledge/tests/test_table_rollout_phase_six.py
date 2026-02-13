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

    def test_legacy_selector_uses_deterministic_precedence(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        candidates = {
            "heuristic": [self._table(detected_via="heuristic", order_index=1)],
            "azure:layout": [self._table(detected_via="azure:layout", order_index=2)],
            "geometry": [self._table(detected_via="geometry", order_index=3)],
        }

        selected, tables, meta = service._select_table_candidates_legacy_precedence(candidates)
        self.assertEqual(selected, "azure:layout")
        self.assertEqual(len(tables), 1)
        self.assertEqual(meta.get("selection_mode"), "legacy_precedence")

    def test_candidate_scorer_selector_exposes_selection_mode(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        selected, _tables, meta = service._select_table_candidates(
            {"geometry": [self._table(detected_via="geometry", order_index=1)]}
        )
        self.assertEqual(selected, "geometry")
        self.assertEqual(meta.get("selection_mode"), "candidate_scorer_v2")

    def test_table_rollout_state_forces_global_pipeline_v2_and_reads_other_flags(self) -> None:
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
            rollout = service._table_rollout_state(upload)

        self.assertEqual(rollout.get("cohort"), "canary")
        self.assertTrue(rollout.get("pipeline_v2_enabled"))
        self.assertEqual(rollout.get("pipeline_v2_strategy"), "global_default")
        self.assertTrue(rollout.get("shadow_ingestion_enabled"))
        self.assertFalse(rollout.get("eval_logging_enabled"))
