from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from apps.rag.observability.quality_monitor import QualityMonitor


class QualityMonitorQueryAnalyticsTests(SimpleTestCase):
    def test_retrieval_sample_includes_query_analytics_fields(self) -> None:
        business = SimpleNamespace(id="biz-1")
        diagnostics = {
            "snippet_count": 0,
            "original_query": "where is invoice INV-123?",
            "normalized_query": "invoice inv 123",
            "query_intent": "invoice_lookup",
            "reason": "not_found",
            "token_count": 3,
            "identifier_like": True,
        }

        with (
            mock.patch("apps.rag.observability.quality_monitor.KnowledgeDriftSample.objects.create") as create_mock,
            mock.patch.object(QualityMonitor, "_check_alias_hit_rate"),
            mock.patch.object(QualityMonitor, "_check_not_found_spike"),
        ):
            QualityMonitor.record_retrieval_sample(
                business_profile=business,
                query_type="identifier",
                alias_hit=False,
                fallback_used=True,
                latency_ms=155,
                stage="fallback",
                diagnostics=diagnostics,
                result_status="not_found",
            )

        self.assertTrue(create_mock.called)
        kwargs = create_mock.call_args.kwargs
        metrics = kwargs.get("metrics") or {}
        metadata = kwargs.get("metadata") or {}

        self.assertEqual(metrics.get("status"), "not_found")
        self.assertEqual(metrics.get("result_count"), 0)
        self.assertEqual(metrics.get("query_intent"), "invoice_lookup")
        self.assertEqual(metrics.get("error_code"), "not_found")
        self.assertEqual(metrics.get("source"), "rag_search")

        query = (metadata.get("query") or {}) if isinstance(metadata, dict) else {}
        self.assertTrue(bool(query.get("fingerprint")))
        self.assertEqual(query.get("query_length"), len("where is invoice INV-123?"))
