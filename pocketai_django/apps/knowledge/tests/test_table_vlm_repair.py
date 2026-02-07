from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from apps.knowledge.knowledge_ingestion import (
    KnowledgeIngestionService,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
)


class TableVlmRepairTests(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._media_root)

    def _service(self) -> KnowledgeIngestionService:
        return KnowledgeIngestionService(media_root=Path(self._media_root), enable_ocr=False)

    def _low_conf_geometry_table(self) -> TablePayload:
        header = TableRowPayload(
            row_index=0,
            page_number=1,
            bbox={},
            raw_text="Fee",
            metadata={"row_type": "header"},
            cells=[
                TableCellPayload(
                    row_index=0,
                    column_index=0,
                    column_key="column_1",
                    raw_text="Fee",
                )
            ],
        )
        rows = [
            TableRowPayload(
                row_index=1,
                page_number=1,
                bbox={},
                raw_text="Annual fee",
                metadata={"row_type": "data"},
                cells=[
                    TableCellPayload(
                        row_index=1,
                        column_index=0,
                        column_key="column_1",
                        raw_text="Annual fee",
                    )
                ],
            ),
            TableRowPayload(
                row_index=2,
                page_number=1,
                bbox={},
                raw_text="Late fee",
                metadata={"row_type": "data"},
                cells=[
                    TableCellPayload(
                        row_index=2,
                        column_index=0,
                        column_key="column_1",
                        raw_text="Late fee",
                    )
                ],
            ),
        ]
        return TablePayload(
            order_index=1,
            title="Fees",
            section_heading="Pricing",
            page_number=1,
            bbox={"x0": 10.0, "y0": 20.0, "x1": 200.0, "y1": 260.0},
            column_schema=["column_1"],
            data_dictionary={},
            metadata={"detected_via": "geometry"},
            rows=[header, *rows],
        )

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_vlm_repair_applies_to_non_azure_tables_when_structure_confidence_low(self, _build_embeddings) -> None:
        service = self._service()
        table = self._low_conf_geometry_table()

        vlm_payload = {
            "columns": ["Fee", "Amount"],
            "rows": [["Annual fee", "100"], ["Late fee", "50"]],
        }

        with (
            mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=False),
            mock.patch("openai.OpenAI"),
            mock.patch.object(KnowledgeIngestionService, "_render_table_crop", return_value=b"png"),
            mock.patch.object(KnowledgeIngestionService, "_run_vlm_table_repair", return_value=vlm_payload),
        ):
            repaired, issues, meta = service._repair_tables_with_vlm(Path("dummy.pdf"), [table])

        self.assertEqual(meta.get("attempted"), 1)
        self.assertEqual(meta.get("repaired"), 1)
        self.assertEqual(len(issues), 0)
        self.assertEqual(len(repaired), 1)
        self.assertEqual(repaired[0].section_heading, "Pricing")
        self.assertEqual(repaired[0].metadata.get("detected_via"), "geometry+vlm")
        self.assertGreaterEqual(float(repaired[0].metadata.get("structure_confidence") or 0.0), 0.9)
        self.assertEqual(repaired[0].column_schema, ["fee", "amount"])

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_vlm_repair_skips_high_confidence_tables_without_calling_model(self, _build_embeddings) -> None:
        service = self._service()
        table = self._low_conf_geometry_table()
        table.metadata["structure_confidence"] = 0.95

        with (
            mock.patch.object(KnowledgeIngestionService, "_render_table_crop", return_value=b"png") as render,
            mock.patch.object(KnowledgeIngestionService, "_run_vlm_table_repair", return_value=None) as run,
        ):
            repaired, issues, meta = service._repair_tables_with_vlm(Path("dummy.pdf"), [table])

        self.assertEqual(len(issues), 0)
        self.assertEqual(repaired[0].metadata.get("structure_confidence"), 0.95)
        self.assertEqual(meta.get("attempted"), 0)
        self.assertEqual(meta.get("repaired"), 0)
        render.assert_not_called()
        run.assert_not_called()

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_vlm_repair_retries_full_page_after_crop_failure(self, _build_embeddings) -> None:
        service = self._service()
        table = self._low_conf_geometry_table()
        vlm_payload = {
            "columns": ["Fee", "Amount"],
            "rows": [["Annual fee", "100"]],
        }

        with (
            mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=False),
            mock.patch("openai.OpenAI"),
            mock.patch.object(KnowledgeIngestionService, "_render_table_crop", return_value=b"crop"),
            mock.patch.object(KnowledgeIngestionService, "_render_full_page", return_value=b"full_page"),
            mock.patch.object(
                KnowledgeIngestionService,
                "_run_vlm_table_repair",
                side_effect=[None, vlm_payload],
            ) as run,
        ):
            repaired, issues, meta = service._repair_tables_with_vlm(Path("dummy.pdf"), [table])

        self.assertEqual(meta.get("attempted"), 1)
        self.assertEqual(meta.get("repaired"), 1)
        self.assertEqual(len(issues), 0)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].kwargs.get("render_mode"), "crop")
        self.assertEqual(run.call_args_list[1].kwargs.get("render_mode"), "full_page")
        self.assertTrue(run.call_args_list[1].kwargs.get("table_hint"))
        self.assertEqual(meta.get("repaired_tables")[0].get("render_mode"), "full_page_retry")
        self.assertEqual(repaired[0].metadata.get("detected_via"), "geometry+vlm")

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_vlm_repair_tracks_attempted_modes_when_crop_and_full_page_fail(self, _build_embeddings) -> None:
        service = self._service()
        table = self._low_conf_geometry_table()

        with (
            mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=False),
            mock.patch("openai.OpenAI"),
            mock.patch.object(KnowledgeIngestionService, "_render_table_crop", return_value=b"crop"),
            mock.patch.object(KnowledgeIngestionService, "_render_full_page", return_value=b"full_page"),
            mock.patch.object(
                KnowledgeIngestionService,
                "_run_vlm_table_repair",
                side_effect=[None, None],
            ),
        ):
            repaired, issues, meta = service._repair_tables_with_vlm(Path("dummy.pdf"), [table])

        self.assertEqual(meta.get("attempted"), 1)
        self.assertEqual(meta.get("repaired"), 0)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "table_vlm_failed")
        self.assertEqual(issues[0].details.get("attempted_modes"), ["crop", "full_page"])
        self.assertEqual(issues[0].details.get("render_mode"), "crop")
        self.assertEqual(repaired[0].metadata.get("detected_via"), "geometry")
