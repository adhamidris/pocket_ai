#!/bin/bash
# ==============================================================================
# WORKER ENTRYPOINT - Django Ingestion Worker
# ==============================================================================
# Startup script for background worker (file ingestion)

set -e  # Exit on error

echo "🔧 Starting PocketAI Worker Service..."

# Wait for postgres to be ready
echo "⏳ Waiting for PostgreSQL..."
while ! pg_isready -h "${POSTGRES_HOST:-postgres}" -p "${POSTGRES_PORT:-5432}" -U "${POSTGRES_USER:-pocketai}" > /dev/null 2>&1; do
    sleep 1
done
echo "✅ PostgreSQL is ready"

# Wait for Redis (if configured)
if [ -n "$REDIS_URL" ]; then
    echo "⏳ Waiting for Redis..."
    until redis-cli -u "$REDIS_URL" ping > /dev/null 2>&1; do
        sleep 1
    done
    echo "✅ Redis is ready"
fi

# Warm embeddings cache
if [ "${EMBED_PROVIDER:-local}" = "local" ]; then
    echo "🔥 Warming embeddings cache..."
    python manage.py warm_embeddings || echo "⚠️  Warning: Failed to warm embeddings cache"
fi

echo "⚙️  Starting knowledge ingestion worker..."
exec python manage.py knowledge_ingestion_worker --watch
