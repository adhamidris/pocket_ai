from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings

from apps.accounts.models import (
    BusinessProfile,
    KnowledgeSourceType,
    KnowledgeStatus,
    RegistrationSession,
    User,
)
from apps.knowledge.knowledge_ingestion import ExtractionResult, KnowledgeIngestionService, TablePayload
from apps.knowledge.lexicon_learning import TenantLexiconAutoLearningService
from apps.knowledge.models import KnowledgeLexiconTerm, KnowledgeUpload


class TenantLexiconAutoLearningServiceTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="lex-auto@example.com", first_name="Lex")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Ledger Co",
            industry="operations",
        )
        self.upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.PENDING,
            display_name="invoice_handbook.pdf",
            source_name="billing_export.csv",
            category="billing",
            language="en",
        )
        self.service = TenantLexiconAutoLearningService()

    def _sample_extraction(self) -> ExtractionResult:
        return ExtractionResult(
            text="Invoice operations reference for customer billing.",
            format_hint="csv",
            metadata={
                "tool_schema": {
                    "tools": [
                        {
                            "name": "create_invoice",
                            "parameters": {
                                "properties": {
                                    "due_date": {"type": "string"},
                                    "customer_tier": {"type": "string"},
                                }
                            },
                        }
                    ]
                }
            },
            tables=[
                TablePayload(
                    order_index=1,
                    title="Invoice Ledger",
                    section_heading="Billing",
                    page_number=1,
                    column_schema=["Customer Name", "Invoice Total", "Payment Status"],
                    rows=[],
                )
            ],
            entities=[
                {
                    "entity_type": "invoice",
                    "entity_name": "INV-1001",
                    "aliases": ["Invoice #1001", "billing document"],
                    "columns": ["invoice_id", "status"],
                    "attributes": {"due_date": "2026-02-19", "customer_name": "Acme Co"},
                }
            ],
        )

    def test_learns_terms_from_entities_tables_and_tool_schema(self) -> None:
        stats = self.service.learn_from_ingestion(
            upload=self.upload,
            extraction=self._sample_extraction(),
            structured_summary={
                "tables": [
                    {
                        "title": "Outstanding Invoices",
                        "column_schema": ["Balance Due"],
                    }
                ]
            },
            ingestion_metadata={
                "integration_schema": {
                    "properties": {
                        "invoice_currency": {"type": "string"},
                    }
                }
            },
        )

        self.assertTrue(stats.get("enabled"))
        self.assertGreater(int(stats.get("term_count") or 0), 0)

        entity_terms = set(
            KnowledgeLexiconTerm.objects.filter(
                business_profile=self.business,
                term_type=KnowledgeLexiconTerm.TermType.ENTITY,
            ).values_list("canonical_normalized", flat=True)
        )
        attribute_terms = set(
            KnowledgeLexiconTerm.objects.filter(
                business_profile=self.business,
                term_type=KnowledgeLexiconTerm.TermType.ATTRIBUTE,
            ).values_list("canonical_normalized", flat=True)
        )
        self.assertIn("invoice", entity_terms)
        self.assertIn("invoice ledger", entity_terms)
        self.assertIn("customer name", attribute_terms)
        self.assertIn("invoice total", attribute_terms)
        self.assertIn("payment status", attribute_terms)
        self.assertIn("due date", attribute_terms)
        self.assertIn("invoice currency", attribute_terms)

        invoice = KnowledgeLexiconTerm.objects.get(
            business_profile=self.business,
            term_type=KnowledgeLexiconTerm.TermType.ENTITY,
            canonical_normalized="invoice",
        )
        synonyms = set(invoice.synonyms.values_list("synonym_normalized", flat=True))
        self.assertIn("invoice 1001", synonyms)

    def test_learning_is_idempotent_for_reingestion(self) -> None:
        extraction = self._sample_extraction()
        self.service.learn_from_ingestion(upload=self.upload, extraction=extraction)
        first_term_count = KnowledgeLexiconTerm.objects.filter(business_profile=self.business).count()
        self.assertGreater(first_term_count, 0)

        self.service.learn_from_ingestion(upload=self.upload, extraction=extraction)
        second_term_count = KnowledgeLexiconTerm.objects.filter(business_profile=self.business).count()
        self.assertEqual(first_term_count, second_term_count)


class KnowledgeIngestionLexiconAutoLearningHookTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._media_root)
        self.override = override_settings(MEDIA_ROOT=self._media_root)
        self.override.enable()
        self.addCleanup(self.override.disable)

        self.user = User.objects.create(email="ingestion-lex@example.com", first_name="Ingest")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Support Co",
            industry="support",
        )

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_persist_extraction_records_auto_learning_stats(self, _build_embeddings) -> None:
        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.TEXT,
            status=KnowledgeStatus.PENDING,
            display_name="support_notes",
        )
        service = KnowledgeIngestionService(media_root=Path(self._media_root), enable_ocr=False)
        extraction = ExtractionResult(
            text="Support escalation policy for enterprise customers.",
            format_hint="text",
            metadata={"content_type": "text/plain"},
        )

        with mock.patch.object(
            service,
            "_auto_learn_tenant_lexicon",
            return_value={"enabled": True, "term_count": 3, "synonym_count": 1, "language_code": "en"},
        ) as mocked_auto_learn:
            service._persist_extraction(upload, extraction)

        mocked_auto_learn.assert_called_once()
        upload.refresh_from_db()
        lexicon_meta = upload.ingestion_metadata.get("lexicon_auto_learning") or {}
        self.assertEqual(lexicon_meta.get("term_count"), 3)
        self.assertEqual(lexicon_meta.get("synonym_count"), 1)
