from __future__ import annotations

from django.test import SimpleTestCase

from apps.rag.query.analytics import build_query_analytics_report


class QueryAnalyticsReportingTests(SimpleTestCase):
    def test_build_report_aggregates_status_latency_and_errors(self) -> None:
        samples = [
            {
                "metrics": {
                    "status": "ok",
                    "query_type": "natural",
                    "query_intent": "lookup",
                    "stage": "hybrid",
                    "result_count": 3,
                    "latency_ms": 120,
                    "source": "rag_search",
                }
            },
            {
                "metrics": {
                    "status": "not_found",
                    "query_type": "natural",
                    "query_intent": "lookup",
                    "stage": "fallback",
                    "result_count": 0,
                    "latency_ms": 180,
                    "error_code": "not_found",
                    "source": "rag_search",
                }
            },
            {
                "metrics": {
                    "status": "throttled",
                    "query_type": "identifier",
                    "query_intent": "invoice_id",
                    "stage": "alias_exact",
                    "result_count": 0,
                    "latency_ms": 90,
                    "error_code": "rate_limited",
                    "source": "rag_search",
                }
            },
        ]

        report = build_query_analytics_report(samples)

        self.assertEqual(report.get("total_queries"), 3)
        status = report.get("status") or {}
        self.assertEqual((status.get("counts") or {}).get("ok"), 1)
        self.assertEqual((status.get("counts") or {}).get("not_found"), 1)
        self.assertEqual((status.get("counts") or {}).get("throttled"), 1)
        self.assertEqual((report.get("empty_results") or {}).get("count"), 1)
        self.assertEqual((report.get("by_error_code") or {}).get("not_found"), 1)
        self.assertEqual((report.get("by_error_code") or {}).get("rate_limited"), 1)
        latency = report.get("latency_ms") or {}
        self.assertEqual(latency.get("count"), 3)
        self.assertIsNotNone(latency.get("p50"))
        self.assertIsNotNone(latency.get("p95"))
