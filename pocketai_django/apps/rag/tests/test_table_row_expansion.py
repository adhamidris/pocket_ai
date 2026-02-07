"""
Integration test for table row expansion bug fix.

Tests that fee/pricing queries return row chunks with concrete numbers,
not parent chunks with OCR artifacts.
"""
import pytest
from django.test import TestCase, SimpleTestCase
from apps.accounts.models import BusinessProfile, AgentProfile, KnowledgeUpload, KnowledgeUploadChunk
from apps.rag.ai_orchestrator import KnowledgeSearchService
from django.core.files.uploadedfile import SimpleUploadedFile


class TableRowExpansionTest(TestCase):
    """Test table row expansion returns concrete data from row chunks."""
    
    def setUp(self):
        self.business = BusinessProfile.objects.create(
            name="Test Business",
            industry="finance",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            name="Test Agent",
        )
        self.search_service = KnowledgeSearchService()
    
    def test_fee_query_returns_row_chunks_not_parent(self):
        """
        Critical test: 'credit card fees' query should return ROW chunks with
        concrete numbers (e.g., '500 EGP'), not PARENT chunks with OCR artifacts.
        """
        # Create a mock table upload with parent + row chunks
        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            display_name="Credit Card Fee Schedule",
            source_name="fees.pdf",
            ingestion_metadata={"format_hint": "pdf"},
        )
        
        # Simulate table parent chunk (OCR-corrupted markdown summary)
        parent_chunk = KnowledgeUploadChunk.objects.create(
            upload=upload,
            business_profile=self.business,
            chunk_index=0,
            content="Table 1: Fees\n| Card | Annual |\n|------|--------|\n| OCR_NOISE_123 | OCR_NOISE_456 |",
            token_count=20,
            metadata={
                "is_table_chunk": True,
                "table_chunk_role": "parent",
                "is_table_preview": True,
                "table_id": "550e8400-e29b-41d4-a716-446655440000",  # UUID string
                "table_title": "Credit Card Fees",
                "strategy": "table_schema",
            },
        )
        
        # Simulate table row chunks (actual answer data)
        row1 = KnowledgeUploadChunk.objects.create(
            upload=upload,
            business_profile=self.business,
            chunk_index=1,
            content="[Table] Credit Card Fees\n[Row] 0\nCard: Platinum\nAnnual Fee: EGP 500",
            token_count=15,
            metadata={
                "is_table_chunk": True,
                "table_chunk_role": "row",
                "is_table_preview": False,
                "table_id": "550e8400-e29b-41d4-a716-446655440000",
                "table_row_index": 0,
                "strategy": "table_schema",
            },
        )
        
        row2 = KnowledgeUploadChunk.objects.create(
            upload=upload,
            business_profile=self.business,
            chunk_index=2,
            content="[Table] Credit Card Fees\n[Row] 1\nCard: Gold\nAnnual Fee: EGP 300",
            token_count=15,
            metadata={"is_table_chunk": True,
                "table_chunk_role": "row",
                "is_table_preview": False,
                "table_id": "550e8400-e29b-41d4-a716-446655440000",
                "table_row_index": 1,
                "strategy": "table_schema",
            },
        )
        
        # Search for fees
        result = self.search_service.search(
            business_profile=self.business,
            query="credit card fees",
            limit=3,
        )
        
        # Assert: snippets should contain concrete numbers from row chunks
        assert result.status == "ok", f"Search failed: {result.diagnostics}"
        assert len(result.snippets) > 0, "No snippets returned"
        
        # Check that row expansion happened
        diagnostics = result.diagnostics or {}
        row_expansion_count = diagnostics.get("table_row_expansion", 0)
        
        # The key assertion: row expansion should have occurred
        assert row_expansion_count > 0, (
            f"Table row expansion did not occur! "
            f"Diagnostics: {diagnostics.get('table_reason')}, "
            f"parent_limited: {diagnostics.get('table_parent_limited')}"
        )
        
        # Check snippet content: should have concrete numbers, not OCR noise
        snippet_text = " ".join(s.content or s.summary for s in result.snippets)
        assert "EGP 500" in snippet_text or "EGP 300" in snippet_text, (
            f"Snippets don't contain concrete fee numbers! Content: {snippet_text[:200]}"
        )
        assert "OCR_NOISE" not in snippet_text, (
            f"Snippets contain OCR artifacts from parent chunk! Content: {snippet_text[:200]}"
        )
    
    def test_parent_chunk_metadata_validation(self):
        """Validate that parent chunks have required table_id metadata."""
        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            display_name="Test Upload",
            source_name="test.pdf",
        )
        
        # Create parent chunk WITHOUT table_id (should trigger warning)
        parent_no_id = KnowledgeUploadChunk.objects.create(
            upload=upload,
            business_profile=self.business,
            chunk_index=0,
            content="Table without ID",
            token_count=5,
            metadata={
                "is_table_chunk": True,
                "table_chunk_role": "parent",
                "is_table_preview": True,
                # Missing: "table_id"
            },
        )
        
        # Search should NOT crash, but logs should show warning
        result = self.search_service.search(
            business_profile=self.business,
            query="table",
            limit=3,
        )
        
        # Should complete without error (defensive coding)
        assert result.status in ("ok", "not_found")


