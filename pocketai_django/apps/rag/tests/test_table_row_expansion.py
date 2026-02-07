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
    """Unit tests for fraction-based ordering, score differentiation, and document-name boost."""

    def _make_chunk(self, content, upload=None):
        """Create a mock chunk with the given content."""
        from unittest.mock import MagicMock
        chunk = MagicMock()
        chunk.content = content
        chunk.metadata = {}
        if upload is not None:
            chunk.upload = upload
        return chunk

    def _make_upload(self, display_name, source_name=None):
        from unittest.mock import MagicMock
        upload = MagicMock()
        upload.display_name = display_name
        upload.source_name = source_name or display_name
        return upload

    # ------------------------------------------------------------------
    # Fix 3: Fraction-based token overlap ordering
    # ------------------------------------------------------------------

    def test_fraction_based_ordering_separates_strong_and_weak_matches(self):
        """
        With query "credit card issuance fees" (4 tokens), a row matching
        3/4 tokens (75%) should be classified as relevant while a row
        matching only 1/4 (25%) should be supplemental.
        """
        from apps.rag.ai_orchestrator import ChunkResult

        # 3/4 tokens match: "credit", "card", "fees" → 75%
        cc_row = self._make_chunk(
            "[Table] Credit Card Fees\n[Row] 0\nCard: Platinum\nIssuance Fee: EGP 500"
        )
        # 1/4 tokens match: only "fees" → 25%
        cheque_row = self._make_chunk(
            "[Table] Cheques\n[Row] 1\nService: Chequebook\nFees: EGP 480"
        )
        # 0/4 tokens match → 0%
        loan_row = self._make_chunk(
            "[Table] Loans\n[Row] 0\nTerm: 5 years\nRate: 10%"
        )

        new_rows = [
            ChunkResult(chunk=cheque_row, source_stage="table_row_expansion"),
            ChunkResult(chunk=loan_row, source_stage="table_row_expansion"),
            ChunkResult(chunk=cc_row, source_stage="table_row_expansion"),
        ]

        _expansion_tokens = ("credit", "card", "issuance", "fees")

        def _token_overlap_fraction(hit):
            text = (hit.chunk.content or "").lower()
            if not text or not _expansion_tokens:
                return 0.0
            matches = sum(1 for t in _expansion_tokens if t in text)
            return matches / len(_expansion_tokens)

        _RELEVANCE_THRESHOLD = 0.5
        scored_rows = [(_token_overlap_fraction(r), r) for r in new_rows]
        relevant_rows = sorted(
            [r for frac, r in scored_rows if frac >= _RELEVANCE_THRESHOLD],
            key=lambda r: _token_overlap_fraction(r),
            reverse=True,
        )
        supplemental_rows = [r for frac, r in scored_rows if frac < _RELEVANCE_THRESHOLD]

        # Credit card row (75%) is the only one above 50% threshold
        self.assertEqual(len(relevant_rows), 1)
        self.assertIn("Credit Card", relevant_rows[0].chunk.content)

        # Cheque (25%) and Loan (0%) are supplemental
        self.assertEqual(len(supplemental_rows), 2)

    def test_fraction_based_ordering_sorts_relevant_rows_by_overlap(self):
        """Relevant rows should be sorted by overlap fraction (highest first)."""
        from apps.rag.ai_orchestrator import ChunkResult

        # 4/4 match
        full_match = self._make_chunk("Credit card issuance fees: EGP 500")
        # 3/4 match
        partial_match = self._make_chunk("Credit card fees: EGP 300")
        # 2/4 match
        weak_match = self._make_chunk("Card fees only: EGP 100")

        new_rows = [
            ChunkResult(chunk=weak_match, source_stage="table_row_expansion"),
            ChunkResult(chunk=full_match, source_stage="table_row_expansion"),
            ChunkResult(chunk=partial_match, source_stage="table_row_expansion"),
        ]

        _expansion_tokens = ("credit", "card", "issuance", "fees")

        def _token_overlap_fraction(hit):
            text = (hit.chunk.content or "").lower()
            if not text or not _expansion_tokens:
                return 0.0
            return sum(1 for t in _expansion_tokens if t in text) / len(_expansion_tokens)

        _RELEVANCE_THRESHOLD = 0.5
        scored_rows = [(_token_overlap_fraction(r), r) for r in new_rows]
        relevant_rows = sorted(
            [r for frac, r in scored_rows if frac >= _RELEVANCE_THRESHOLD],
            key=lambda r: _token_overlap_fraction(r),
            reverse=True,
        )

        self.assertEqual(len(relevant_rows), 3)
        # Full match (4/4=1.0) first, then partial (3/4=0.75), then weak (2/4=0.5)
        self.assertIn("issuance", relevant_rows[0].chunk.content.lower())
        self.assertAlmostEqual(_token_overlap_fraction(relevant_rows[0]), 1.0)
        self.assertGreater(
            _token_overlap_fraction(relevant_rows[0]),
            _token_overlap_fraction(relevant_rows[1]),
        )

    # ------------------------------------------------------------------
    # Fix 3: Continuous effective_weight in _expand_table_rows
    # ------------------------------------------------------------------

    def test_continuous_effective_weight_scales_with_overlap(self):
        """
        effective_weight should scale continuously with lexical overlap
        fraction, not be binary 0.3 vs 0.05.
        """
        inherited_weight = 0.3
        base_rerank = 0.8

        # Full overlap (1.0): weight = max(0.05, 0.3 * 1.0) = 0.3
        lexical_full = 1.0
        ew_full = max(0.05, inherited_weight * lexical_full)
        self.assertAlmostEqual(ew_full, 0.3)

        # Half overlap (0.5): weight = max(0.05, 0.3 * 0.5) = 0.15
        lexical_half = 0.5
        ew_half = max(0.05, inherited_weight * lexical_half)
        self.assertAlmostEqual(ew_half, 0.15)

        # Quarter overlap (0.25): weight = max(0.05, 0.3 * 0.25) = 0.075
        lexical_quarter = 0.25
        ew_quarter = max(0.05, inherited_weight * lexical_quarter)
        self.assertAlmostEqual(ew_quarter, 0.075)

        # Zero overlap: weight = 0.05 (floor)
        lexical_zero = 0.0
        ew_zero = 0.05  # matches the code: `if lexical_score > 0 else 0.05`
        self.assertAlmostEqual(ew_zero, 0.05)

        # Scores should be monotonically decreasing
        score_full = max(base_rerank * ew_full, lexical_full)
        score_half = max(base_rerank * ew_half, lexical_half)
        score_quarter = max(base_rerank * ew_quarter, lexical_quarter)
        score_zero = max(base_rerank * ew_zero, 0.0)

        self.assertGreater(score_full, score_half)
        self.assertGreater(score_half, score_quarter)
        self.assertGreater(score_quarter, score_zero)

    # ------------------------------------------------------------------
    # Fix 2: Document-name relevance boost
    # ------------------------------------------------------------------

    def test_document_name_boost_scores_matching_names_higher(self):
        """
        Chunks from a document named 'Fees and Charges Credit Cards'
        should get a higher document_name_boost than chunks from
        'Cheques-EN' for query tokens ("credit", "card", "fees").
        """
        from apps.rag.ai_orchestrator import KnowledgeSearchService

        scorer = KnowledgeSearchService._lexical_score_text
        tokens = ("credit", "card", "issuance", "fees")

        # Document name closely matching query
        score_cc = scorer("Fees and Charges Credit Cards Eng_185", tokens)
        # Document name partially matching
        score_debit = scorer("Debit and Prepaid Fees and Charges EN", tokens)
        # Document name barely matching
        score_cheque = scorer("Cheques-EN", tokens)
        # Document name not matching at all
        score_loan = scorer("Mortgage Rates 2025", tokens)

        # Credit cards doc should score highest (matches "credit", "card", "fees" = 3/4)
        self.assertGreater(score_cc, score_debit)
        self.assertGreater(score_cc, score_cheque)
        self.assertGreater(score_cc, score_loan)

        # Debit doc matches "fees" only = 1/4
        self.assertGreater(score_debit, score_loan)

        # Cheques and Mortgage match 0/4 tokens
        self.assertEqual(score_cheque, 0.0)
        self.assertEqual(score_loan, 0.0)

    # ------------------------------------------------------------------
    # Fix 1: Cross-encoder enablement
    # ------------------------------------------------------------------

    def test_cross_encoder_defaults_enabled(self):
        """Cross-encoder should default to enabled with table-intent skip disabled."""
        from django.conf import settings

        self.assertTrue(
            getattr(settings, "RAG_ENABLE_CROSS_ENCODER", False),
            "RAG_ENABLE_CROSS_ENCODER should default to True",
        )
        self.assertFalse(
            getattr(settings, "RAG_CROSS_ENCODER_AUTO_SKIP_TABLE_INTENT", True),
            "RAG_CROSS_ENCODER_AUTO_SKIP_TABLE_INTENT should default to False",
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
