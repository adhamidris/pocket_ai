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
