#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$ROOT/pocketai_django"
OTEL_CONFIG="$APP_DIR/ops/otel-collector.yaml"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required but was not found in PATH" >&2
  exit 1
fi

if [ ! -f "$OTEL_CONFIG" ]; then
  echo "OpenTelemetry collector config not found: $OTEL_CONFIG" >&2
  exit 1
fi

if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  echo "Expected virtualenv python at $APP_DIR/.venv/bin/python" >&2
  exit 1
fi

docker rm -f jaeger otel-collector >/dev/null 2>&1 || true
docker network create pocketai_network >/dev/null 2>&1 || true

docker run -d \
  --name jaeger \
  --network pocketai_network \
  -e COLLECTOR_OTLP_ENABLED=true \
  -p 16686:16686 \
  jaegertracing/all-in-one:latest >/dev/null

docker run -d \
  --name otel-collector \
  --network pocketai_network \
  -p 4317:4317 \
  -p 4318:4318 \
  -v "$OTEL_CONFIG:/etc/otelcol/config.yaml:ro" \
  otel/opentelemetry-collector-contrib:latest \
  --config /etc/otelcol/config.yaml >/dev/null

export OTEL_TRACING_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
export OTEL_SERVICE_NAME=pocketai-django
export OTEL_SERVICE_VERSION=dev
export OTEL_ENVIRONMENT=local
export PORTAL_STREAM_TRACE=true
export PORTAL_DEBUG_TOOL_TRACE=true

echo "Jaeger UI: http://localhost:16686"
echo "Starting Django with tracing enabled..."

cd "$APP_DIR"
exec .venv/bin/python manage.py runserver
