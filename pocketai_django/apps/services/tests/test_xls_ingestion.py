from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.services import knowledge_ingestion as ingestion_module
from apps.services.knowledge_ingestion import KnowledgeIngestionService


class XlsIngestionTests(SimpleTestCase):
    def test_detect_format_handles_xls_suffix(self) -> None:
        detail = SimpleNamespace(
            filename="sample.XLS",
            content_type="application/vnd.ms-excel",
        )
        detected = KnowledgeIngestionService._detect_format(detail)
        self.assertEqual(detected, "xls")

    @override_settings(INGEST_NORMALIZE_TABLES=True)
    def test_extract_xls_normalizes_rows(self) -> None:
        xlrd_module = getattr(ingestion_module, "xlrd", None)
        if xlrd_module is None:
            self.skipTest("xlrd not installed")

        class FakeSheet:
            def __init__(self) -> None:
                self.name = "Sheet1"
                self.nrows = 3
                self.ncols = 2
                self._cells = [
                    ["Name", "Status"],
                    ["Alice", "NULL"],
                    ["Bob", ""],
                ]

            def cell_value(self, row: int, col: int):
                return self._cells[row][col]

            def cell_type(self, row: int, col: int) -> int:
                return xlrd_module.XL_CELL_TEXT

        class FakeWorkbook:
            datemode = 0
            nsheets = 1

            def sheet_by_index(self, index: int) -> FakeSheet:
                return FakeSheet()

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_path = root / "sample.xls"
            fake_path.write_bytes(b"fake")
            file_detail = SimpleNamespace(
                filename="sample.xls",
                content_type="application/vnd.ms-excel",
                storage_path=str(fake_path),
            )
            service = KnowledgeIngestionService(media_root=root)
            with mock.patch.object(xlrd_module, "open_workbook", return_value=FakeWorkbook()):
                result = service._extract_xls(fake_path, file_detail=file_detail, upload=None)
        self.assertTrue(result.tables)
        table = result.tables[0]
        self.assertEqual(table.column_schema, ["Name", "Status"])
        self.assertEqual(table.rows[0].cells[1].raw_text, "")
        self.assertEqual(result.metadata.get("format"), "xls")
