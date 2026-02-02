# 🚀 Production Deployment Guide - PocketAI

**Multi-Tenant B2B SaaS Platform for AI-Powered Support/Sales Agents**

This guide will walk you through deploying your RAG-powered, multi-tenant AI assistant platform to production. Each business (tenant) gets their own AI agent that searches their knowledge and manages CRM objects.

---

## 📋 Pre-Flight Checklist

Before deploying, ensure you have:

- [ ] PostgreSQL 15+ database with **pgvector extension** enabled
- [ ] Redis 7+ instance for caching
- [ ] LLM API key (OpenAI or DeepSeek)
- [ ] Domain name configured
- [ ] SSL certificate (or use platform-managed SSL)
- [ ] S3-compatible storage for file uploads (optional but recommended)

---

## 🔐 Environment Variables

Create a `.env` file in the project root. Use `.env.example` as a template.

### Core Django Settings

```bash
# Security (REQUIRED for production)
DJANGO_DEBUG=false                    # NEVER set to 'true' in production
DJANGO_SECRET_KEY=your-secret-key-min-50-chars-random-string-here
ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com,api.yourdomain.com

# Database (PostgreSQL with pgvector)
DATABASE_URL=postgresql://user:password@host:5432/dbname
# OR individual components:
POSTGRES_DB=pocket ai
POSTGRES_USER=pocketai_prod
POSTGRES_PASSWORD=your-secure-password-here
POSTGRES_HOST=your-postgres-host.com
POSTGRES_PORT=5432

# Redis Caching
REDIS_URL=redis://your-redis-host:6379/0
```

### LLM Provider (Choose one)

```bash
# OpenAI
OPENAI_API_KEY=sk-proj-your-key-here
MCP_PROVIDER=openai

# OR DeepSeek (cheaper alternative)
DEEPSEEK_API_KEY=your-deepseek-key-here
MCP_PROVIDER=deepseek
```

### Embedding Provider

```bash
# Option 1: Local embeddings (FREE, but needs 2GB RAM)
EMBED_PROVIDER=local
EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
EMBED_DIM=384

# Option 2: OpenAI embeddings (PAID, lighter on resources)
EMBED_PROVIDER=openai
EMBED_MODEL=text-embedding-3-small
EMBED_DIM=1536
OPENAI_API_KEY=sk-proj-your-key-here
```

### Security Middleware (HTTPS)

```bash
# Enable HTTPS enforcement (set to 'true' in production ONLY after SSL is working)
SECURE_SSL_REDIRECT=true
SECURE_HSTS_SECONDS=31536000          # 1 year
SESSION_COOKIE_SECURE=true
CSRF_COOKIE_SECURE=true
```

### Error Monitoring (Highly Recommended)

```bash
# Sentry Error Tracking (https://sentry.io)
SENTRY_DSN=https://your-key@sentry.io/your-project-id
SENTRY_ENVIRONMENT=production  # or staging, development
SENTRY_RELEASE=v1.0.0  # Your app version (optional)
SENTRY_TRACES_SAMPLE_RATE=0.1  # 10% of requests for performance monitoring
SENTRY_PROFILES_SAMPLE_RATE=0.1  # 10% of traces for profiling
```

