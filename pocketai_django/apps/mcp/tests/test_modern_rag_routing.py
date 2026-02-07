from __future__ import annotations
from unittest import mock
from django.test import TestCase
import uuid

from apps.accounts.models import (
    BusinessProfile,
    KnowledgeSourceType,
    KnowledgeStatus,
    KnowledgeUpload,
    RegistrationSession,
    User,
)
from apps.conversations.models import Conversation
from apps.mcp import tools, orchestrator
from apps.mcp.types import ToolExecutionContext

class ModernRagRoutingTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        tools._knowledge_service.cache_clear()
        self.embed_patcher = mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
        self.embed_patcher.start()
        self.user = User.objects.create(email="rag@example.com", first_name="RAG")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="RAG Bank",
            industry="banking",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            session_token="session-rag",
        )
        
        # Create a PDF upload
        self.pdf_upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Guide.pdf",
            ingestion_metadata={"format": "pdf"},
        )
        
        # Create a Dataset upload
        self.dataset_upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Data.csv",
            ingestion_metadata={"format": "csv", "dataset": {"enabled": True}},
        )

        # Create specific other business profile for isolation testing
        self.other_registration = RegistrationSession.objects.create(user=self.user)
        self.other_business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.other_registration,
            name="Other Bank",
            industry="banking",
        )
        self.other_upload = KnowledgeUpload.objects.create(
             business_profile=self.other_business,
             user=self.user,
             source_type=KnowledgeSourceType.FILE,
             status=KnowledgeStatus.ACTIVE,
             display_name="Secret.csv",
             ingestion_metadata={"format": "csv", "dataset": {"enabled": True}},
        )

    def tearDown(self) -> None:
        self.embed_patcher.stop()
        super().tearDown()

    def test_adaptive_routing_corrects_dataset_query_on_pdf(self) -> None:
        """Test that query_dataset on a PDF is auto-repaired to read_knowledge (agentic contract)."""
        # Create a mock agent profile which is required for McpOrchestratorService init
        mock_agent = mock.Mock()
        mock_agent.business_profile = self.business
        
        service = orchestrator.McpOrchestratorService(agent=mock_agent, provider=None)
        
        args = {"dataset_id": str(self.pdf_upload.id), "query": "select *"}
        
        # Direct call to private method for unit testing logic
        tool_name, new_args = service._adaptive_routing_policy("query_dataset", args, self.conversation)

        self.assertEqual(tool_name, "read_knowledge")
        self.assertEqual(new_args["refs"][0]["id"], str(self.pdf_upload.id))
        self.assertNotIn("mode", new_args)  # read_knowledge has no mode knob in the agentic contract

    def test_adaptive_routing_corrects_read_document_on_dataset(self) -> None:
        """Test that read_document on a Dataset is auto-repaired to query_dataset."""
        mock_agent = mock.Mock()
        mock_agent.business_profile = self.business
        service = orchestrator.McpOrchestratorService(agent=mock_agent, provider=None)
        
        args = {"document_id": str(self.dataset_upload.id)}
        tool_name, new_args = service._adaptive_routing_policy("read_document", args, self.conversation)
        
        self.assertEqual(tool_name, "query_dataset")
        self.assertEqual(new_args["dataset_id"], str(self.dataset_upload.id))
        self.assertEqual(new_args.get("limit"), 5) # Preview

    def test_adaptive_routing_respects_tenant_isolation(self) -> None:
        """Test that cross-tenant IDs are not probed for metadata, preventing auto-repair based on inaccessible info."""
        mock_agent = mock.Mock()
        mock_agent.business_profile = self.business
        service = orchestrator.McpOrchestratorService(agent=mock_agent, provider=None)
        
        # Try to read_document on a dataset from ANOTHER business.
        # If isolation works, _is_dataset returns False (not found), so we do NOT auto-repair to query_dataset.
        # We expect it to be repaired to the agentic read tool ("read_knowledge") without probing cross-tenant metadata.
        args = {"document_id": str(self.other_upload.id)}
        tool_name, new_args = service._adaptive_routing_policy(
            "read_document", 
            args, 
            self.conversation
        )

        self.assertEqual(tool_name, "read_knowledge")
        self.assertEqual(new_args["refs"][0]["id"], str(self.other_upload.id))

    def test_adaptive_routing_leaves_valid_calls_alone(self) -> None:
        mock_agent = mock.Mock()
        mock_agent.business_profile = self.business
        service = orchestrator.McpOrchestratorService(agent=mock_agent, provider=None)
        
        # Valid PDF read
        args_pdf = {"document_id": str(self.pdf_upload.id)}
        name_pdf, _ = service._adaptive_routing_policy("read_document", args_pdf, self.conversation)
        self.assertEqual(name_pdf, "read_knowledge")

        # Valid Dataset query
        args_ds = {"dataset_id": str(self.dataset_upload.id)}
        name_ds, _ = service._adaptive_routing_policy("query_dataset", args_ds, self.conversation)
        self.assertEqual(name_ds, "query_dataset")

    def test_read_knowledge_ref_repair_uses_single_recent_ref(self) -> None:
        mock_agent = mock.Mock()
        mock_agent.business_profile = self.business
        service = orchestrator.McpOrchestratorService(agent=mock_agent, provider=None)

        context = ToolExecutionContext()
        context.hydrate_recent_search_refs(
            {
                "refs": [
                    {
                        "id": "03669f1f-7eab-4b7f-aff9-771dcd6bbea8",
                        "label": "Fees and Charges Credit Cards Eng_185 - chunk 9",
                        "kind": "table_chunk",
                        "document_id": "6bfaa7d3-0969-4293-aa41-9674581daa14",
                    }
                ]
            }
        )

        repaired = service._repair_read_knowledge_refs_from_context(
            {"refs": [{"id": "doc_credit_cards_fees_charges_2025"}]},
            context,
        )
        refs = repaired.get("refs") if isinstance(repaired.get("refs"), list) else []
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].get("id"), "03669f1f-7eab-4b7f-aff9-771dcd6bbea8")

    def test_read_knowledge_ref_repair_keeps_invalid_id_when_ambiguous(self) -> None:
        mock_agent = mock.Mock()
        mock_agent.business_profile = self.business
        service = orchestrator.McpOrchestratorService(agent=mock_agent, provider=None)

        context = ToolExecutionContext()
        context.hydrate_recent_search_refs(
            {
                "refs": [
                    {
                        "id": "03669f1f-7eab-4b7f-aff9-771dcd6bbea8",
                        "document_id": "6bfaa7d3-0969-4293-aa41-9674581daa14",
                    },
                    {
                        "id": "59b047e5-1375-4e1f-8f9a-f0e8d0ff06f0",
                        "document_id": "11111111-2222-3333-4444-555555555555",
                    },
                ]
            }
        )

        repaired = service._repair_read_knowledge_refs_from_context(
            {"refs": [{"id": "doc_credit_cards_fees_charges_2025"}]},
            context,
        )
        refs = repaired.get("refs") if isinstance(repaired.get("refs"), list) else []
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].get("id"), "doc_credit_cards_fees_charges_2025")
