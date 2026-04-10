#!/usr/bin/env bash
set -euo pipefail

docker rm -f otel-collector jaeger >/dev/null 2>&1 || true
echo "Stopped tracing containers: otel-collector, jaeger"
