"""
Unit tests for get_document_structure MCP tool

Tests the document structure tool that enables LLM-driven enumeration
by returning complete table structure with row labels.
"""

from __future__ import annotations

from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    KnowledgeSourceType,
    KnowledgeStatus,
    KnowledgeUpload,
    KnowledgeUploadTable,
    KnowledgeUploadTableRow,
    KnowledgeUploadTableCell,
    RegistrationSession,
    User,
)
from apps.conversations.models import Conversation
from apps.mcp import tools
from apps.mcp.types import ToolExecutionContext
from core.tenancy import tenant_context


class GetDocumentStructureHandlerTests(TestCase):
    """Tests for _get_document_structure_handler."""
    
    def setUp(self) -> None:
        super().setUp()
        tools._knowledge_service.cache_clear()  # type: ignore[attr-defined]
        self.embed_patcher = mock.patch(
            "apps.rag.ai_orchestrator.build_embedding_service", return_value=None
        )
        self.embed_patcher.start()
        
        self.user = User.objects.create(
            email="doc-structure@example.com", 
            first_name="Structure"
        )
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Cards Inc",
            industry="banking",
        )
        self.tenant_scope = tenant_context(self.business.id)
        self.tenant_scope.__enter__()
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            session_token="doc-structure-session",
        )
        
        # Create a document with tables
        self.upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Credit Cards Guide",
            ingestion_metadata={"format": "pdf"},
        )
        
        # Create a table with column schema
        self.table = KnowledgeUploadTable.objects.create(
            upload=self.upload,
            order_index=1,
            title="Credit Card Types",
            column_schema=["Card Name", "Annual Fee", "Interest Rate", "Benefits"],
        )
        
        # Create rows with cells
        card_names = ["Gold", "Platinum", "Classic", "E-Commerce", "Cash Back"]
        for i, card_name in enumerate(card_names):
            row = KnowledgeUploadTableRow.objects.create(
                table=self.table,
                row_index=i + 1,
            )
            KnowledgeUploadTableCell.objects.create(
                table=self.table,
                row=row,
                column_index=0,
                column_key="Card Name",
                raw_text=card_name,
            )
    
    def tearDown(self) -> None:
        self.embed_patcher.stop()
        if hasattr(self, "tenant_scope"):
            self.tenant_scope.__exit__(None, None, None)
        super().tearDown()
    
    def test_returns_document_structure_with_tables(self) -> None:
        """Test that handler returns complete document structure."""
        context = ToolExecutionContext()
        payload = {"document_id": str(self.upload.id)}
        
        result = tools._get_document_structure_handler(
            payload, self.conversation, context
        )
        
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["total_tables"], 1)
        self.assertEqual(result["total_items"], 5)
        
        document = result.get("document", {})
        self.assertEqual(document["document_id"], str(self.upload.id))
        self.assertEqual(document["display_name"], "Credit Cards Guide")
    
    def test_returns_table_columns(self) -> None:
        """Test that tables include column headers."""
        context = ToolExecutionContext()
        payload = {"document_id": str(self.upload.id)}
        
        result = tools._get_document_structure_handler(
            payload, self.conversation, context
        )
        
        tables = result.get("tables", [])
        self.assertEqual(len(tables), 1)
        
        table = tables[0]
        self.assertEqual(table["title"], "Credit Card Types")
        self.assertEqual(table["column_count"], 4)
        self.assertIn("Card Name", table["columns"])
        self.assertIn("Annual Fee", table["columns"])
    
    def test_returns_row_labels(self) -> None:
        """Test that tables include row labels (first column values)."""
        context = ToolExecutionContext()
        payload = {"document_id": str(self.upload.id), "include_row_labels": True}
        
        result = tools._get_document_structure_handler(
            payload, self.conversation, context
        )
        
        tables = result.get("tables", [])
        table = tables[0]
        
        self.assertIn("row_labels", table)
        row_labels = table["row_labels"]
        self.assertEqual(len(row_labels), 5)
        self.assertIn("Gold", row_labels)
        self.assertIn("Platinum", row_labels)
        self.assertIn("Classic", row_labels)
        self.assertIn("E-Commerce", row_labels)
        self.assertIn("Cash Back", row_labels)
    
    def test_row_labels_disabled(self) -> None:
        """Test that row labels can be disabled."""
        context = ToolExecutionContext()
        payload = {"document_id": str(self.upload.id), "include_row_labels": False}
        
        result = tools._get_document_structure_handler(
            payload, self.conversation, context
        )
        
        tables = result.get("tables", [])
        table = tables[0]
        
        self.assertNotIn("row_labels", table)
        self.assertEqual(table["row_count"], 5)  # Count still included
    
    def test_missing_document_id_error(self) -> None:
        """Test error when document_id is missing."""
        context = ToolExecutionContext()
        payload = {}
        
        result = tools._get_document_structure_handler(
            payload, self.conversation, context
        )
        
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "missing_document_id")
    
    def test_invalid_document_id_error(self) -> None:
        """Test error when document_id is invalid."""
        context = ToolExecutionContext()
        payload = {"document_id": "not-a-uuid"}
        
        result = tools._get_document_structure_handler(
            payload, self.conversation, context
        )
        
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "invalid_document_id")
    
    def test_document_not_found_error(self) -> None:
        """Test error when document doesn't exist."""
        import uuid
        context = ToolExecutionContext()
        payload = {"document_id": str(uuid.uuid4())}
        
        result = tools._get_document_structure_handler(
            payload, self.conversation, context
        )
        
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["error_code"], "document_not_found")
    
    def test_filter_by_table_id(self) -> None:
        """Test filtering by specific table_id."""
        # Add a second table
        table2 = KnowledgeUploadTable.objects.create(
            upload=self.upload,
            order_index=2,
            title="Fees Table",
            column_schema=["Fee Type", "Amount"],
        )
        
        context = ToolExecutionContext()
        payload = {
            "document_id": str(self.upload.id),
            "table_id": str(self.table.id),
        }
        
        result = tools._get_document_structure_handler(
            payload, self.conversation, context
        )
        
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["total_tables"], 1)
        tables = result["tables"]
        self.assertEqual(tables[0]["title"], "Credit Card Types")

    def test_agent_scope_blocks_unpermitted_document(self) -> None:
        """Test that agent scope restricts access to documents."""
        agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Scoped Agent",
        )
        agent.allowed_documents.add(self.upload)

        restricted_upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Restricted Document",
            ingestion_metadata={"format": "pdf"},
        )
        KnowledgeUploadTable.objects.create(
            upload=restricted_upload,
            order_index=1,
            title="Restricted Table",
            column_schema=["Name", "Value"],
        )

        scoped_conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=agent,
            session_token="doc-structure-scope-session",
        )
        context = ToolExecutionContext()
        payload = {"document_id": str(restricted_upload.id)}

        result = tools._get_document_structure_handler(
            payload, scoped_conversation, context
        )

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["error_code"], "document_not_found")

    @override_settings(MCP_DOC_STRUCTURE_CALLS_PER_MINUTE=1, MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS=60)
    def test_rate_limiting(self) -> None:
        """Test that rate limiting returns throttled status."""
        cache.clear()
        context = ToolExecutionContext()
        payload = {"document_id": str(self.upload.id)}

        first = tools._get_document_structure_handler(
            payload, self.conversation, context
        )
        second = tools._get_document_structure_handler(
            payload, self.conversation, context
        )

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "throttled")
        self.assertEqual(second["error_code"], "rate_limited")
    
    def test_document_without_tables(self) -> None:
        """Test behavior when document has no tables."""
        # Create a document without tables
        text_upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Text Document",
            ingestion_metadata={"format": "txt"},
        )
        
        context = ToolExecutionContext()
        payload = {"document_id": str(text_upload.id)}
        
        result = tools._get_document_structure_handler(
            payload, self.conversation, context
        )
        
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["total_tables"], 0)
        self.assertEqual(result["total_items"], 0)
        self.assertIn("hint", result)


class GetDocumentStructureToolDefinitionTests(TestCase):
    """Tests for get_document_structure tool definition."""
    
    def test_tool_in_definitions(self) -> None:
        """Test that get_document_structure is in TOOL_DEFINITIONS."""
        tool_names = [
            t.get("function", {}).get("name") 
            for t in tools.TOOL_DEFINITIONS
        ]
        self.assertIn("get_document_structure", tool_names)
    
    def test_tool_in_handlers(self) -> None:
        """Test that get_document_structure is in _TOOL_HANDLERS."""
        self.assertIn("get_document_structure", tools._TOOL_HANDLERS)
    
    def test_tool_requires_document_id(self) -> None:
        """Test that document_id is required parameter."""
        tool_def = None
        for t in tools.TOOL_DEFINITIONS:
            if t.get("function", {}).get("name") == "get_document_structure":
                tool_def = t
                break
        
        self.assertIsNotNone(tool_def)
        required = tool_def["function"]["parameters"]["required"]
        self.assertIn("document_id", required)
