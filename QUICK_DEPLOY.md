# 🚀 Quick Deploy Guide

This file provides quick commands to deploy your multi-tenant AI platform using Docker.

## Prerequisites

- Docker and Docker Compose installed
- PostgreSQL with pgvector (handled by docker-compose)
- Environment variables configured in `.env`

## Quick Start (Local/Staging)

```bash
# 1. Build and start all services
docker-compose up -d

# 2. Run migrations
docker-compose exec web python manage.py migrate

# 3. Create superuser
docker-compose exec web python manage.py createsuperuser

# 4. Access the application
open http://localhost:8000
```

## Production Deployment

See [`PRODUCTION_DEPLOYMENT.md`](./PRODUCTION_DEPLOYMENT.md) for complete guide.

### Railway (Recommended for MVP)

```bash
railway init
railway add --plugin postgresql
railway add --plugin redis
railway up
```

### Render

1. Connect GitHub repository
2. Select "Docker" deployment
3. Add PostgreSQL and Redis
4. Deploy

### Fly.io

```bash
fly launch
fly postgres create
fly deploy
```

## Monitoring

```bash
# View logs
docker-compose logs -f web

# Check service health
docker-compose ps

# Stop all services
docker-compose down
```

For detailed deployment instructions, troubleshooting, and platform-specific tips, see [`PRODUCTION_DEPLOYMENT.md`](./PRODUCTION_DEPLOYMENT.md).
