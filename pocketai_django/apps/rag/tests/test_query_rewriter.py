from __future__ import annotations

from django.test import SimpleTestCase

from apps.rag.query.rewriter import ContextAwareQueryRewriter, RewriteContext, get_query_rewriter


class QueryRewriterTests(SimpleTestCase):
    def test_document_continuity_rewriter_never_injects_file_context(self) -> None:
        rewriter = ContextAwareQueryRewriter(enabled=True, prefix_mode=True, min_confidence=0.5)
        context = RewriteContext(
            primary_document_title="Fees and Charges Credit Cards Eng_185",
            previous_queries=("list me all credit cards and their issuance fees",),
        )

        result = rewriter.rewrite("all credit card issuance fees", context)

        self.assertFalse(result.context_injected)
        self.assertEqual(result.rewritten_query, "all credit card issuance fees")
        self.assertEqual(result.rewrite_strategy, "disabled")
        self.assertEqual(result.reason, "document_continuity_removed")

    def test_configured_rewriter_stays_disabled_even_if_settings_enable_it(self) -> None:
        rewriter = get_query_rewriter()
        context = RewriteContext(primary_document_title="Trade Bills EN")

        result = rewriter.rewrite("what about fees?", context)

        self.assertFalse(result.context_injected)
        self.assertEqual(result.rewrite_strategy, "disabled")
        self.assertEqual(result.rewritten_query, "what about fees?")
