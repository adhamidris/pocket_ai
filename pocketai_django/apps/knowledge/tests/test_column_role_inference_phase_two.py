from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase

from apps.knowledge.column_role_inference import (
    COLUMN_ROLE_DESCRIPTOR,
    COLUMN_ROLE_NOTE,
    COLUMN_ROLE_QUALIFIER,
    COLUMN_ROLE_SCOPE_DIMENSION,
    infer_column_roles,
)
from apps.knowledge.knowledge_ingestion import (
    COLUMN_ROLE_INFERENCE_VERSION,
    ExtractionResult,
    KnowledgeIngestionService,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
)


class ColumnRoleInferenceModuleTests(SimpleTestCase):
    def test_infer_roles_separates_qualifier_and_scope_columns(self) -> None:
        schema = ["Service", "Tariff", "Prime", "Plus", "Wealth", "Private"]
        rows = [
            schema,
            ["Cash withdrawal in branch", "EGP", "1.0%", "", "", ""],
            ["Cheque processing in branch", "USD", "", "1.5%", "", ""],
            ["Cross-border transfer charge", "EGP", "", "", "2.0%", ""],
            ["Card replacement service", "USD", "", "", "", "2.5%"],
        ]

        profiles = infer_column_roles(
            row_values=rows,
            column_schema=schema,
            header_row_indices={0},
            min_scope_columns=3,
        )
        role_by_column = {profile.column_key: profile.role for profile in profiles}

        self.assertEqual(role_by_column.get("Service"), COLUMN_ROLE_DESCRIPTOR)
        self.assertEqual(role_by_column.get("Tariff"), COLUMN_ROLE_QUALIFIER)
        self.assertEqual(role_by_column.get("Prime"), COLUMN_ROLE_SCOPE_DIMENSION)
        self.assertEqual(role_by_column.get("Plus"), COLUMN_ROLE_SCOPE_DIMENSION)
        self.assertEqual(role_by_column.get("Wealth"), COLUMN_ROLE_SCOPE_DIMENSION)
        self.assertEqual(role_by_column.get("Private"), COLUMN_ROLE_SCOPE_DIMENSION)

    def test_infer_roles_detects_sparse_notes_column(self) -> None:
        schema = ["Metric", "Segment A", "Segment B", "Notes"]
        rows = [
            schema,
            ["Cycle time reduction", "12%", "10%", ""],
            ["Defect density", "1.2", "1.0", ""],
            ["Throughput", "420 units", "390 units", ""],
            [
                "Scrap rate",
                "2.1%",
                "1.8%",
                "Only measured in plants with optical inspection lines",
            ],
        ]

        profiles = infer_column_roles(
            row_values=rows,
            column_schema=schema,
            header_row_indices={0},
            min_scope_columns=2,
        )
        role_by_column = {profile.column_key: profile.role for profile in profiles}

        self.assertEqual(role_by_column.get("Metric"), COLUMN_ROLE_DESCRIPTOR)
        self.assertEqual(role_by_column.get("Segment A"), COLUMN_ROLE_SCOPE_DIMENSION)
        self.assertEqual(role_by_column.get("Segment B"), COLUMN_ROLE_SCOPE_DIMENSION)
        self.assertEqual(role_by_column.get("Notes"), COLUMN_ROLE_NOTE)


class ColumnRoleInferencePersistenceTests(SimpleTestCase):
    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_enrich_extraction_persists_role_confidence(self, _build_embeddings) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)

        table = TablePayload(
            order_index=1,
            title="Fee matrix",
            section_heading="",
            page_number=1,
            column_schema=["Service", "Tariff", "Prime", "Plus", "Wealth", "Private"],
            rows=[
                TableRowPayload(
                    row_index=0,
                    page_number=1,
                    metadata={"row_type": "header"},
                    raw_text="Service | Tariff | Prime | Plus | Wealth | Private",
                    cells=[
                        TableCellPayload(0, 0, "service", "Service"),
                        TableCellPayload(0, 1, "tariff", "Tariff"),
                        TableCellPayload(0, 2, "prime", "Prime"),
                        TableCellPayload(0, 3, "plus", "Plus"),
                        TableCellPayload(0, 4, "wealth", "Wealth"),
                        TableCellPayload(0, 5, "private", "Private"),
                    ],
                ),
                TableRowPayload(
                    row_index=1,
                    page_number=1,
                    metadata={"row_type": "data"},
                    raw_text="Cash withdrawal in branch | EGP | 1.0%",
                    cells=[
                        TableCellPayload(1, 0, "service", "Cash withdrawal in branch"),
                        TableCellPayload(1, 1, "tariff", "EGP"),
                        TableCellPayload(1, 2, "prime", "1.0%"),
                    ],
                ),
                TableRowPayload(
                    row_index=2,
                    page_number=1,
                    metadata={"row_type": "data"},
                    raw_text="Cross-border transfer charge | USD | 1.6%",
                    cells=[
                        TableCellPayload(2, 0, "service", "Cross-border transfer charge"),
                        TableCellPayload(2, 1, "tariff", "USD"),
                        TableCellPayload(2, 3, "plus", "1.6%"),
                    ],
                ),
            ],
        )

        extraction = ExtractionResult(
            text="fees",
            format_hint="pdf",
            metadata={},
            pages=[],
            tables=[table],
            issues=[],
            entities=[],
        )

        enriched = service._enrich_extraction_with_column_roles(extraction)

        self.assertIn("column_role_inference", enriched.metadata)
        role_meta = enriched.metadata.get("column_role_inference") or {}
        self.assertEqual(role_meta.get("version"), COLUMN_ROLE_INFERENCE_VERSION)
        self.assertEqual(role_meta.get("table_count"), 1)
        self.assertEqual(role_meta.get("tables_with_roles"), 1)

        table_data = enriched.tables[0].data_dictionary
        self.assertEqual(table_data.get("column_role_inference_version"), COLUMN_ROLE_INFERENCE_VERSION)

        roles = table_data.get("column_roles") or []
        self.assertEqual(len(roles), 6)
        self.assertTrue(all("confidence" in role for role in roles))
        self.assertTrue(all("role_scores" in role for role in roles))

        grouped = table_data.get("column_roles_by_type") or {}
        self.assertIn("Service", grouped.get(COLUMN_ROLE_DESCRIPTOR, []))
        self.assertIn("Tariff", grouped.get(COLUMN_ROLE_QUALIFIER, []))
        self.assertIn("Prime", grouped.get(COLUMN_ROLE_SCOPE_DIMENSION, []))
        self.assertIn("Plus", grouped.get(COLUMN_ROLE_SCOPE_DIMENSION, []))
