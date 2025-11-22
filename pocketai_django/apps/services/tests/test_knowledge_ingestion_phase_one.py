from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings

from apps.accounts.models import (
    BusinessProfile,
    KnowledgeAlias,
    KnowledgeEntity,
    KnowledgeSourceType,
    KnowledgeStatus,
    KnowledgeUpload,
    KnowledgeUploadFile,
    KnowledgeUploadIssue,
    KnowledgeUploadTable,
    KnowledgeUploadTableRow,
    RegistrationSession,
    User,
)
from apps.services.knowledge_ingestion import KnowledgeIngestionService

try:
    from openpyxl import Workbook
except ImportError:
    Workbook = None


class KnowledgeIngestionAliasTests(TestCase):
    def test_collect_aliases_detects_short_identifiers(self):
        aliases, sources = KnowledgeIngestionService._collect_aliases_from_record(
            record={"slug": "trip-1"},
            flattened={"slug": "trip-1", "code": "A-9"},
            attributes={"slug": "trip-1"},
            entity_name="Trip 1",
        )
        self.assertIn("trip-1", aliases)
        self.assertTrue(any(source.startswith("record_slug") for source in sources))


class KnowledgeIngestionJsonTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._media_root)
        self.override = override_settings(MEDIA_ROOT=self._media_root)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = User.objects.create(email="ingest@example.com", first_name="Test")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Travel Co",
            industry="travel",
            metadata={"ingest_max_json_entities": 2},
        )

    @mock.patch("apps.services.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_json_ingestion_persists_entities_and_aliases(self, _build_embeddings):
        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.PENDING,
            display_name="Trips",
        )
        storage_path = Path("uploads/sample.json")
        target_path = Path(self._media_root) / storage_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "trips": [
                {"slug": "trip-1", "name": "Trip One", "code": "TR-ONE"},
                {"slug": "trip-2", "name": "Trip Two", "code": "TR-TWO"},
                {"slug": "trip-3", "name": "Trip Three", "code": "TR-THREE"},
            ]
        }
        target_path.write_text(json.dumps(payload), encoding="utf-8")
        KnowledgeUploadFile.objects.create(
            upload=upload,
            filename="sample.json",
            storage_path=str(storage_path),
            content_type="application/json",
            size_bytes=target_path.stat().st_size,
        )

        service = KnowledgeIngestionService(media_root=Path(self._media_root))
        extraction = service._extract_upload(upload)
        service._persist_extraction(upload, extraction)

        upload.refresh_from_db()
        self.assertEqual(KnowledgeEntity.objects.filter(upload=upload).count(), 2)
        alias_count = KnowledgeAlias.objects.filter(entity__upload=upload).count()
        self.assertGreater(alias_count, 0)
        self.assertEqual(upload.ingestion_metadata.get("alias_count"), alias_count)
        self.assertEqual(upload.ingestion_metadata.get("truncated_entities"), 2)


class KnowledgeIngestionSpreadsheetTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._media_root)
        self.override = override_settings(MEDIA_ROOT=self._media_root)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = User.objects.create(email="spreadsheet@example.com", first_name="Sheet")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Data Co",
            industry="finance",
        )

    @mock.patch("apps.services.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_csv_ingestion_creates_tables(self, _build_embeddings):
        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.PENDING,
            display_name="Plans CSV",
        )
        storage_path = Path("uploads/plans.csv")
        target_path = Path(self._media_root) / storage_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text("plan,price\nBasic,10\nPro,25\n", encoding="utf-8")
        KnowledgeUploadFile.objects.create(
            upload=upload,
            filename="plans.csv",
            storage_path=str(storage_path),
            content_type="text/csv",
            size_bytes=target_path.stat().st_size,
        )
        service = KnowledgeIngestionService(media_root=Path(self._media_root))
        extraction = service._extract_upload(upload)
        service._persist_extraction(upload, extraction)
        upload.refresh_from_db()
        tables = KnowledgeUploadTable.objects.filter(upload=upload)
        self.assertEqual(tables.count(), 1)
        rows = KnowledgeUploadTableRow.objects.filter(table__upload=upload)
        self.assertEqual(rows.count(), 2)
        self.assertEqual(tables.first().column_schema, ["plan", "price"])
        entities = KnowledgeEntity.objects.filter(upload=upload)
        self.assertEqual(entities.count(), 2)
        self.assertTrue(
            KnowledgeAlias.objects.filter(entity__upload=upload, alias_normalized="basic").exists()
        )
        table_stats = upload.ingestion_metadata.get("table_stats") or {}
        self.assertEqual(table_stats.get("total_rows"), 2)
        self.assertEqual(table_stats.get("indexed_rows"), 2)
        self.assertEqual(table_stats.get("row_cap"), 2)
        self.assertEqual(table_stats.get("row_tier"), "small")
        self.assertFalse(table_stats.get("partial_index"))
        self.assertFalse(KnowledgeUploadIssue.objects.filter(upload=upload).exists())

    def test_detect_format_prefers_csv_over_text_content_type(self):
        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.PENDING,
            display_name="Plans CSV",
        )
        KnowledgeUploadFile.objects.create(
            upload=upload,
            filename="plans.csv",
            storage_path="uploads/plans.csv",
            content_type="text/csv",
            size_bytes=123,
        )
        service = KnowledgeIngestionService(media_root=Path(self._media_root))
        file_detail = upload.file_detail
        self.assertEqual(service._detect_format(file_detail), "csv")

    @mock.patch("apps.services.knowledge_ingestion.build_embedding_service", return_value=None)
    @override_settings(
        TABLE_MAX_ROWS_DEFAULT=3,
        RAG_TABLE_SMALL_ROW_LIMIT=2,
        RAG_TABLE_LARGE_ROW_LIMIT=4,
        RAG_TABLE_MAX_HARD_CAP=5,
    )
    def test_large_csv_truncation_emits_issue(self, _build_embeddings):
        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.PENDING,
            display_name="Large CSV",
        )
        storage_path = Path("uploads/large.csv")
        target_path = Path(self._media_root) / storage_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        rows = ["plan,price"]
        for idx in range(1, 7):
            rows.append(f"Tier{idx},{idx * 10}")
        target_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        KnowledgeUploadFile.objects.create(
            upload=upload,
            filename="large.csv",
            storage_path=str(storage_path),
            content_type="text/csv",
            size_bytes=target_path.stat().st_size,
        )
        service = KnowledgeIngestionService(media_root=Path(self._media_root))
        extraction = service._extract_upload(upload)
        service._persist_extraction(upload, extraction)
        upload.refresh_from_db()
        self.assertEqual(KnowledgeUploadTableRow.objects.filter(table__upload=upload).count(), 3)
        table_truncation = upload.ingestion_metadata.get("table_truncation") or {}
        self.assertEqual(table_truncation.get("truncated_rows"), 3)
        table_stats = upload.ingestion_metadata.get("table_stats") or {}
        self.assertTrue(table_stats.get("partial_index"))
        self.assertEqual(table_stats.get("row_cap"), 3)
        self.assertTrue(
            KnowledgeUploadIssue.objects.filter(upload=upload, issue_code="table_rows_truncated").exists()
        )
        self.assertEqual(table_stats.get("row_tier"), "large")
        issues = KnowledgeUploadIssue.objects.filter(upload=upload, issue_code="table_rows_truncated")
        self.assertTrue(issues.exists())

    @mock.patch("apps.services.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_xlsx_ingestion_creates_tables(self, _build_embeddings):
        if Workbook is None:
            self.skipTest("openpyxl not installed")
        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.PENDING,
            display_name="Plans XLSX",
        )
        storage_path = Path("uploads/plans.xlsx")
        target_path = Path(self._media_root) / storage_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Plans"
        sheet.append(["plan", "price"])
        sheet.append(["Starter", 5])
        sheet.append(["Enterprise", 99])
        workbook.save(target_path)
        KnowledgeUploadFile.objects.create(
            upload=upload,
            filename="plans.xlsx",
            storage_path=str(storage_path),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            size_bytes=target_path.stat().st_size,
        )
        service = KnowledgeIngestionService(media_root=Path(self._media_root))
        extraction = service._extract_upload(upload)
        service._persist_extraction(upload, extraction)
        upload.refresh_from_db()
        tables = KnowledgeUploadTable.objects.filter(upload=upload)
        self.assertEqual(tables.count(), 1)
        rows = KnowledgeUploadTableRow.objects.filter(table__upload=upload)
        self.assertEqual(rows.count(), 2)
        self.assertEqual(tables.first().column_schema, ["plan", "price"])
        entities = KnowledgeEntity.objects.filter(upload=upload)
        self.assertEqual(entities.count(), 2)
        self.assertTrue(
            KnowledgeAlias.objects.filter(entity__upload=upload, alias_normalized="starter").exists()
        )
        table_stats = upload.ingestion_metadata.get("table_stats") or {}
        self.assertEqual(table_stats.get("total_rows"), 2)
        self.assertEqual(table_stats.get("indexed_rows"), 2)
        self.assertEqual(table_stats.get("row_cap"), 2)
        self.assertEqual(table_stats.get("row_tier"), "small")
        self.assertFalse(table_stats.get("partial_index"))
