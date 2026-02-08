from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

import requests
from django.test import SimpleTestCase

from apps.knowledge.knowledge_ingestion import AzureDocumentIntelligenceExtractor


def _mock_response(
    *,
    status_code: int,
    payload: dict | None = None,
    headers: dict | None = None,
    text: str = "",
):
    response = mock.Mock()
    response.status_code = status_code
    response.headers = headers or {}
    response.text = text
    if payload is None:
        response.json.side_effect = ValueError("no json")
    else:
        response.json.return_value = payload
    return response


class AzureDocumentIntelligenceExtractorTests(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        handle.write(b"%PDF-1.4\n% phase-one retry test\n")
        handle.flush()
        handle.close()
        self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))
        self.path = Path(handle.name)

    @mock.patch("apps.knowledge.knowledge_ingestion.time.sleep")
    @mock.patch("apps.knowledge.knowledge_ingestion.requests.get")
    @mock.patch("apps.knowledge.knowledge_ingestion.requests.post")
    def test_submit_retries_on_throttle_with_retry_after(
        self,
        mock_post,
        mock_get,
        mock_sleep,
    ) -> None:
        mock_post.side_effect = [
            _mock_response(
                status_code=429,
                headers={"retry-after": "2"},
                text="throttled",
            ),
            _mock_response(
                status_code=202,
                headers={"operation-location": "https://example.test/op/123"},
            ),
        ]
        mock_get.return_value = _mock_response(
            status_code=200,
            payload={"status": "succeeded", "analyzeResult": {"pages": [], "tables": []}},
        )

        extractor = AzureDocumentIntelligenceExtractor(
            endpoint="https://example.test",
            key="secret",
            request_max_attempts=3,
            poll_request_max_attempts=2,
            retry_backoff_base_seconds=0.1,
            retry_backoff_max_seconds=0.2,
            max_retry_after_seconds=5.0,
            poll_interval_seconds=2.0,
            max_polls=5,
        )

        analyze_result, issues, meta = extractor._analyze_document(self.path)

        self.assertEqual(analyze_result, {"pages": [], "tables": []})
        self.assertEqual(issues, [])
        self.assertEqual(meta.get("status"), "succeeded")
        self.assertEqual(meta.get("request_attempts"), 2)

        retry_events = meta.get("retry_events") or []
        self.assertTrue(retry_events)
        first_retry = retry_events[0]
        self.assertEqual(first_retry.get("phase"), "submit")
        self.assertEqual(first_retry.get("reason"), "submit_http_retry")
        self.assertEqual(first_retry.get("status_code"), 429)
        self.assertGreaterEqual(float(first_retry.get("delay_s") or 0.0), 2.0)
        self.assertTrue(mock_sleep.called)

    @mock.patch("apps.knowledge.knowledge_ingestion.time.sleep")
    @mock.patch("apps.knowledge.knowledge_ingestion.requests.get")
    @mock.patch("apps.knowledge.knowledge_ingestion.requests.post")
    def test_poll_timeout_is_classified(self, mock_post, mock_get, _mock_sleep) -> None:
        mock_post.return_value = _mock_response(
            status_code=202,
            headers={"operation-location": "https://example.test/op/456"},
        )
        mock_get.side_effect = [
            requests.Timeout("poll timeout #1"),
            requests.Timeout("poll timeout #2"),
        ]

        extractor = AzureDocumentIntelligenceExtractor(
            endpoint="https://example.test",
            key="secret",
            request_max_attempts=1,
            poll_request_max_attempts=2,
            retry_backoff_base_seconds=0.1,
            retry_backoff_max_seconds=0.2,
            poll_interval_seconds=2.0,
            max_polls=3,
        )

        analyze_result, issues, meta = extractor._analyze_document(self.path)

        self.assertIsNone(analyze_result)
        self.assertTrue(issues)
        self.assertEqual(issues[0].code, "azure_di_poll_failed")
        self.assertEqual(meta.get("status"), "timeout")
        self.assertEqual(meta.get("failure_class"), "timeout")
        self.assertEqual(meta.get("failure_stage"), "poll")
        self.assertEqual(meta.get("failure_reason"), "poll_exception")

    @mock.patch("apps.knowledge.knowledge_ingestion.time.sleep")
    @mock.patch("apps.knowledge.knowledge_ingestion.requests.post")
    def test_submit_hard_failure_is_classified(self, mock_post, mock_sleep) -> None:
        mock_post.return_value = _mock_response(
            status_code=400,
            text="bad request",
            payload={"error": {"code": "InvalidRequest"}},
        )

        extractor = AzureDocumentIntelligenceExtractor(
            endpoint="https://example.test",
            key="secret",
            request_max_attempts=3,
        )
        analyze_result, issues, meta = extractor._analyze_document(self.path)

        self.assertIsNone(analyze_result)
        self.assertTrue(issues)
        self.assertEqual(issues[0].code, "azure_di_request_error")
        self.assertEqual(meta.get("status"), "failed")
        self.assertEqual(meta.get("failure_class"), "hard_failure")
        self.assertEqual(meta.get("failure_stage"), "submit")
        self.assertEqual(meta.get("failure_reason"), "request_http_error")
        self.assertEqual(meta.get("request_attempts"), 1)
        self.assertFalse(mock_sleep.called)
