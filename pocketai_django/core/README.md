Core Module (core/)
===================

Purpose
-------
Shared runtime utilities that don’t belong to a single Django app:
lightweight latency logging and OpenTelemetry tracing setup.

Directory Map
-------------
- metrics.py
  In-process latency sampling + periodic p50/p95 logs.
- tracing.py
  OpenTelemetry OTLP exporter config + Django/requests/logging instrumentation.

Key Flows
---------
1) Latency monitoring
   latency_monitor.observe(stage, duration_ms, tags) -> periodic p50/p95 logs.

2) Tracing bootstrap
   configure_tracing() -> configures OTLP exporter + Django/HTTP instrumentation.

Configuration Touchpoints
-------------------------
- OTEL_TRACING_ENABLED (true/false)
- OTEL_EXPORTER_OTLP_ENDPOINT
- OTEL_EXPORTER_OTLP_HEADERS
- OTEL_SERVICE_NAME / OTEL_SERVICE_VERSION / OTEL_ENVIRONMENT

Quick Start (Dev)
----------------
Disable tracing locally:
```
OTEL_TRACING_ENABLED=false
```

Examples
--------
Record latency:
```python
from core.metrics import latency_monitor
latency_monitor.observe("mcp.tool", 120, tags={"tool": "read_knowledge"})
```

Enable tracing:
```python
from core.tracing import configure_tracing
configure_tracing()
```

Troubleshooting
---------------
- Command hangs due to tracing:
  - Disable tracing via OTEL_TRACING_ENABLED=false.
- No traces in collector:
  - Verify OTLP endpoint + headers and that collector is running.

Where To Start (Reading Order)
------------------------------
1) `core/tracing.py`
2) `core/metrics.py`
