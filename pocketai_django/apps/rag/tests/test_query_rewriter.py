from __future__ import annotations

from django.test import SimpleTestCase

from apps.rag.query_rewriter import ContextAwareQueryRewriter, RewriteContext


class QueryRewriterTests(SimpleTestCase):
    def test_rewrites_short_followup_without_explicit_indicator(self) -> None:
        rewriter = ContextAwareQueryRewriter(enabled=True, prefix_mode=True, min_confidence=0.5)
        context = RewriteContext(
            primary_document_title="Trade Bills EN",
            previous_queries=("What are the fees for Postpone Bills for Guarantee?",),
        )
        result = rewriter.rewrite("Withdraw Bills for Collection fees", context)
        self.assertTrue(result.context_injected)
        self.assertEqual(result.rewrite_strategy, "prefix")
        self.assertTrue(result.rewritten_query.startswith("Trade Bills EN: "))

    def test_does_not_rewrite_new_topic_short_query(self) -> None:
        rewriter = ContextAwareQueryRewriter(enabled=True, prefix_mode=True, min_confidence=0.5)
        context = RewriteContext(
            primary_document_title="Trade Bills EN",
            previous_queries=("What are the fees for Postpone Bills for Guarantee?",),
        )
        result = rewriter.rewrite("Credit card fees", context)
        self.assertFalse(result.context_injected)
        self.assertEqual(result.rewrite_strategy, "topic_shift")

    def test_generic_fee_overlap_does_not_rewrite_cross_topic_query(self) -> None:
        rewriter = ContextAwareQueryRewriter(enabled=True, prefix_mode=True, min_confidence=0.5)
        context = RewriteContext(
            primary_document_title="Fees and Charges Credit Cards Eng_185",
            previous_queries=("list me all credit cards and their issuance fees",),
        )

        result = rewriter.rewrite("account opening fees", context)

        self.assertFalse(result.context_injected)
        self.assertEqual(result.rewrite_strategy, "topic_shift")
        self.assertEqual(result.reason, "topic_shift")

    def test_explicit_followup_marker_can_rewrite_generic_fee_query(self) -> None:
        rewriter = ContextAwareQueryRewriter(enabled=True, prefix_mode=True, min_confidence=0.5)
        context = RewriteContext(
            primary_document_title="Fees and Charges Credit Cards Eng_185",
            previous_queries=("list me all credit cards and their issuance fees",),
        )

        result = rewriter.rewrite("what about replacement fees?", context)

        self.assertTrue(result.context_injected)
        self.assertEqual(result.rewrite_strategy, "prefix")
        self.assertTrue(result.rewritten_query.startswith("Fees and Charges Credit Cards Eng_185: "))

    def test_does_not_rewrite_when_query_mentions_primary_title(self) -> None:
        rewriter = ContextAwareQueryRewriter(enabled=True, prefix_mode=True, min_confidence=0.5)
        context = RewriteContext(primary_document_title="Trade Bills EN")
        result = rewriter.rewrite("Trade Bills EN withdrawal fees", context)
        self.assertFalse(result.context_injected)
        self.assertEqual(result.rewrite_strategy, "already_contextual")

    def test_no_context_no_rewrite(self) -> None:
        rewriter = ContextAwareQueryRewriter(enabled=True, prefix_mode=True, min_confidence=0.5)
        context = RewriteContext(primary_document_title=None)
        result = rewriter.rewrite("withdrawal fees", context)
        self.assertFalse(result.context_injected)
        self.assertEqual(result.rewrite_strategy, "no_context")

    def test_lexicon_overlap_can_trigger_followup_rewrite(self) -> None:
        rewriter = ContextAwareQueryRewriter(enabled=True, prefix_mode=True, min_confidence=0.5)
        context = RewriteContext(
            primary_document_title="Ops Playbook",
            previous_queries=("Show service request workflow",),
            tenant_entity_terms=("service request",),
            tenant_attribute_terms=("resolution time",),
        )
        result = rewriter.rewrite("resolution time by service request", context)
        self.assertTrue(result.context_injected)
        self.assertEqual(result.rewrite_strategy, "prefix")

    def test_arabic_followup_indicator_rewrites(self) -> None:
        rewriter = ContextAwareQueryRewriter(enabled=True, prefix_mode=True, min_confidence=0.5)
        context = RewriteContext(
            primary_document_title="دليل الفوترة",
            previous_queries=("ما هي رسوم الإعداد؟",),
        )
        result = rewriter.rewrite("وماذا عن رسوم السحب؟", context)
        self.assertTrue(result.context_injected)
        self.assertEqual(result.rewrite_strategy, "prefix")

    def test_arabic_document_mention_is_detected_as_contextual(self) -> None:
        rewriter = ContextAwareQueryRewriter(enabled=True, prefix_mode=True, min_confidence=0.5)
        context = RewriteContext(primary_document_title="إِجْمالِيّ المُبيـعات")
        result = rewriter.rewrite("ما اجمالي المبيعات؟", context)
        self.assertFalse(result.context_injected)
        self.assertEqual(result.rewrite_strategy, "already_contextual")