**FREE Sentry Setup** (5 minutes):
1. Sign up at [sentry.io](https://sentry.io) (free tier: 5K errors/month)
2. Create new project → Select "Django"
3. Copy your DSN
4. Set `SENTRY_DSN` environment variable
5. Errors will auto-appear in your Sentry dashboard!

### Optional Services

```bash
# Google Drive Integration (optional)
GOOGLE_OAUTH_CLIENT_ID=your-client-id
GOOGLE_OAUTH_CLIENT_SECRET=your-secret
GOOGLE_OAUTH_REDIRECT_URI=https://yourdomain.com/api/integrations/google/callback/

# Email notifications (optional, for case alerts)
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.sendgrid.net
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=apikey
EMAIL_HOST_PASSWORD=your-sendgrid-api-key

# S3 Storage for uploads (recommended for production)
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_STORAGE_BUCKET_NAME=pocketai-uploads
AWS_S3_REGION_NAME=us-east-1
USE_S3_STORAGE=true
```

---

## 🗄️ Database Setup

### Step 1: Create PostgreSQL Database with pgvector

**On your PostgreSQL server (15+):**

```sql
-- Connect as superuser
CREATE DATABASE pocketai;
CREATE USER pocketai_prod WITH PASSWORD 'your-secure-password';
ALTER DATABASE pocketai OWNER TO pocketai_prod;

-- Enable pgvector extension
\c pocketai
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- For text search

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE pocketai TO pocketai_prod;
```

### Step 2: Verify pgvector is Installed

```bash
psql "postgresql://pocketai_prod:password@host:5432/pocketai" -c "SELECT * FROM pg_extension WHERE extname = 'vector';"
```

You should see `vector | 0.7.0` or similar.

###Step 3: Run Django Migrations

```bash
cd pocketai_django
python manage.py migrate
```

**Expected output:** All migrations apply successfully (~30-40 migrations).

---

## 🐳 Docker Deployment (Recommended)

### Option 1: Using Docker Compose (Easiest for Self-Hosting)

**1. Build the Docker image:**

```bash
docker build -t pocketai:latest -f pocketai_django/Dockerfile .
```

**2. Start services:**

```bash
docker-compose up -d
```

This starts:
- Django web server (gunicorn)
- PostgreSQL with pgvector
- Redis
- Ingestion worker (Django management command)

**3. Run migrations:**

```bash
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py createsuperuser
```

**4. Collect static files:**

```bash
docker-compose exec web python manage.py collectstatic --noinput
```

### Option 2: Platform-as-a-Service (Easiest for MVP)

Choose one of these platforms for quick deployment:

#### 🚂 Railway.app (Best for MVP)

**Why Railway?**
- PostgreSQL with pgvector ✅
- Redis add-on ✅
- Auto-SSL ✅
- $5/month free credit
- Easy environment variable management

**Steps:**

1. **Install Railway CLI:**
   ```bash
   npm install -g @railway/cli
   railway login
   ```

2. **Initialize project:**
   ```bash
   cd /path/to/pocket_ai-main\ 2/
   railway init
   ```

3. **Add PostgreSQL + Redis:**
   ```bash
   railway add --plugin postgresql
   railway add --plugin redis
   ```

4. **Set environment variables:**
   ```bash
   railway variables set DJANGO_SECRET_KEY="your-secret-key-here"
   railway variables set ALLOWED_HOSTS="your-app.railway.app"
   railway variables set DJANGO_DEBUG=false
   railway variables set OPENAI_API_KEY="sk-your-key"
   ```

5. **Deploy:**
   ```bash
   railway up
   ```

6. **Run migrations:**
   ```bash
   railway run python pocketai_django/manage.py migrate
   railway run python pocketai_django/manage.py createsuperuser
   ```

#### 🎨 Render.com (Good Alternative)

1. Create a new "Web Service" from GitHub
2. Choose "Docker" as the environment
3. Add PostgreSQL and Redis from the dashboard
4. Set environment variables in the dashboard
5. Deploy

#### ✈️ Fly.io (Best for Multi-Region)

1. Install Fly CLI: `brew install flyctl`
2. Login: `fly auth login`
3. Launch: `fly launch`
4. Attach Postgres: `fly postgres create`
5. Deploy: `fly deploy`

---

## ⚙️ Production Configuration

### 1. Security Settings

**CRITICAL:** Never deploy with these settings wrong!

```bash
# .env file
DJANGO_DEBUG=false                    # ❌ NEVER true in production
DJANGO_SECRET_KEY=min-50-random-chars # ✅ Generate with: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
ALLOWED_HOSTS=yourdomain.com         # ✅ Your actual domain(s)

# HTTPS enforcement (enable ONLY after SSL is working)
SECURE_SSL_REDIRECT=true
SECURE_HSTS_SECONDS=31536000
SESSION_COOKIE_SECURE=true
CSRF_COOKIE_SECURE=true
```

### 2. Performance Settings

```bash
# FastEmbed cache warmup (run at deploy time)
python pocketai_django/manage.py warm_embeddings

# Gunicorn workers (2-4 × CPUs)
WEB_CONCURRENCY=4
GUNICORN_WORKERS=4

# Connection pooling
DB_CONN_MAX_AGE=600  # 10 minutes
REDIS_MAX_CONNECTIONS=50
```

### 3. Multi-Tenant Isolation

**Ensure your database isolates tenants properly:**

Your code already implements business-level isolation via `BusinessProfile` foreign keys. Double-check:

- `apps/knowledge/knowledge_access.py:apply_customer_visible_uploads` - Filters uploads by business
- All RAG queries filter by `business_profile_id`

**No additional configuration needed** - tenant isolation is built-in.

---

## 🔧 Background Workers

### Start Ingestion Worker (Required for File Ingestion)

**If using Docker Compose:**
Already running via `docker-compose.yml`.

**If running manually:**

```bash
cd pocketai_django
python manage.py knowledge_ingestion_worker --watch
```

**Optional (feature-based) workers:**
- Integrations sync: `python manage.py sync_knowledge_integrations --watch --sleep 300`
- Sub-agents: `python manage.py process_agent_runs --watch`
- Voice (dev-only): `python manage.py voice_call_worker --watch` + `python manage.py voice_ws_server --port 8081`

### Scheduled Tasks (Optional)

Run these periodically via cron or platform scheduler:

```bash
# Every night at 2 AM: Cleanup old knowledge
0 2 * * * cd /app/pocketai_django && python manage.py enforce_knowledge_retention

# Every 6 hours: Warm embeddings cache
0 */6 * * * cd /app/pocketai_django && python manage.py warm_embeddings
```

---

## 🔍 Health Check Endpoints

Your application includes health check endpoints for monitoring:

### Available Endpoints

```bash
# Comprehensive health check (database + cache)
curl https://yourdomain.com/api/health/
# Returns: {"status": "healthy", "checks": {"database": "ok", "cache": "ok"}}

# Readiness probe (for load balancers)
curl https://yourdomain.com/api/health/ready/
# Returns: {"ready": true}

# Liveness probe (for Kubernetes)
curl https://yourdomain.com/api/health/live/
# Returns: {"alive": true}
```

### Platform Integration

**Railway**: Auto-detects `/api/health/` ✅

**Render**: Add to `render.yaml`
```yaml
healthCheckPath: /api/health/
```

**Fly.io**: Add to `fly.toml`
```toml
[http_service.checks.health]
  path = "/api/health/"
  interval = "30s"
```

**Kubernetes**:
```yaml
livenessProbe:
  httpGet:
    path: /api/health/live/
    port: 8000
readinessProbe:
  httpGet:
    path: /api/health/ready/
    port: 8000
```

---

## 📊 Monitoring & Logging

### 1. Error Tracking with Sentry (Recommended)

**Setup Sentry** (takes 5 minutes):

1. **Sign up**: Go to [sentry.io](https://sentry.io) and create account
2. **Create project**: Click "Create Project" → Select "Django" → Name it "pocketai"
3. **Copy DSN**: You'll get a DSN like `https://abc123@sentry.io/456789`
4. **Set environment variable**:
   ```bash
   # Railway
   railway variables set SENTRY_DSN="your-dsn-here"
   railway variables set SENTRY_ENVIRONMENT="production"
   
   # Render (in dashboard environment variables)
   SENTRY_DSN=your-dsn-here
   SENTRY_ENVIRONMENT=production
   
   # Fly.io (in fly.toml secrets)
   fly secrets set SENTRY_DSN="your-dsn-here"
   ```

**What you get**:
- ✅ Automatic error capture with full stack traces
- ✅ Performance monitoring (slow queries, endpoints)
- ✅ Email/Slack alerts when errors occur
- ✅ Error grouping and trends
- ✅ FREE tier: 5,000 errors/month, 10K perf units

**View errors**: https://sentry.io → Your issues will appear automatically!

### 2. Application Logs

Logs are already configured and go to:
- `pocketai_django/var/logs/rag.log` - RAG queries, tool calls
- `pocketai_django/var/logs/deepseek_calls.log` - LLM requests
- stdout - Django application logs

**View logs in production:**

```bash
# Railway
railway logs --service web

# Render
# View in dashboard → Logs tab

# Docker
docker-compose logs -f web

# Fly.io
fly logs
```

**Pro tip**: With Sentry configured, you don't need to check logs manually - errors are automatically sent to your dashboard with full context!

### 2. Monitor RAG Quality

**Run evaluation harness regularly:**

```bash
cd pocketai_django
python manage.py run_rag_eval --set commerce
```

Exports metrics to `var/logs/rag_eval_latest.json`.

### 3. Load Testing

**Before going live, run load tests:**

```bash
python manage.py run_mcp_load_test \
  --business-id YOUR_BUSINESS_UUID \
  --mode search_read \
  --iterations 200 \
  --concurrency 8 \
  --queries "pricing" "refund policy"
```

**Success criteria:**
- p95 latency < 5s
- Error rate < 2%
- Throttled rate < 2%

### 4. Health Checks

**Endpoint:** `GET /health/`

Returns:
- `200 OK` if Django is healthy
- Database connectivity check
- Redis connectivity check

Configure your load balancer to use this endpoint.

---

## 🚨 Troubleshooting

### Issue: "pgvector extension not found"

**Symptom:** Migration fails with `extension "vector" does not exist`

**Fix:**
```sql
-- Connect to your database as superuser
\c pocketai
CREATE EXTENSION vector;
```

If you don't have superuser access, ask your hosting provider to enable it.

### Issue: "No module named 'fastembed'"

**Symptom:** Server crashes when generating embeddings

**Fix:**
```bash
cd pocketai_django
pip install -r requirements.txt
python manage.py warm_embeddings
```

### Issue: "413 Request Entity Too Large" when uploading files

**Symptom:** Large file uploads fail

**Fix - Nginx:**
```nginx
client_max_body_size 100M;
```

**Fix - Railway/Render:**
Set in app settings or use S3 for direct uploads.

### Issue: Slow searches (> 5s per query)

**Symptom:** RAG queries taking too long

**Diagnosis:**
```bash
# Check index health
psql $DATABASE_URL -c "SELECT schemaname, tablename, indexname FROM pg_indexes WHERE tablename = 'accounts_knowledgeuploadchunk';"

# Should see indexes on: embedding, business_profile_id, upload_id
```

**Fix: Rebuild vector index:**
```sql
-- As database owner
REINDEX INDEX accounts_knowledgeuploadchunk_embedding_idx;
```

### Issue: High memory usage

**Symptom:** Worker pods restarting (OOMKilled)

**Cause:** FastEmbed models load ~500MB per worker

**Fix:**
- Option 1: Use `EMBED_PROVIDER=openai` (offload to API)
- Option 2: Increase pod memory to 2GB+
- Option 3: Reduce `WEB_CONCURRENCY`

---

## 📈 Scaling Guide

### When to scale?

Monitor these metrics:

- **Database:** `SELECT count(*) FROM accounts_knowledgeuploadchunk;`
  - \> 1M chunks: Add read replicas
- **Redis:** Memory usage > 80%
  - Upgrade instance or add partitioning
- **Web workers:** CPU > 70% sustained
  - Add more pods/dynos

### Horizontal Scaling

**1. Add web workers:**

```bash
# Railway
railway scale --replicas 3

# Docker Swarm
docker service scale pocketai_web=3
```

**2. Add ingestion workers:**

Scale the ingestion worker separately:

```bash
# In docker-compose.yml
services:
  worker:
    replicas: 2
```

**3. Database read replicas:**

Use for:
- Dashboard analytics
- `run_rag_eval` harness
- Backfill operations

Don't use for:
- Live RAG queries (needs latest data)
- Ingestion writes

---

## 🎯 Production Checklist

Before accepting real traffic:

### Security
- [ ] `DEBUG=false` verified
- [ ] `ALLOWED_HOSTS` contains only your domains
- [ ] `SECRET_KEY` is random (50+ chars)
- [ ] `SECURE_SSL_REDIRECT=true` (after SSL works)
- [ ] Database password is strong (20+ chars)
- [ ] `.env` file is in `.gitignore`
- [ ] Admin panel secured (change `/admin/` URL)

### Database
- [ ] pgvector extension enabled
- [ ] All migrations applied
- [ ] Database backups configured (daily + PITR)
- [ ] Connection pooling configured
- [ ] Query performance tested (`EXPLAIN ANALYZE`)

### Caching
- [ ] Redis connected
- [ ] `REDIS_URL` environment variable set
- [ ] Cache hit rate > 50% (check after 1 day)

### LLM Provider
- [ ] API key configured
- [ ] Billing limits set
- [ ] Usage monitoring enabled
- [ ] Fallback provider configured (optional)

### Embeddings
- [ ] Provider chosen (`local` or `openai`)
- [ ] Warm cache run: `python manage.py warm_embeddings`
- [ ] `EMBED_DIM` matches database vector size (384 or 1536)

### Workers
- [ ] Ingestion worker running
- [ ] Worker health checks passing
- [ ] Job queue not backed up

### Monitoring
- [ ] **Sentry account created** (sentry.io)
- [ ] **Sentry DSN configured** (`SENTRY_DSN` environment variable)
- [ ] **Sentry alerts configured** (email/Slack for errors)
- [ ] **Health check endpoints tested** (`/api/health/`, `/api/health/ready/`, `/api/health/live/`)
- [ ] Logs aggregated (Logtail, Papertrail, etc.) - optional
- [ ] Uptime monitoring (UptimeRobot, Pingdom) - optional
- [ ] Database metrics dashboard - optional
- [ ] Cost alerts configured

### Testing
- [ ] RAG evaluation passing: `run_rag_eval`
- [ ] Load test passing: `run_mcp_load_test`
- [ ] Production gates passing: `run_production_gates`
- [ ] Manual smoke test completed

### Performance
- [ ] p95 search latency < 5s
- [ ] File ingestion < 60s for 10MB PDF
- [ ] Dashboard loads < 2s
- [ ] Concurrent users tested (10+ simultaneous chats)

---

## 🛠️ Platform-Specific Tips

### Railway
- ✅ PostgreSQL plugin includes pgvector
- ✅ Auto-SSL with custom domains
- ⚠️ Free tier: 500 hours/month (stops at night if unused)
- 💡 Enable "Auto Deploy" from GitHub

### Render
- ✅ Free PostgreSQL tier available
- ✅ Auto-deploy from GitHub
- ⚠️ pgvector requires manual extension install
- 💡 Use a background worker service for `knowledge_ingestion_worker`

### Fly.io
- ✅ Multi-region deployment
- ✅ Excellent PostgreSQL support
- ⚠️ Slightly more complex setup
- 💡 Use `fly regions add` for low latency

### AWS (Advanced)
- Use **ECS Fargate** for containers
- **RDS PostgreSQL** with pgvector
- **ElastiCache Redis** for caching
- **S3** for file uploads
- Estimated cost: $150-300/month for 100 businesses

---

## 📚 Additional Resources

### Official Documentation
- [Django Deployment Checklist](https://docs.djangoproject.com/en/5.1/howto/deployment/checklist/)
- [pgvector Extension](https://github.com/pgvector/pgvector)
- [OpenTelemetry Django](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/django/django.html)

### PocketAI Docs
- `pocketai_django/docs/rag/rag_rollout_ops.md` - RAG operations guide
- `pocketai_django/docs/ops/load_testing.md` - Load testing guide
- `pocketai_django/docs/ops/manual_qa_playbook.md` - QA procedures
- `pocketai_django/docs/product/saas_brief.md` - Product overview

### Support
- Create an issue on GitHub (if applicable)
- Check `var/logs/rag.log` for detailed RAG traces
- Run `python manage.py check --deploy` for security warnings

---

## 🎉 You're Ready for Initial Deployment!

**✅ Phases 0-2 Complete:** All critical blockers addressed  
**🚀 Production Readiness: ~85-90%**

Once you complete the above checklist, your multi-tenant AI assistant platform is ready for **staging deployment**. Each business can upload their knowledge, configure their agent, and start serving customers.

**What you've accomplished:**
- ✅ **Phase 0**: Security hardening (DEBUG, secrets, HTTPS)
- ✅ **Phase 1**: Docker deployment infrastructure
- ✅ **Phase 2**: Error monitoring (Sentry) + health checks

**Next immediate steps:**
1. Deploy to staging environment (Railway/Render recommended)
2. Test with real customer data
3. Complete remaining phases based on actual usage patterns

---

## 📅 Pending Phases Roadmap

The following phases can be completed **after** initial staging deployment based on your scaling needs:

### Phase 3: Database Safety & Migrations (Week 2-3)
**Priority: HIGH** - Before handling large datasets

- [ ] **Test migrations at scale**
  - Run all migrations against database with 1M+ chunks
  - Measure migration time and table locks
  - Document any performance issues
  
- [ ] **Rollback procedures**
  - Document rollback steps for each recent migration
  - Test rollback on staging environment
  - Create emergency rollback script
  
- [ ] **Query performance monitoring**
  - Enable `pg_stat_statements` extension
  - Monitor slow queries (>1s)
  - Create indexes for common query patterns
  
- [ ] **Read replicas** (optional for large deployments)
  - Setup PostgreSQL read replica
  - Route analytics queries to replica
  - Route `run_rag_eval` to replica

**Estimated time: 1-2 weeks**

---

### Phase 4: Scaling & Resource Limits (Week 3-4)
**Priority: MEDIUM** - Before 100+ businesses

- [ ] **Container resource limits**
  - Set memory limits: 4GB per worker, 2GB per web pod
  - Set CPU limits: 2 CPUs per worker, 1 CPU per web
  - Monitor OOMKilled restarts
  
- [ ] **Horizontal auto-scaling**
  - Configure HPA for Kubernetes (CPU >70% → scale up)
  - Or equivalent for Railway/Render
  - Test scaling from 1→10 pods
  
- [ ] **Disk usage alerts**
  - Setup alerts for disk >80% usage
  - Configure automatic cleanup jobs
  - Monitor embedding storage growth
  
- [ ] **Data retention policy**
  - Run `enforce_knowledge_retention.py` nightly
  - Delete uploads inactive for 90+ days
  - Archive old embeddings
  
- [ ] **Load testing**
  - Run load tests to 10x expected traffic
  - Test with `run_mcp_load_test --iterations 1000`
  - Measure p95 latency under load
  - Verify error rate <2% at peak

**Estimated time: 1-2 weeks**

---

### Phase 5: Async Processing Optimization (Week 4)
**Priority: MEDIUM** - For better UX with large files

- [ ] **Ingestion worker verification**
  - Confirm ingestion workers running in production
  - Monitor worker queue depth
  - Setup dead letter queue for failed jobs
  
- [ ] **Queue all heavy operations**
  - Queue file uploads >1MB
  - Queue embedding generation
  - Queue batch operations
  
- [ ] **Job status API**
  - Add endpoint: `GET /api/jobs/{id}/status/`
  - Return: `{"status": "pending|running|completed|failed", "progress": 75}`
  - Show progress in UI
  
- [ ] **Worker scaling**
  - Configure separate worker pool for heavy jobs
  - Auto-scale workers based on queue depth
  - Set max retries and timeout policies

**Estimated time: 1 week**

---

### Phase 6: Operational Excellence (Ongoing)
**Priority: LOW** - Nice to have, but not blocking

- [ ] **Monitoring dashboards**
  - Grafana dashboard for RAG metrics (latency, cache hit rate)
  - Grafana dashboard for business metrics (uploads, queries)
  - Cost tracking per tenant (LLM tokens, embeddings)
  
- [ ] **API versioning**
  - Implement `/api/v1/` versioning
  - Add deprecation warnings for old endpoints
  - Create migration guide for API changes
  
- [ ] **Incident response**
  - Write runbooks for common incidents:
    - High error rate
    - Latency spike
    - Disk full
    - Database connection exhausted
  - Setup on-call rotation
  - Schedule quarterly disaster recovery drills
  
- [ ] **Advanced optimizations**
  - Read-through cache for hot documents
  - Embedding precomputation for common queries
  - Multi-region deployment
  
- [ ] **Compliance & governance**
  - GDPR compliance audit
  - SOC 2 preparation (if needed)
  - Data retention policies
  - Privacy policy updates

**Estimated time: Ongoing**

---

## 🎯 Deployment Decision Tree

```
Are you ready to accept production traffic?
│
├─ YES, deploy immediately
│  └─ ✅ Phases 0-2 complete (security, Docker, Sentry)
│     └─ Do: Deploy to Railway/Render, monitor for 7 days, then Phases 3-4
│
├─ YES, but need to test first
│  └─ ✅ Phases 0-2 complete
│     └─ Do: Deploy to staging, test with beta customers, then Phases 3-4
│
└─ NO, need more preparation
   └─ ⚠️  Missing Phase 0-2?
      └─ Do: Complete security/Docker/Sentry first (CRITICAL)
```

---

## 🚀 Quick Start: Deploy to Staging NOW

You can deploy right now! Here's how:

### Option 1: Railway (5 minutes)
```bash
railway init
railway add --plugin postgresql
railway add --plugin redis
railway variables set SENTRY_DSN="your-dsn"
railway variables set DJANGO_DEBUG=false
railway up
```

### Option 2: Render (10 minutes)
1. Connect GitHub repository
2. Select "Docker" deployment
3. Add PostgreSQL + Redis from dashboard
4. Set environment variables
5. Click "Deploy"

### Option 3: Docker Compose Local Test
```bash
docker-compose up -d
docker-compose exec web python manage.py migrate
curl http://localhost:8000/api/health/
```

---

## 📊 Current Status Summary

- Platform is **beta**.
- MCP is the only active orchestrator; agentic read v2 is enabled.
- Voice stack is implemented but **dev-only**.
- Mobile app is paused.

Before production traffic, validate:
- Ingestion worker health + queue latency
- RAG latency and tool budgets under load
- Verified lookup policies for sensitive data

---

**Happy deploying! 🚀**
