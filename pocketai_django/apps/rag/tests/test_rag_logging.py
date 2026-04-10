from __future__ import annotations

import io
import logging

from django.test import SimpleTestCase

from apps.rag import rag_logging


class RagLoggingOtelFieldsTests(SimpleTestCase):
    def test_structured_log_populates_otel_formatter_fields(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(
            logging.Formatter(
                "%(otelTraceID)s %(otelSpanID)s %(otelServiceName)s %(otelTraceSampled)s %(message)s"
            )
        )

        logger = logging.getLogger("tests.rag_logging.otel")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False

        try:
            rag_logging.structured_log(
                "rag",
                "table.search_snippets_entry",
                {"query": "fees"},
                logger_obj=logger,
            )
        finally:
            logger.handlers = []

        output = stream.getvalue()
        self.assertIn("pocketai-django", output)
        self.assertIn("TABLE.SEARCH_SNIPPETS_ENTRY", output)
