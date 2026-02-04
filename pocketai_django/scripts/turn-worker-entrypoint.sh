#!/bin/bash
# ==============================================================================
# TURN WORKER ENTRYPOINT - Portal Turn Worker
# ==============================================================================
# Startup script for background worker (portal turns)

set -e  # Exit on error

echo "🔧 Starting PocketAI Portal Turn Worker..."

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

echo "⚙️  Starting portal turn worker..."
exec python manage.py process_portal_turns --watch

