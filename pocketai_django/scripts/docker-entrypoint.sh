#!/bin/bash
# ==============================================================================
# DOCKER ENTRYPOINT - Django Web Application
# ==============================================================================
# Production startup script for the web service
# Runs migrations, collects static files, and starts gunicorn

set -e  # Exit on error

echo "🚀 Starting PocketAI Web Service..."

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

# Run database migrations (optional, controlled by env var)
if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "🔄 Running database migrations..."
    python manage.py migrate --noinput
    echo "✅ Migrations complete"
fi

# Collect static files
if [ "${COLLECT_STATIC:-true}" = "true" ]; then
    echo "📦 Collecting static files..."
    python manage.py collectstatic --noinput --clear
    echo "✅ Static files collected"
fi

# Warm embeddings cache (pre-download models)
if [ "${EMBED_PROVIDER:-local}" = "local" ]; then
    echo "🔥 Warming embeddings cache..."
    python manage.py warm_embeddings || echo "⚠️  Warning: Failed to warm embeddings cache"
fi

# Create superuser if credentials provided (first run only)
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ] && [ -n "$DJANGO_SUPERUSER_EMAIL" ]; then
    echo "👤 Creating superuser..."
    python manage.py shell << EOF
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='$DJANGO_SUPERUSER_USERNAME').exists():
    User.objects.create_superuser('$DJANGO_SUPERUSER_USERNAME', '$DJANGO_SUPERUSER_EMAIL', '$DJANGO_SUPERUSER_PASSWORD')
    print('✅ Superuser created')
else:
    print('ℹ️  Superuser already exists')
EOF
fi

echo "🎯 Starting gunicorn..."
exec gunicorn pocketai.wsgi:application \
    --config gunicorn.conf.py \
    --bind 0.0.0.0:${PORT:-8000}