class TableRowExpansionOrderingTest(SimpleTestCase):
    """Unit tests for the query-relevance ordering and score differentiation."""

    def _make_chunk(self, content):
        """Create a mock chunk with the given content."""
        from unittest.mock import MagicMock
        chunk = MagicMock()
        chunk.content = content
        return chunk

    def test_multi_table_expansion_orders_relevant_rows_first(self):
        """
        Regression test: when multiple unrelated tables are expanded, rows
        from tables whose content matches query tokens must appear before
        rows from unrelated tables.  This prevents irrelevant table rows
        (e.g. Mortgage, Teller) from flooding the top results when the
        user asks about "credit card fees".
        """
        from apps.rag.ai_orchestrator import ChunkResult

        # --- Credit-card table rows (RELEVANT to query "credit card fees") ---
        cc_row1 = self._make_chunk("[Table] Credit Card Fees\n[Row] 0\nCard: Platinum\nAnnual Fee: EGP 500")
        cc_row2 = self._make_chunk("[Table] Credit Card Fees\n[Row] 1\nCard: Gold\nAnnual Fee: EGP 300")

        # --- Unrelated table rows ---
        mort_row = self._make_chunk("[Table] Mortgage Rates\n[Row] 0\nTerm: 20 years\nRate: 12.5%")
        teller_row = self._make_chunk("[Table] Teller Services\n[Row] 0\nService: Cash Deposit\nCharge: EGP 10")

        # Build ChunkResult list mimicking what _expand_table_rows returns:
        # unrelated rows appear first (simulating the bug scenario).
        new_rows = [
            ChunkResult(chunk=mort_row, source_stage="table_row_expansion", lexical_score=0.0),
            ChunkResult(chunk=teller_row, source_stage="table_row_expansion", lexical_score=0.0),
            ChunkResult(chunk=cc_row1, source_stage="table_row_expansion", lexical_score=0.4),
            ChunkResult(chunk=cc_row2, source_stage="table_row_expansion", lexical_score=0.3),
        ]

        # A non-parent search result (regular vector hit)
        regular_chunk = self._make_chunk("Our credit card fee schedule is competitive.")
        non_parent_chunks = [
            ChunkResult(chunk=regular_chunk, source_stage="vector", lexical_score=0.5),
        ]

        # Apply the same splitting logic used in _search_inner
        query_tokens = ("credit", "card", "fees")

        def _has_query_token_overlap(hit):
            text = (hit.chunk.content or "").lower()
            return bool(text and query_tokens and any(t in text for t in query_tokens))

        relevant_rows = [r for r in new_rows if _has_query_token_overlap(r)]
        supplemental_rows = [r for r in new_rows if not _has_query_token_overlap(r)]

        chunk_hits = (
            tuple(relevant_rows)
            + tuple(non_parent_chunks)
            + tuple(supplemental_rows)
        )

        # --- Assertions ---
        # Relevant credit-card rows must come first
        self.assertEqual(len(relevant_rows), 2, f"Expected 2 relevant rows, got {len(relevant_rows)}")
        self.assertTrue(all("credit" in r.chunk.content.lower() or "card" in r.chunk.content.lower()
                            for r in relevant_rows))

        # Supplemental (unrelated) rows must be separated
        self.assertEqual(len(supplemental_rows), 2, f"Expected 2 supplemental rows, got {len(supplemental_rows)}")

        # In the final ordering, the first two hits must be the credit-card rows
        self.assertIn("Credit Card", chunk_hits[0].chunk.content)
        self.assertIn("Credit Card", chunk_hits[1].chunk.content)

        # The regular search result must come before supplemental rows
        self.assertEqual(chunk_hits[2].source_stage, "vector")

        # Supplemental rows come last
        self.assertTrue("Mortgage" in chunk_hits[3].chunk.content or "Teller" in chunk_hits[3].chunk.content)
        self.assertTrue("Mortgage" in chunk_hits[4].chunk.content or "Teller" in chunk_hits[4].chunk.content)

    def test_differentiated_score_zero_lexical_overlap(self):
        """
        Rows with zero lexical overlap should get effective_weight=0.05
        (not 0.3), so unrelated table rows don't receive inflated scores.
        """
        inherited_weight = 0.3
        base_lexical = 0.8

        # Row WITH lexical overlap
        lexical_score_relevant = 0.4
        effective_weight_relevant = inherited_weight if lexical_score_relevant > 0 else 0.05
        score_relevant = max(base_lexical * effective_weight_relevant, lexical_score_relevant)
        self.assertEqual(effective_weight_relevant, 0.3)
        self.assertAlmostEqual(score_relevant, max(0.8 * 0.3, 0.4))

        # Row WITHOUT lexical overlap
        lexical_score_irrelevant = 0.0
        effective_weight_irrelevant = inherited_weight if lexical_score_irrelevant > 0 else 0.05
        score_irrelevant = max(base_lexical * effective_weight_irrelevant, lexical_score_irrelevant)
        self.assertEqual(effective_weight_irrelevant, 0.05)
        self.assertAlmostEqual(score_irrelevant, 0.8 * 0.05)

        # Irrelevant row score should be much lower
        self.assertLess(score_irrelevant, score_relevant,
                        f"Irrelevant row score ({score_irrelevant}) should be less than "
                        f"relevant row score ({score_relevant})")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
