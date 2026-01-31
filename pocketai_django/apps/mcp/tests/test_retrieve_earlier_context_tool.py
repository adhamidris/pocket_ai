from __future__ import annotations

from unittest import mock

from django.test import TestCase

from apps.accounts.models import BusinessProfile, RegistrationSession, User
from apps.conversations.models import CompactedHistorySegment, Conversation
from apps.mcp import tools
from apps.mcp.types import ToolExecutionContext
from core.tenancy import tenant_context


class _StubEmbedder:
    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def embed_text(self, text: str) -> list[float]:
        return list(self._vector)


class RetrieveEarlierContextToolTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="memory@example.com", first_name="Memory")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Memory Co",
            industry="general",
        )
        self.tenant_scope = tenant_context(self.business.id)
        self.tenant_scope.__enter__()
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            session_token="session-memory",
        )

    def tearDown(self) -> None:
        if hasattr(self, "tenant_scope"):
            self.tenant_scope.__exit__(None, None, None)
        super().tearDown()

    def test_semantic_retrieval_picks_best_segment(self) -> None:
        dim = int(getattr(tools.settings, "EMBED_DIM", 384) or 384)
        query_vector = [0.0] * dim
        query_vector[0] = 1.0

        seg1_vec = [0.0] * dim
        seg1_vec[0] = 1.0  # perfect match
        seg2_vec = [0.0] * dim
        seg2_vec[1] = 1.0  # orthogonal

        seg1 = CompactedHistorySegment.objects.create(
            conversation=self.conversation,
            segment_range="turns_1_to_10",
            start_message_id=tools.uuid.uuid4(),
            end_message_id=tools.uuid.uuid4(),
            summary="Gold card issuance fee is EGP 300.",
            full_messages=[{"id": "m1", "sender": "ai", "body": "Fee: EGP 300", "metadata": {}}],
            embedding=seg1_vec,
            extracted_facts={},
            extracted_decisions={},
            token_count_original=100,
            token_count_summary=10,
            compression_ratio=0.1,
        )
        CompactedHistorySegment.objects.create(
            conversation=self.conversation,
            segment_range="turns_11_to_20",
            start_message_id=tools.uuid.uuid4(),
            end_message_id=tools.uuid.uuid4(),
            summary="Unrelated content about loans.",
            full_messages=[{"id": "m2", "sender": "ai", "body": "Loan rates ...", "metadata": {}}],
            embedding=seg2_vec,
            extracted_facts={},
            extracted_decisions={},
            token_count_original=100,
            token_count_summary=10,
            compression_ratio=0.1,
        )

        with mock.patch("apps.mcp.tools._portal_file_embedding_service", return_value=_StubEmbedder(query_vector)):
            result = tools.execute_tool(
                "retrieve_earlier_context",
                {"query": "gold card issuance fee"},
                conversation=self.conversation,
                context=ToolExecutionContext(),
            )

        self.assertEqual(result.get("status"), "ok")
        self.assertTrue(result.get("found"))
        self.assertEqual(result.get("match_method"), "semantic")
        self.assertEqual(result.get("segment_id"), str(seg1.id))

    def test_direct_segment_id_can_return_full_segment(self) -> None:
        dim = int(getattr(tools.settings, "EMBED_DIM", 384) or 384)
        seg_vec = [0.0] * dim
        seg_vec[0] = 1.0

        seg = CompactedHistorySegment.objects.create(
            conversation=self.conversation,
            segment_range="turns_1_to_5",
            start_message_id=tools.uuid.uuid4(),
            end_message_id=tools.uuid.uuid4(),
            summary="Earlier segment.",
            full_messages=[{"id": "m1", "sender": "ai", "body": "hello", "metadata": {}}],
            embedding=seg_vec,
            extracted_facts={},
            extracted_decisions={},
            token_count_original=100,
            token_count_summary=10,
            compression_ratio=0.1,
        )

        result = tools.execute_tool(
            "retrieve_earlier_context",
            {"segment_id": str(seg.id), "include_full_segment": True},
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )
        self.assertEqual(result.get("status"), "ok")
        self.assertTrue(result.get("found"))
        self.assertEqual(result.get("match_method"), "direct")
        self.assertEqual(result.get("segment_id"), str(seg.id))
        self.assertEqual(len(result.get("messages") or []), 1)

