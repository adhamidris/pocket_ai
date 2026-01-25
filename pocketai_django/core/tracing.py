from __future__ import annotations

import os
import threading
from typing import Mapping

try:  # pragma: no cover - optional dependency
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
except Exception:  # pragma: no cover - allow running without opentelemetry installed

    def configure_tracing() -> None:
        return

else:
    _CONFIGURED = False
    _LOCK = threading.Lock()

    def _is_enabled() -> bool:
        return os.getenv("OTEL_TRACING_ENABLED", "true").strip().lower() in {"1", "true", "yes"}

    def _exporter() -> OTLPSpanExporter:
        """
        Build an OTLP exporter pointed at the configured collector.

        Defaults to localhost gRPC endpoint so Jaeger all-in-one works out of the box.
        """

        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        headers_raw = os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")
        headers: Mapping[str, str] | None = None
        if headers_raw:
            header_pairs = {}
            for pair in headers_raw.split(","):
                if not pair:
                    continue
                if "=" not in pair:
                    continue
                key, value = pair.split("=", 1)
                if key and value:
                    header_pairs[key.strip()] = value.strip()
            if header_pairs:
                headers = header_pairs
        insecure = endpoint.startswith("http://")
        return OTLPSpanExporter(endpoint=endpoint, headers=headers, insecure=insecure)

    def configure_tracing() -> None:
        """
        Configure OpenTelemetry tracing for Django + outbound HTTP/DB calls.

        Safe to call multiple times; the first invocation wins.
        """

        global _CONFIGURED
        if _CONFIGURED or not _is_enabled():
            return

        with _LOCK:
            if _CONFIGURED:
                return
            resource = Resource.create(
                {
                    "service.name": os.getenv("OTEL_SERVICE_NAME", "pocketai-django"),
                    "service.version": os.getenv("OTEL_SERVICE_VERSION", "dev"),
                    "deployment.environment": os.getenv("OTEL_ENVIRONMENT", "development"),
                }
            )
            provider = TracerProvider(resource=resource)
            processor = BatchSpanProcessor(_exporter())
            provider.add_span_processor(processor)
            trace.set_tracer_provider(provider)

            DjangoInstrumentor().instrument()
            RequestsInstrumentor().instrument()
            LoggingInstrumentor().instrument(set_logging_format=True)

            _CONFIGURED = True
