"""
Integration test for table row expansion bug fix.

Tests that fee/pricing queries return row chunks with concrete numbers,
not parent chunks with OCR artifacts.
"""
import pytest
from django.test import TransientTransactionTestCase
from apps.accounts.models import BusinessProfile, AgentProfile, KnowledgeUpload, KnowledgeUploadChunk
from apps.rag.ai_orchestrator import KnowledgeSearchService
from django.core.files.uploadedfile import SimpleUploadedFile


class TableRowExpansionTest(TransientTransactionTestCase):
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
