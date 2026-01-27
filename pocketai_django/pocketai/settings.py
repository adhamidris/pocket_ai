"""Django settings for the server-rendered PocketAI project."""

from pathlib import Path
import base64
import binascii
import hashlib
import json
import os
import time

# Base directory of the Django project (the folder that contains manage.py)
BASE_DIR = Path(__file__).resolve().parent.parent

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


# ==============================================================================
# SECURITY SETTINGS (Environment-based)
# ==============================================================================

def _is_production() -> bool:
    """Detect if running in production based on environment signals."""
    return any([
        # DJANGO_ENV: Explicit environment marker ("production" in production).
        os.getenv("DJANGO_ENV") == "production",
        # RAILWAY_ENVIRONMENT: Railway-provided environment marker.
        os.getenv("RAILWAY_ENVIRONMENT") == "production",
        # RENDER: Render sets this; used as a production signal.
        os.getenv("RENDER") is not None,
        # FLY_APP_NAME: Fly.io app name; used as a production signal.
        os.getenv("FLY_APP_NAME") is not None,
        # DJANGO_DEBUG: Treat explicit "false" as a production signal.
        os.getenv("DJANGO_DEBUG", "").lower() == "false",
    ])


# DJANGO_DEBUG: Django DEBUG flag (default false; never enable in production).
DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() in {"1", "true", "yes"}

if DEBUG and _is_production():
    import warnings
    warnings.warn(
        "DEBUG=True detected in production environment! This is a CRITICAL security risk.",
        RuntimeWarning,
        stacklevel=2,
    )

_default_secret_key = "django-insecure-change-me"
# DJANGO_SECRET_KEY: Django secret key (signing; must be set in production).
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", _default_secret_key)

if SECRET_KEY == _default_secret_key:
    import sys
    warning_msg = (
        "\n"
        "🔒 WARNING: Using insecure default SECRET_KEY (dangerous in production)\n"
        "   Generate secure key: python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'\n"
        "   Then set DJANGO_SECRET_KEY environment variable.\n"
    )
    if _is_production():
        # In production, this is a critical error
        print(warning_msg, file=sys.stderr)
        raise RuntimeError("Cannot start in production with default SECRET_KEY")
    else:
        # In development, just warn
        print(warning_msg, file=sys.stderr)

# ALLOWED_HOSTS: Comma-separated list of allowed hostnames for Django.
_allowed_hosts_raw = os.getenv("ALLOWED_HOSTS", "").strip()
if _allowed_hosts_raw:
    ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts_raw.split(",") if h.strip()]
else:
    # Development defaults
    ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
    if _is_production():
        import warnings
        warnings.warn(
            "ALLOWED_HOSTS not configured in production! Set the ALLOWED_HOSTS environment variable.",
            RuntimeWarning,
            stacklevel=2,
        )


# ==============================================================================
# ERROR MONITORING (Sentry)
# ==============================================================================

if not DEBUG:
    # SENTRY_DSN: Sentry project DSN (enables Sentry when set).
    _sentry_dsn = os.getenv("SENTRY_DSN", "").strip()
    if _sentry_dsn:
        # SENTRY_ENVIRONMENT: Sentry environment tag (e.g., production/staging).
        _sentry_environment = os.getenv("SENTRY_ENVIRONMENT", "production")
        # SENTRY_RELEASE: Release/version tag (optional).
        _sentry_release = os.getenv("SENTRY_RELEASE", "unknown")
        # SENTRY_TRACES_SAMPLE_RATE: Fraction (0..1) of requests to trace.
        _sentry_traces_sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))
        # SENTRY_PROFILES_SAMPLE_RATE: Fraction (0..1) of traced requests to profile.
        _sentry_profiles_sample_rate = float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.1"))

        import logging
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.redis import RedisIntegration
        
        sentry_sdk.init(
            dsn=_sentry_dsn,
            integrations=[
                DjangoIntegration(
                    transaction_style='url',
                    middleware_spans=True,
                    signals_spans=True,
                ),
                LoggingIntegration(
                    level=logging.INFO,
                    event_level=logging.ERROR,
                ),
                RedisIntegration(),
            ],
            environment=_sentry_environment,
            release=_sentry_release,
            
            # Performance Monitoring
            traces_sample_rate=_sentry_traces_sample_rate,
            profiles_sample_rate=_sentry_profiles_sample_rate,
            
            # Privacy: Don't send PII (critical for multi-tenant SaaS)
            send_default_pii=False,
            
            # Ignore common noise
            ignore_errors=[
                'SuspiciousOperation',
                'PermissionDenied',
            ],
        )
        print(f"✅ Sentry initialized for environment: {_sentry_environment}")



def _split_scopes(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [scope.strip() for scope in raw.split() if scope.strip()]


def _integration_credentials_key() -> str:
    # INTEGRATION_CREDENTIALS_KEY: Optional base secret for encrypting integration credentials at rest.
    provided = os.getenv("INTEGRATION_CREDENTIALS_KEY", "").strip()
    if provided:
        candidate = provided.encode("utf-8")
        try:
            base64.urlsafe_b64decode(candidate)
            return provided
        except (binascii.Error, ValueError):
            digest = hashlib.sha256(candidate).digest()
            return base64.urlsafe_b64encode(digest).decode("utf-8")

    digest = hashlib.sha256(SECRET_KEY.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8")


# GOOGLE_OAUTH_CLIENT_ID: Google OAuth client ID (Drive/Sheets integration).
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
# GOOGLE_OAUTH_CLIENT_SECRET: Google OAuth client secret (Drive/Sheets integration).
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
# GOOGLE_OAUTH_REDIRECT_URI: OAuth callback URL (must match Google Console config).
GOOGLE_OAUTH_REDIRECT_URI = os.getenv(
    "GOOGLE_OAUTH_REDIRECT_URI",
    "http://localhost:8000/api/integrations/google/callback/",
)
_default_google_scopes = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
# GOOGLE_OAUTH_SCOPES: Space-separated OAuth scopes to request (defaults to Drive/Sheets read-only + OpenID).
GOOGLE_OAUTH_SCOPES = _split_scopes(os.getenv("GOOGLE_OAUTH_SCOPES")) or _default_google_scopes

# ------------------------------------------------------------------------------
# MCP Marketplace OAuth Providers (global)
# ------------------------------------------------------------------------------
# MCP OAuth providers are stored in the database (OAuthProvider model). These env vars allow
# bootstrapping common providers without manual admin setup.
#
# NOTE: Redirect URI for MCP marketplace OAuth is dynamic and uses:
#   /api/oauth/callback/<provider_key>/
# Ensure your OAuth app allows that URI for the deployment host.
MCP_OAUTH_GOOGLE_CLIENT_ID = os.getenv("MCP_OAUTH_GOOGLE_CLIENT_ID", GOOGLE_OAUTH_CLIENT_ID).strip()
MCP_OAUTH_GOOGLE_CLIENT_SECRET = os.getenv("MCP_OAUTH_GOOGLE_CLIENT_SECRET", GOOGLE_OAUTH_CLIENT_SECRET).strip()
_default_mcp_google_scopes = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    # Common marketplace connectors
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/adwords",
]
MCP_OAUTH_GOOGLE_SCOPES = _split_scopes(os.getenv("MCP_OAUTH_GOOGLE_SCOPES")) or _default_mcp_google_scopes

MCP_OAUTH_SLACK_CLIENT_ID = os.getenv("MCP_OAUTH_SLACK_CLIENT_ID", "").strip()
MCP_OAUTH_SLACK_CLIENT_SECRET = os.getenv("MCP_OAUTH_SLACK_CLIENT_SECRET", "").strip()
_default_mcp_slack_scopes = [
    "chat:write",
    "channels:read",
    "users:read",
]
MCP_OAUTH_SLACK_SCOPES = _split_scopes(os.getenv("MCP_OAUTH_SLACK_SCOPES")) or _default_mcp_slack_scopes

# ------------------------------------------------------------------------------
# Email Connectors (Google/Microsoft) — OAuth apps (platform-owned)
# ------------------------------------------------------------------------------
# NOTE: Redirect URI for email OAuth is dynamic and uses:
#   /api/email/oauth/callback/<provider_key>/
# Ensure your OAuth apps allow that URI for the deployment host(s).
EMAIL_OAUTH_GOOGLE_CLIENT_ID = os.getenv("EMAIL_OAUTH_GOOGLE_CLIENT_ID", GOOGLE_OAUTH_CLIENT_ID).strip()
EMAIL_OAUTH_GOOGLE_CLIENT_SECRET = os.getenv("EMAIL_OAUTH_GOOGLE_CLIENT_SECRET", GOOGLE_OAUTH_CLIENT_SECRET).strip()
_default_email_google_scopes = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    # Email capabilities (v1: text-only send + read/search)
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
]
EMAIL_OAUTH_GOOGLE_SCOPES = _split_scopes(os.getenv("EMAIL_OAUTH_GOOGLE_SCOPES")) or _default_email_google_scopes

EMAIL_OAUTH_MICROSOFT_CLIENT_ID = os.getenv("EMAIL_OAUTH_MICROSOFT_CLIENT_ID", "").strip()
EMAIL_OAUTH_MICROSOFT_CLIENT_SECRET = os.getenv("EMAIL_OAUTH_MICROSOFT_CLIENT_SECRET", "").strip()
_default_email_microsoft_scopes = [
    "openid",
    "profile",
    "email",
    "offline_access",
    "User.Read",
    "Mail.Read",
    "Mail.Send",
]
EMAIL_OAUTH_MICROSOFT_SCOPES = _split_scopes(os.getenv("EMAIL_OAUTH_MICROSOFT_SCOPES")) or _default_email_microsoft_scopes

# ------------------------------------------------------------------------------
# Email Connectors (Google/Microsoft) — policy defaults
# ------------------------------------------------------------------------------
# These settings define default-safe behavior for first-party email connectors.
# They do not grant access by themselves; access is governed by per-user OAuth.
#
# SEND DEFAULT:
# - "draft_approval": always require approval before sending (recommended default)
# - "auto_send": allow unattended send when per-connection/per-agent policy permits it
EMAIL_SEND_DEFAULT_MODE = os.getenv("EMAIL_SEND_DEFAULT_MODE", "draft_approval").strip() or "draft_approval"

# Auto-send safety: step-up to approval for risky recipients.
EMAIL_AUTOSEND_STEP_UP_EXTERNAL_DOMAIN = os.getenv("EMAIL_AUTOSEND_STEP_UP_EXTERNAL_DOMAIN", "true").lower() in {"1", "true", "yes"}

# Optional "save thread to knowledge" defaults (explicit user action).
EMAIL_SAVED_THREAD_DEFAULT_VISIBILITY = os.getenv("EMAIL_SAVED_THREAD_DEFAULT_VISIBILITY", "private").strip() or "private"
EMAIL_SAVED_THREAD_COLLECTION_SLUG = os.getenv("EMAIL_SAVED_THREAD_COLLECTION_SLUG", "saved-emails").strip() or "saved-emails"
# INTEGRATIONS_DASHBOARD_URL: Where the UI should send users after connecting integrations.
INTEGRATIONS_DASHBOARD_URL = os.getenv(
    "INTEGRATIONS_DASHBOARD_URL",
    "/dashboard/knowledge?panel=integrations",
)
INTEGRATION_CREDENTIALS_KEY = _integration_credentials_key()
# INTEGRATION_CREDENTIAL_ROTATION_DAYS: Rotate/stale-check integration credentials after N days.
INTEGRATION_CREDENTIAL_ROTATION_DAYS = int(os.getenv("INTEGRATION_CREDENTIAL_ROTATION_DAYS", "30"))
# INTEGRATION_CREDENTIAL_MAX_ERRORS: Disable/flag a credential after N consecutive errors.
INTEGRATION_CREDENTIAL_MAX_ERRORS = int(os.getenv("INTEGRATION_CREDENTIAL_MAX_ERRORS", "3"))

# API_PUBLIC_URL: Optional public URL base (e.g., "https://api.example.com" or "http://localhost:3000")
# Used to construct OAuth callback URLs when the request host (e.g., localhost:8000) differs from
# the registered OAuth redirect URI (e.g., localhost:3000).
API_PUBLIC_URL = os.getenv("API_PUBLIC_URL", "").strip()

# INGEST_NORMALIZE_TABLES: Enable ingestion-time table normalization/cleanup.
INGEST_NORMALIZE_TABLES = os.getenv("INGEST_NORMALIZE_TABLES", "true").lower() in {"1", "true", "yes"}
# INGEST_NORMALIZATION_POLICY_VERSION: Ingestion normalization policy version tag (for safe rollouts).
INGEST_NORMALIZATION_POLICY_VERSION = os.getenv("INGEST_NORMALIZATION_POLICY_VERSION", "v1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Profiling / request tracing (DEBUG-only URLs wired below)
    # Project apps
    "apps.accounts",
    "apps.cases",
    "apps.customers",
    "apps.conversations",
    "apps.integrations.apps.IntegrationsConfig",
    "apps.knowledge.apps.KnowledgeConfig",
    "apps.llm.apps.LlmConfig",
    "apps.mcp.apps.McpConfig",
    "apps.rag.apps.RagConfig",
    "frontend",
    #Vectorizing
    "pgvector.django",
    "django.contrib.postgres",
]

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "pocketai-cache",
        "TIMEOUT": 300,  # seconds
    }
}

# REDIS_URL: Redis connection string; when set, replaces LocMemCache with shared Redis cache.
redis_url = os.getenv("REDIS_URL")
if redis_url:
    CACHES["default"] = {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": redis_url,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "IGNORE_EXCEPTIONS": True,
        },
        "TIMEOUT": 300,
    }

# EMBED_PROVIDER: Embedding backend ("local" for FastEmbed, or "openai").
EMBED_PROVIDER = os.getenv("EMBED_PROVIDER", "local")
# Default to a multilingual FastEmbed model so Arabic/mixed-language tenants work out of the box.
# Keep `EMBED_DIM=384` unless you intentionally migrate the `VectorField` dimension in Postgres.
# EMBED_MODEL: Embedding model name (must match the embedding provider).
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
# EMBED_DIM: Embedding vector dimension stored in Postgres/pgvector.
EMBED_DIM = int(os.getenv("EMBED_DIM", "384"))
# EMBED_DISTANCE: Vector distance metric for pgvector ("cosine"|"l2"|"ip").
EMBED_DISTANCE = os.getenv("EMBED_DISTANCE", "cosine")  # 'cosine'|'l2'|'ip'
# RAG_SEARCH_BACKEND: Retrieval backend ("postgres" for local, "azure" for Azure AI Search).
RAG_SEARCH_BACKEND = os.getenv("RAG_SEARCH_BACKEND", "postgres").strip().lower()  # 'postgres'|'azure'
# AZURE_SEARCH_ENDPOINT: Azure AI Search service endpoint URL.
AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "").strip()
# AZURE_SEARCH_ADMIN_KEY: Azure AI Search admin key (index management + queries).
AZURE_SEARCH_ADMIN_KEY = os.getenv("AZURE_SEARCH_ADMIN_KEY", "").strip()
# AZURE_SEARCH_QUERY_KEY: Azure AI Search query key (queries only; optional if admin key is set).
AZURE_SEARCH_QUERY_KEY = os.getenv("AZURE_SEARCH_QUERY_KEY", "").strip()
# AZURE_SEARCH_INDEX_NAME: Azure AI Search index name for tenant knowledge.
AZURE_SEARCH_INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX_NAME", "pocketai-knowledge").strip()
# AZURE_SEARCH_SEMANTIC_ENABLED: Enable semantic ranking when configured in the Azure service.
AZURE_SEARCH_SEMANTIC_ENABLED = os.getenv("AZURE_SEARCH_SEMANTIC_ENABLED", "false").lower() in {"1", "true", "yes"}
# AZURE_SEARCH_SEMANTIC_CONFIG: Name of the semantic configuration to use (Azure-side).
AZURE_SEARCH_SEMANTIC_CONFIG = os.getenv("AZURE_SEARCH_SEMANTIC_CONFIG", "default").strip() or "default"
# AZURE_SEARCH_REQUEST_TIMEOUT_S: Network timeout (seconds) for Azure AI Search requests.
AZURE_SEARCH_REQUEST_TIMEOUT_S = float(os.getenv("AZURE_SEARCH_REQUEST_TIMEOUT_S", "6.0") or 6.0)
# AZURE_SEARCH_INDEX_BATCH_SIZE: Batch size for index backfills/updates.
AZURE_SEARCH_INDEX_BATCH_SIZE = int(os.getenv("AZURE_SEARCH_INDEX_BATCH_SIZE", "500") or 500)
# AZURE_SEARCH_UPLOAD_FILTER_THRESHOLD: Threshold after which upload filters are enforced to keep queries fast.
AZURE_SEARCH_UPLOAD_FILTER_THRESHOLD = int(os.getenv("AZURE_SEARCH_UPLOAD_FILTER_THRESHOLD", "150") or 150)
# INGEST_MAX_JSON_ENTITIES_DEFAULT: Default cap for JSON entity extraction during ingestion.
INGEST_MAX_JSON_ENTITIES_DEFAULT = int(os.getenv("INGEST_MAX_JSON_ENTITIES_DEFAULT", "1000"))
# INGEST_MAX_JSON_ENTITY_CANDIDATES: Upper bound on JSON entity candidates considered before pruning.
INGEST_MAX_JSON_ENTITY_CANDIDATES = int(os.getenv("INGEST_MAX_JSON_ENTITY_CANDIDATES", "4000"))
# INGEST_ALIAS_WARNING_THRESHOLD: Warn when alias extraction exceeds this count (signals noisy ingestion).
INGEST_ALIAS_WARNING_THRESHOLD = int(os.getenv("INGEST_ALIAS_WARNING_THRESHOLD", "2000"))
# RAG_MAX_SNIPPETS_PER_SEARCH: Max retrieval candidates returned by the backend per search.
# Note: LLM-visible snippet evidence is capped separately via MCP_PROMPT_MAX_SNIPPETS.
# Increased from 3 to 8 to support comprehensive queries on table-heavy documents.
RAG_MAX_SNIPPETS_PER_SEARCH = int(os.getenv("RAG_MAX_SNIPPETS_PER_SEARCH", "8"))
# RAG_ALIAS_MAX_CHUNKS_PER_UPLOAD: Per-upload cap for alias/identifier chunks in retrieval.
RAG_ALIAS_MAX_CHUNKS_PER_UPLOAD = int(os.getenv("RAG_ALIAS_MAX_CHUNKS_PER_UPLOAD", "3"))
# RAG_ANN_MAX_CHUNKS_PER_UPLOAD: Per-upload cap for ANN/vector chunks in retrieval.
# Increased from 3 to 6 to allow more table rows per document in results.
RAG_ANN_MAX_CHUNKS_PER_UPLOAD = int(os.getenv("RAG_ANN_MAX_CHUNKS_PER_UPLOAD", "6"))
# RAG_SEARCH_PREVIEW_CHAR_LIMIT: Max characters of preview text included in snippet evidence.
RAG_SEARCH_PREVIEW_CHAR_LIMIT = int(os.getenv("RAG_SEARCH_PREVIEW_CHAR_LIMIT", "800"))
# RAG_FTS_ENABLED: Enable lexical (Postgres FTS) retrieval stage.
RAG_FTS_ENABLED = os.getenv("RAG_FTS_ENABLED", "true").lower() in {"1", "true", "yes"}
try:
    # RAG_DB_STATEMENT_TIMEOUT_MS: Statement timeout (ms) for retrieval DB work.
    # P0 #4: Reduced from 15s to 5s to prevent long hangs
    RAG_DB_STATEMENT_TIMEOUT_MS = int(os.getenv("RAG_DB_STATEMENT_TIMEOUT_MS", "5000"))
except (TypeError, ValueError):
    RAG_DB_STATEMENT_TIMEOUT_MS = 5000
if RAG_DB_STATEMENT_TIMEOUT_MS < 0:
    RAG_DB_STATEMENT_TIMEOUT_MS = 0
try:
    # RAG_DB_LOCK_TIMEOUT_MS: Lock wait timeout (ms) for retrieval DB work.
    # P0 #4: Reduced from 2s to 1s for faster lock contention detection
    RAG_DB_LOCK_TIMEOUT_MS = int(os.getenv("RAG_DB_LOCK_TIMEOUT_MS", "1000"))
except (TypeError, ValueError):
    RAG_DB_LOCK_TIMEOUT_MS = 1000
if RAG_DB_LOCK_TIMEOUT_MS < 0:
    RAG_DB_LOCK_TIMEOUT_MS = 0
# RAG_RERANK_POOL: Candidate pool size considered for reranking.
# Reduced from 60 to 30 to cut rerank latency (was taking 4+ seconds).
RAG_RERANK_POOL = int(os.getenv("RAG_RERANK_POOL", "30"))
# RAG_RERANK_BUDGET_MS: Max time budget (ms) for reranking stage (0 disables budget enforcement).
# Default 2000ms prevents reranking from dominating search latency.
RAG_RERANK_BUDGET_MS = int(os.getenv("RAG_RERANK_BUDGET_MS", "2000"))
# RAG_SNIPPET_RERANK_BUDGET_MS: Max time budget (ms) for snippet-level reranking (0 disables).
RAG_SNIPPET_RERANK_BUDGET_MS = int(os.getenv("RAG_SNIPPET_RERANK_BUDGET_MS", "1000"))
# RAG_MMR_LAMBDA: MMR diversity/quality tradeoff (0..1; higher = less diversity).
RAG_MMR_LAMBDA = float(os.getenv("RAG_MMR_LAMBDA", "0.7"))
# RAG_VECTOR_DISTANCE_CEILING: Maximum allowed vector distance for accepting candidates (lower = stricter).
RAG_VECTOR_DISTANCE_CEILING = float(os.getenv("RAG_VECTOR_DISTANCE_CEILING", "0.5"))
# RAG_WEIGHT_VECTOR: Fusion weight for vector similarity stage.
RAG_WEIGHT_VECTOR = float(os.getenv("RAG_WEIGHT_VECTOR", "1.0"))
# RAG_WEIGHT_LEXICAL: Fusion weight for lexical/FTS stage.
RAG_WEIGHT_LEXICAL = float(os.getenv("RAG_WEIGHT_LEXICAL", "0.8"))
# RAG_WEIGHT_ALIAS: Fusion weight for alias/identifier stage.
RAG_WEIGHT_ALIAS = float(os.getenv("RAG_WEIGHT_ALIAS", "1.2"))
# RAG_WEIGHT_ENTITY: Fusion weight for entity-aware stage.
RAG_WEIGHT_ENTITY = float(os.getenv("RAG_WEIGHT_ENTITY", "0.4"))
# RAG_WEIGHT_RECENCY: Fusion weight for recency boosts.
RAG_WEIGHT_RECENCY = float(os.getenv("RAG_WEIGHT_RECENCY", "0.25"))
# RAG_LEXICAL_THRESHOLD_SHORT: Minimum lexical similarity threshold for short queries.
RAG_LEXICAL_THRESHOLD_SHORT = float(os.getenv("RAG_LEXICAL_THRESHOLD_SHORT", "0.25"))
# RAG_LEXICAL_THRESHOLD_MEDIUM: Minimum lexical similarity threshold for medium queries.
RAG_LEXICAL_THRESHOLD_MEDIUM = float(os.getenv("RAG_LEXICAL_THRESHOLD_MEDIUM", "0.2"))
# RAG_LEXICAL_THRESHOLD_LONG: Minimum lexical similarity threshold for long queries.
RAG_LEXICAL_THRESHOLD_LONG = float(os.getenv("RAG_LEXICAL_THRESHOLD_LONG", "0.15"))
# RAG_IVFFLAT_PROBES: pgvector IVFFLAT probes (higher = better recall, slower).
RAG_IVFFLAT_PROBES = int(os.getenv("RAG_IVFFLAT_PROBES", "8"))
# RAG_QUERY_VECTOR_CACHE_MAX_BYTES: Max bytes for per-turn vector query cache.
RAG_QUERY_VECTOR_CACHE_MAX_BYTES = int(os.getenv("RAG_QUERY_VECTOR_CACHE_MAX_BYTES", "16384"))
# RAG_NEIGHBOR_WINDOW_CACHE_SIZE: Cache size for page/chunk neighbor windows.
RAG_NEIGHBOR_WINDOW_CACHE_SIZE = int(os.getenv("RAG_NEIGHBOR_WINDOW_CACHE_SIZE", "128"))
# RAG_BUSINESS_OVERRIDE_KEY: BusinessProfile.metadata key for per-tenant RAG overrides.
RAG_BUSINESS_OVERRIDE_KEY = os.getenv("RAG_BUSINESS_OVERRIDE_KEY", "rag_overrides")
# RAG_TABLE_RESULT_LIMIT: Number of table candidates to prioritize/return when table routing runs.
RAG_TABLE_RESULT_LIMIT = int(os.getenv("RAG_TABLE_RESULT_LIMIT", "3"))
# RAG_TABLE_HEADER_MATCH_BONUS: Bonus added when query tokens match table headers.
RAG_TABLE_HEADER_MATCH_BONUS = float(os.getenv("RAG_TABLE_HEADER_MATCH_BONUS", "0.12"))
# RAG_TABLE_SPECIFIC_MISS_PENALTY: Penalty applied when a query looks specific but table match is weak.
RAG_TABLE_SPECIFIC_MISS_PENALTY = float(os.getenv("RAG_TABLE_SPECIFIC_MISS_PENALTY", "0.25"))
# RAG_TABLE_SPECIFIC_MIN_LENGTH: Minimum token length to treat a query as "specific".
RAG_TABLE_SPECIFIC_MIN_LENGTH = int(os.getenv("RAG_TABLE_SPECIFIC_MIN_LENGTH", "4"))
# RAG_TABLE_HEADER_TOKEN_CACHE: Cache size for header-token fingerprints.
RAG_TABLE_HEADER_TOKEN_CACHE = int(os.getenv("RAG_TABLE_HEADER_TOKEN_CACHE", "256"))
# RAG_TABLE_GENERIC_TOKEN_DF: Document-frequency threshold for considering tokens "generic".
RAG_TABLE_GENERIC_TOKEN_DF = float(os.getenv("RAG_TABLE_GENERIC_TOKEN_DF", "0.35"))
# RAG_TABLE_GENERIC_TOKEN_TOPK: Top-K tokens considered when extracting generic table hints.
RAG_TABLE_GENERIC_TOKEN_TOPK = int(os.getenv("RAG_TABLE_GENERIC_TOKEN_TOPK", "40"))
# RAG_TABLE_GENERIC_MIN_TABLES: Minimum tables required to run generic table routing.
RAG_TABLE_GENERIC_MIN_TABLES = int(os.getenv("RAG_TABLE_GENERIC_MIN_TABLES", "2"))
# RAG_TABLE_DOMINANT_MIN_TABLES: Minimum tables required to consider a single upload dominant.
RAG_TABLE_DOMINANT_MIN_TABLES = int(os.getenv("RAG_TABLE_DOMINANT_MIN_TABLES", "2"))
# RAG_TABLE_DOMINANT_UPLOAD_RATIO: Share of table hits required to treat one upload as dominant.
RAG_TABLE_DOMINANT_UPLOAD_RATIO = float(os.getenv("RAG_TABLE_DOMINANT_UPLOAD_RATIO", "0.35"))
# RAG_TABLE_ROW_LABEL_SAMPLE_LIMIT: Rows sampled to infer row labels/headings for table routing.
RAG_TABLE_ROW_LABEL_SAMPLE_LIMIT = int(os.getenv("RAG_TABLE_ROW_LABEL_SAMPLE_LIMIT", "200"))
# RAG_TABLE_CONTEXT_CACHE_SIZE: Cache size for table context/profiles.
RAG_TABLE_CONTEXT_CACHE_SIZE = int(os.getenv("RAG_TABLE_CONTEXT_CACHE_SIZE", "128"))
# RAG_NON_QUERYABLE_TABLE_FORMATS: Formats excluded from table-aware retrieval (e.g., ["docx"]).
# Default is empty to allow PDF/DOCX tables to be queryable. Set to ["pdf", "docx"] to disable.
_raw_non_queryable_formats = os.getenv("RAG_NON_QUERYABLE_TABLE_FORMATS", "").strip()
if _raw_non_queryable_formats:
    RAG_NON_QUERYABLE_TABLE_FORMATS = [f.strip().lower() for f in _raw_non_queryable_formats.split(",") if f.strip()]
else:
    RAG_NON_QUERYABLE_TABLE_FORMATS = []  # Empty = all formats queryable (PDF tables enabled)
# RAG_PDFPLUMBER_ENABLED: Enable pdfplumber extraction for PDFs (local ingest path).
RAG_PDFPLUMBER_ENABLED = os.getenv("RAG_PDFPLUMBER_ENABLED", "true").lower() in {"1", "true", "yes"}
# RAG_PDF_TABLE_EXTRACTOR: Which PDF table extractor to use ("auto"|"pdfplumber"|...).
RAG_PDF_TABLE_EXTRACTOR = os.getenv("RAG_PDF_TABLE_EXTRACTOR", "auto").strip().lower() or "auto"
# RAG_PDFPLUMBER_TABLE_SETTINGS: Optional JSON dict of pdfplumber table settings.
_raw_pdfplumber_settings = os.getenv("RAG_PDFPLUMBER_TABLE_SETTINGS", "").strip()
if _raw_pdfplumber_settings:
    try:
        RAG_PDFPLUMBER_TABLE_SETTINGS = json.loads(_raw_pdfplumber_settings)
    except json.JSONDecodeError:
        RAG_PDFPLUMBER_TABLE_SETTINGS = None
else:
    RAG_PDFPLUMBER_TABLE_SETTINGS = None

# RAG_AZURE_DI_ENABLED: Enable Azure Document Intelligence for OCR/layout/table extraction.
RAG_AZURE_DI_ENABLED = os.getenv("RAG_AZURE_DI_ENABLED", "true").lower() in {"1", "true", "yes"}
# AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT: Azure Document Intelligence endpoint URL.
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
# AZURE_DOCUMENT_INTELLIGENCE_KEY: Azure Document Intelligence API key.
AZURE_DOCUMENT_INTELLIGENCE_KEY = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")
# AZURE_DOCUMENT_INTELLIGENCE_MODEL: Azure Document Intelligence model id (e.g., prebuilt-layout).
AZURE_DOCUMENT_INTELLIGENCE_MODEL = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_MODEL", "prebuilt-layout")
# AZURE_DOCUMENT_INTELLIGENCE_API_VERSION: Azure Document Intelligence API version string.
AZURE_DOCUMENT_INTELLIGENCE_API_VERSION = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_API_VERSION", "2023-07-31")
# AZURE_DOCUMENT_INTELLIGENCE_BASE_PATH: Base path segment for the DI endpoint (varies by service).
AZURE_DOCUMENT_INTELLIGENCE_BASE_PATH = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_BASE_PATH", "formrecognizer")
# AZURE_DOCUMENT_INTELLIGENCE_LOCALE: Optional locale hint for OCR (e.g., "ar", "en").
AZURE_DOCUMENT_INTELLIGENCE_LOCALE = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_LOCALE", "")
# RAG_AZURE_DI_TIMEOUT_SECONDS: Timeout (seconds) for DI analyze requests.
RAG_AZURE_DI_TIMEOUT_SECONDS = float(os.getenv("RAG_AZURE_DI_TIMEOUT_SECONDS", "60"))
# RAG_AZURE_DI_POLL_INTERVAL_SECONDS: Poll interval (seconds) while waiting on DI async jobs.
RAG_AZURE_DI_POLL_INTERVAL_SECONDS = float(os.getenv("RAG_AZURE_DI_POLL_INTERVAL_SECONDS", "1.5"))
# RAG_AZURE_DI_MAX_POLLS: Max poll attempts before giving up on DI.
RAG_AZURE_DI_MAX_POLLS = int(os.getenv("RAG_AZURE_DI_MAX_POLLS", "40"))

# RAG_TABLE_VLM_ENABLED: Enable LLM/VLM-assisted table repair for tricky documents.
RAG_TABLE_VLM_ENABLED = os.getenv("RAG_TABLE_VLM_ENABLED", "true").lower() in {"1", "true", "yes"}
# RAG_TABLE_VLM_MODEL: Model used for table repairs (provider-specific).
RAG_TABLE_VLM_MODEL = os.getenv("RAG_TABLE_VLM_MODEL", "gpt-4o")
# RAG_TABLE_VLM_CONFIDENCE_THRESHOLD: Minimum confidence to accept a repaired table extraction.
RAG_TABLE_VLM_CONFIDENCE_THRESHOLD = float(os.getenv("RAG_TABLE_VLM_CONFIDENCE_THRESHOLD", "0.6"))
# RAG_TABLE_VLM_MAX_REPAIRS_PER_UPLOAD: Max repair attempts per upload during ingestion.
RAG_TABLE_VLM_MAX_REPAIRS_PER_UPLOAD = int(os.getenv("RAG_TABLE_VLM_MAX_REPAIRS_PER_UPLOAD", "3"))

# RAG_TABLE_SCHEMA_CHUNKING: Enable schema-aware chunking for table artifacts.
RAG_TABLE_SCHEMA_CHUNKING = os.getenv("RAG_TABLE_SCHEMA_CHUNKING", "true").lower() in {"1", "true", "yes"}
# RAG_TABLE_PARENT_MAX_ROWS: Max rows in a "parent" table chunk (schema chunking).
RAG_TABLE_PARENT_MAX_ROWS = int(os.getenv("RAG_TABLE_PARENT_MAX_ROWS", "200"))
# RAG_TABLE_PARENT_MAX_CHARS: Max characters in a "parent" table chunk (schema chunking).
RAG_TABLE_PARENT_MAX_CHARS = int(os.getenv("RAG_TABLE_PARENT_MAX_CHARS", "16000"))
# RAG_TABLE_CHILD_MAX_ROWS: Max rows in a "child" table chunk (schema chunking).
RAG_TABLE_CHILD_MAX_ROWS = int(os.getenv("RAG_TABLE_CHILD_MAX_ROWS", "500"))
# RAG_TABLE_HEADER_PROPAGATION_ENABLED: Propagate detected table headers across pages/sections.
RAG_TABLE_HEADER_PROPAGATION_ENABLED = os.getenv("RAG_TABLE_HEADER_PROPAGATION_ENABLED", "true").lower() in {"1", "true", "yes"}
# RAG_TABLE_HEADER_PROPAGATION_MIN_OVERLAP: Minimum overlap score to propagate headers.
RAG_TABLE_HEADER_PROPAGATION_MIN_OVERLAP = float(os.getenv("RAG_TABLE_HEADER_PROPAGATION_MIN_OVERLAP", "0.45"))
# RAG_TABLE_DEDUPE_ENABLED: Enable deduplication of similar tables/chunks.
RAG_TABLE_DEDUPE_ENABLED = os.getenv("RAG_TABLE_DEDUPE_ENABLED", "true").lower() in {"1", "true", "yes"}
# RAG_TABLE_DEDUPE_MIN_OVERLAP: Minimum overlap score to treat tables as duplicates.
RAG_TABLE_DEDUPE_MIN_OVERLAP = float(os.getenv("RAG_TABLE_DEDUPE_MIN_OVERLAP", "0.6"))
# RAG_TABLE_POSTPROCESS_ROW_LIMIT: Max rows kept after table postprocessing/cleanup.
RAG_TABLE_POSTPROCESS_ROW_LIMIT = int(os.getenv("RAG_TABLE_POSTPROCESS_ROW_LIMIT", "40"))

# RAG_OCR_NORMALIZATION_ENABLED: Normalize common OCR artefacts during ingestion.
RAG_OCR_NORMALIZATION_ENABLED = os.getenv("RAG_OCR_NORMALIZATION_ENABLED", "true").lower() in {"1", "true", "yes"}
# RAG_OCR_NORMALIZATION_REPLACEMENTS: Optional custom replacements (string/config) for OCR normalization.
RAG_OCR_NORMALIZATION_REPLACEMENTS = os.getenv("RAG_OCR_NORMALIZATION_REPLACEMENTS")
# RAG_OCR_PERCENT_FIX_ENABLED: Enable fixes for common percent OCR errors.
RAG_OCR_PERCENT_FIX_ENABLED = os.getenv("RAG_OCR_PERCENT_FIX_ENABLED", "true").lower() in {"1", "true", "yes"}
# RAG_OCR_PERCENT_SPACE_FIX_ENABLED: Enable fixes for percent spacing issues (e.g., "5 %" vs "5%").
RAG_OCR_PERCENT_SPACE_FIX_ENABLED = os.getenv("RAG_OCR_PERCENT_SPACE_FIX_ENABLED", "true").lower() in {"1", "true", "yes"}
# RAG_OCR_PERCENT_SANITY_MAX: Clamp percent values above this threshold as likely OCR noise.
RAG_OCR_PERCENT_SANITY_MAX = float(os.getenv("RAG_OCR_PERCENT_SANITY_MAX", "100"))
# RAG_OCR_CURRENCY_SPACING_ENABLED: Normalize currency spacing artefacts (e.g., "100 EGP" formatting).
RAG_OCR_CURRENCY_SPACING_ENABLED = os.getenv("RAG_OCR_CURRENCY_SPACING_ENABLED", "true").lower() in {"1", "true", "yes"}
# RAG_OCR_RENDER_DPI: Rasterization DPI for PDF OCR (higher improves accuracy but costs CPU).
try:
    RAG_OCR_RENDER_DPI = int(os.getenv("RAG_OCR_RENDER_DPI", "200"))
except (TypeError, ValueError):
    RAG_OCR_RENDER_DPI = 200
RAG_OCR_RENDER_DPI = max(72, min(600, RAG_OCR_RENDER_DPI))
# RAG_USE_MCP_ORCHESTRATOR: Use the MCP orchestrator for RAG turns (default true).
RAG_USE_MCP_ORCHESTRATOR = os.getenv("RAG_USE_MCP_ORCHESTRATOR", "true").lower() in {"1", "true", "yes"}
# Retrieval is predictable by default: one strong query per user turn.
# When the caller provides explicit batched queries (tool arg: `queries=[...]`),
# we can safely fan out a few variants to improve recall without increasing prompt tokens.
# MCP_SEARCH_MAX_QUERY_VARIANTS: Max query rewrites/fanout variants per user message.
MCP_SEARCH_MAX_QUERY_VARIANTS = int(os.getenv("MCP_SEARCH_MAX_QUERY_VARIANTS", "3"))
# MCP_SEARCH_FANOUT_BUDGET_MS: Fanout time budget (ms); 0 disables fanout.
MCP_SEARCH_FANOUT_BUDGET_MS = int(os.getenv("MCP_SEARCH_FANOUT_BUDGET_MS", "0"))
# MCP_SEARCH_FANOUT_RRF_K: RRF k parameter for combining fanout results.
MCP_SEARCH_FANOUT_RRF_K = int(os.getenv("MCP_SEARCH_FANOUT_RRF_K", "60"))
# MCP_SEARCH_FANOUT_PARALLEL: Run fanout queries in parallel (can spike DB/CPU).
MCP_SEARCH_FANOUT_PARALLEL = os.getenv("MCP_SEARCH_FANOUT_PARALLEL", "false").lower() in {"1", "true", "yes"}
try:
    # MCP_SEARCH_FANOUT_PARALLEL_MAX_WORKERS: Threadpool workers for parallel fanout.
    MCP_SEARCH_FANOUT_PARALLEL_MAX_WORKERS = int(os.getenv("MCP_SEARCH_FANOUT_PARALLEL_MAX_WORKERS", "4"))
except (TypeError, ValueError):
    MCP_SEARCH_FANOUT_PARALLEL_MAX_WORKERS = 4
MCP_SEARCH_FANOUT_PARALLEL_MAX_WORKERS = max(1, min(8, MCP_SEARCH_FANOUT_PARALLEL_MAX_WORKERS))
# MCP prompt-safe tool output limits (evidence packets sent back to the LLM).
# MCP_PROMPT_MAX_SNIPPETS: Max snippet evidence items returned to the model per tool call.
MCP_PROMPT_MAX_SNIPPETS = int(os.getenv("MCP_PROMPT_MAX_SNIPPETS", "4"))
# MCP_SEARCH_KNOWLEDGE_DEFAULT_LIMIT: Server-side fallback when `search_knowledge.limit`
# is omitted by the caller (LLMs sometimes assume schema defaults are enforced).
# This is clamped to MCP_PROMPT_MAX_SNIPPETS since the model will never see more than that.
MCP_SEARCH_KNOWLEDGE_DEFAULT_LIMIT = int(os.getenv("MCP_SEARCH_KNOWLEDGE_DEFAULT_LIMIT", "10"))
MCP_SEARCH_KNOWLEDGE_DEFAULT_LIMIT = max(1, min(MCP_SEARCH_KNOWLEDGE_DEFAULT_LIMIT, MCP_PROMPT_MAX_SNIPPETS))
# MCP_PROMPT_SNIPPET_CONTENT_CHARS: Max characters of snippet content included in tool output.
MCP_PROMPT_SNIPPET_CONTENT_CHARS = int(os.getenv("MCP_PROMPT_SNIPPET_CONTENT_CHARS", "1200"))
# MCP_PROMPT_TABLE_MAX_ROWS: Max rows included in tabular tool evidence.
MCP_PROMPT_TABLE_MAX_ROWS = int(os.getenv("MCP_PROMPT_TABLE_MAX_ROWS", "12"))
# MCP_PROMPT_TABLE_MAX_CONTRIBUTIONS: Max contribution entries included in table evidence.
MCP_PROMPT_TABLE_MAX_CONTRIBUTIONS = int(os.getenv("MCP_PROMPT_TABLE_MAX_CONTRIBUTIONS", "25"))
# MCP_PROMPT_TABLE_MAX_CELLS: Max cells per row included in table evidence.
MCP_PROMPT_TABLE_MAX_CELLS = int(os.getenv("MCP_PROMPT_TABLE_MAX_CELLS", "12"))
# MCP_PROMPT_TABLE_MAX_CELLS_EXACT: Higher cap for exact-match table responses (still bounded).
MCP_PROMPT_TABLE_MAX_CELLS_EXACT = int(os.getenv("MCP_PROMPT_TABLE_MAX_CELLS_EXACT", "60"))
# MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS: Hard cap on any single tool output message injected into the LLM prompt.
# This is a safety backstop; Phase 1 will replace this with out-of-band tool artifacts + prompt_view.
MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS = int(os.getenv("MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS", "25000"))
# MCP_READ_DOCUMENT_MAX_CHARS_MARGIN: Safety margin to keep read_document JSON outputs under MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS.
MCP_READ_DOCUMENT_MAX_CHARS_MARGIN = int(os.getenv("MCP_READ_DOCUMENT_MAX_CHARS_MARGIN", "1500"))
# MCP_READ_DOCUMENT_ARTIFACT_RETENTION_DAYS: Retain local read_document tool-output artifacts for this many days.
try:
    MCP_READ_DOCUMENT_ARTIFACT_RETENTION_DAYS = int(os.getenv("MCP_READ_DOCUMENT_ARTIFACT_RETENTION_DAYS", "30"))
except (TypeError, ValueError):
    MCP_READ_DOCUMENT_ARTIFACT_RETENTION_DAYS = 30
MCP_READ_DOCUMENT_ARTIFACT_RETENTION_DAYS = max(1, min(365, MCP_READ_DOCUMENT_ARTIFACT_RETENTION_DAYS))
# MCP_READ_DOCUMENT_ARTIFACT_MAX_PER_CONVERSATION: Keep at most N local read_document artifacts per conversation.
try:
    MCP_READ_DOCUMENT_ARTIFACT_MAX_PER_CONVERSATION = int(os.getenv("MCP_READ_DOCUMENT_ARTIFACT_MAX_PER_CONVERSATION", "200"))
except (TypeError, ValueError):
    MCP_READ_DOCUMENT_ARTIFACT_MAX_PER_CONVERSATION = 200
MCP_READ_DOCUMENT_ARTIFACT_MAX_PER_CONVERSATION = max(0, min(5000, MCP_READ_DOCUMENT_ARTIFACT_MAX_PER_CONVERSATION))

# Stage transcript windowing (raw messages kept verbatim in each provider call).
# These are *message* limits (not tokens) and apply after tool-call anchoring.
try:
    MCP_STAGE_HISTORY_INITIAL_PASS = int(os.getenv("MCP_STAGE_HISTORY_INITIAL_PASS", "6"))
except (TypeError, ValueError):
    MCP_STAGE_HISTORY_INITIAL_PASS = 6
try:
    MCP_STAGE_HISTORY_TOOL_ITERATION = int(os.getenv("MCP_STAGE_HISTORY_TOOL_ITERATION", "6"))
except (TypeError, ValueError):
    MCP_STAGE_HISTORY_TOOL_ITERATION = 6
try:
    MCP_STAGE_HISTORY_PLANNER = int(os.getenv("MCP_STAGE_HISTORY_PLANNER", "6"))
except (TypeError, ValueError):
    MCP_STAGE_HISTORY_PLANNER = 6
try:
    MCP_STAGE_HISTORY_POSTFLIGHT = int(os.getenv("MCP_STAGE_HISTORY_POSTFLIGHT", "6"))
except (TypeError, ValueError):
    MCP_STAGE_HISTORY_POSTFLIGHT = 6
# Logging privacy toggles (default: safe/no PII in logs).
# MCP_LOG_PII: If true, logs may include raw text/PII (dangerous; keep false in prod).
MCP_LOG_PII = os.getenv("MCP_LOG_PII", "false").lower() in {"1", "true", "yes"}
# MCP_LOG_SNIPPET_PREVIEWS: If true, log short snippet previews (still avoid PII when MCP_LOG_PII=false).
MCP_LOG_SNIPPET_PREVIEWS = os.getenv("MCP_LOG_SNIPPET_PREVIEWS", "false").lower() in {"1", "true", "yes"}
# MCP_LOG_FULL_SNIPPET_CONTENT: If true, include full snippet content/hashes in logs (only raw when MCP_LOG_PII=true).
MCP_LOG_FULL_SNIPPET_CONTENT = os.getenv("MCP_LOG_FULL_SNIPPET_CONTENT", "false").lower() in {"1", "true", "yes"}

# Logging verbosity control: "minimal", "standard", "verbose"
# LOG_VERBOSITY: Global verbosity level (applies to both console and file if not overridden).
LOG_VERBOSITY = os.getenv("LOG_VERBOSITY", "standard").strip().lower()
if LOG_VERBOSITY not in {"minimal", "standard", "verbose"}:
    LOG_VERBOSITY = "standard"
# LOG_VERBOSITY_CONSOLE: Console-specific verbosity override.
LOG_VERBOSITY_CONSOLE = os.getenv("LOG_VERBOSITY_CONSOLE", LOG_VERBOSITY).strip().lower()
if LOG_VERBOSITY_CONSOLE not in {"minimal", "standard", "verbose"}:
    LOG_VERBOSITY_CONSOLE = LOG_VERBOSITY
# LOG_VERBOSITY_FILE: File-specific verbosity override (default: verbose for comprehensive file logs).
LOG_VERBOSITY_FILE = os.getenv("LOG_VERBOSITY_FILE", "verbose").strip().lower()
if LOG_VERBOSITY_FILE not in {"minimal", "standard", "verbose"}:
    LOG_VERBOSITY_FILE = "verbose"

# Tabular prompt safety: apply per-upload column privacy + PII masking before tool
# results are stored/re-injected into prompts.
# MCP_TABULAR_PRIVACY_ENABLED: Enforce per-upload allow/deny/mask column policies for tabular evidence.
MCP_TABULAR_PRIVACY_ENABLED = os.getenv("MCP_TABULAR_PRIVACY_ENABLED", "true").lower() in {"1", "true", "yes"}
# MCP_TABULAR_PII_REDACTION_ENABLED: Mask PII-like values in tabular evidence (best-effort).
MCP_TABULAR_PII_REDACTION_ENABLED = os.getenv("MCP_TABULAR_PII_REDACTION_ENABLED", "true").lower() in {"1", "true", "yes"}
# Verified lookup mode: require a verified conversation context before returning
# tabular PII fields (addresses/phones/emails/etc) from tools like read_knowledge.
# MCP_VERIFIED_LOOKUP_ENABLED: Enable verified-lookup gating for sensitive tabular outputs.
MCP_VERIFIED_LOOKUP_ENABLED = os.getenv("MCP_VERIFIED_LOOKUP_ENABLED", "true").lower() in {"1", "true", "yes"}
# MCP_VERIFIED_LOOKUP_REQUIRE_FOR_PII: Require verification when the request touches sensitive (PII) columns.
MCP_VERIFIED_LOOKUP_REQUIRE_FOR_PII = os.getenv("MCP_VERIFIED_LOOKUP_REQUIRE_FOR_PII", "true").lower() in {"1", "true", "yes"}
# MCP_VERIFIED_LOOKUP_ALLOW_CUSTOMER_MATCH: Treat authenticated customer sessions as verified (bypass OTP where applicable).
MCP_VERIFIED_LOOKUP_ALLOW_CUSTOMER_MATCH = os.getenv("MCP_VERIFIED_LOOKUP_ALLOW_CUSTOMER_MATCH", "true").lower() in {"1", "true", "yes"}
# MCP_TEXT_PII_REDACTION_ENABLED: Mask common PII patterns in unstructured snippet text before returning evidence to the LLM.
MCP_TEXT_PII_REDACTION_ENABLED = os.getenv("MCP_TEXT_PII_REDACTION_ENABLED", "true").lower() in {"1", "true", "yes"}
# MCP_TEXT_PII_REDACTION_ALLOW_VERIFIED: Allow unredacted unstructured text when verified (keep false until identity scoping exists).
MCP_TEXT_PII_REDACTION_ALLOW_VERIFIED = os.getenv("MCP_TEXT_PII_REDACTION_ALLOW_VERIFIED", "false").lower() in {"1", "true", "yes"}

# Portal verification (OTP) tuning.
try:
    # PORTAL_VERIFICATION_OTP_TTL_SECONDS: OTP validity window (seconds).
    PORTAL_VERIFICATION_OTP_TTL_SECONDS = int(os.getenv("PORTAL_VERIFICATION_OTP_TTL_SECONDS", "600"))
except (TypeError, ValueError):
    PORTAL_VERIFICATION_OTP_TTL_SECONDS = 600
try:
    # PORTAL_VERIFICATION_RESEND_COOLDOWN_SECONDS: Minimum wait before issuing another OTP (seconds).
    PORTAL_VERIFICATION_RESEND_COOLDOWN_SECONDS = int(os.getenv("PORTAL_VERIFICATION_RESEND_COOLDOWN_SECONDS", "30"))
except (TypeError, ValueError):
    PORTAL_VERIFICATION_RESEND_COOLDOWN_SECONDS = 30
try:
    # PORTAL_VERIFICATION_MAX_ATTEMPTS: Maximum invalid attempts before requiring a new OTP.
    PORTAL_VERIFICATION_MAX_ATTEMPTS = int(os.getenv("PORTAL_VERIFICATION_MAX_ATTEMPTS", "5"))
except (TypeError, ValueError):
    PORTAL_VERIFICATION_MAX_ATTEMPTS = 5
# PORTAL_VERIFICATION_DEBUG_RETURN_CODE: Return OTP in API responses for local/dev testing (never enable in prod).
PORTAL_VERIFICATION_DEBUG_RETURN_CODE = os.getenv("PORTAL_VERIFICATION_DEBUG_RETURN_CODE", "false").lower() in {"1", "true", "yes"}
# MCP prompt/context governor. Defaults are conservative to avoid provider context overflows.
# MCP_CONTEXT_GOVERNOR_ENABLED: Enable prompt/token budget enforcement across tool outputs.
MCP_CONTEXT_GOVERNOR_ENABLED = os.getenv("MCP_CONTEXT_GOVERNOR_ENABLED", "true").lower() in {"1", "true", "yes"}
# MCP_PREPLAN_ENABLED: Enable a lightweight pre-plan routing pass before tool calls.
MCP_PREPLAN_ENABLED = os.getenv("MCP_PREPLAN_ENABLED", "false").lower() in {"1", "true", "yes"}
# MCP_VERIFICATION_ENABLED: Enable a lightweight verification pass after answer drafting.
MCP_VERIFICATION_ENABLED = os.getenv("MCP_VERIFICATION_ENABLED", "false").lower() in {"1", "true", "yes"}
# MCP_VERIFICATION_BLOCK_STREAMING: When true, delay streaming until verification completes.
MCP_VERIFICATION_BLOCK_STREAMING = os.getenv("MCP_VERIFICATION_BLOCK_STREAMING", "false").lower() in {"1", "true", "yes"}
# MCP_MAX_CONTEXT_TOKENS: Model context window size assumed for budgeting.
MCP_MAX_CONTEXT_TOKENS = int(os.getenv("MCP_MAX_CONTEXT_TOKENS", "8192"))
# MCP_RESPONSE_TOKEN_RESERVE: Tokens reserved for the model's final answer.
MCP_RESPONSE_TOKEN_RESERVE = int(os.getenv("MCP_RESPONSE_TOKEN_RESERVE", "1200"))
try:
    # MCP_MAX_INPUT_TOKENS: Max input tokens allowed for prompt + tool evidence (derived by default).
    MCP_MAX_INPUT_TOKENS = int(
        os.getenv(
            "MCP_MAX_INPUT_TOKENS",
            str(max(1000, MCP_MAX_CONTEXT_TOKENS - MCP_RESPONSE_TOKEN_RESERVE)),
        )
    )
except (TypeError, ValueError):
    MCP_MAX_INPUT_TOKENS = max(1000, MCP_MAX_CONTEXT_TOKENS - MCP_RESPONSE_TOKEN_RESERVE)

# Proactive prompt compaction (before context limit is reached).
try:
    MCP_PROACTIVE_COMPACTION_TRIGGER_RATIO = float(os.getenv("MCP_PROACTIVE_COMPACTION_TRIGGER_RATIO", "0.9"))
except (TypeError, ValueError):
    MCP_PROACTIVE_COMPACTION_TRIGGER_RATIO = 0.9
MCP_PROACTIVE_COMPACTION_TRIGGER_RATIO = max(0.1, min(1.0, MCP_PROACTIVE_COMPACTION_TRIGGER_RATIO))
try:
    MCP_PROACTIVE_COMPACTION_TARGET_RATIO = float(os.getenv("MCP_PROACTIVE_COMPACTION_TARGET_RATIO", "0.85"))
except (TypeError, ValueError):
    MCP_PROACTIVE_COMPACTION_TARGET_RATIO = 0.85
MCP_PROACTIVE_COMPACTION_TARGET_RATIO = max(
    0.05,
    min(MCP_PROACTIVE_COMPACTION_TARGET_RATIO, MCP_PROACTIVE_COMPACTION_TRIGGER_RATIO),
)
try:
    MCP_PROACTIVE_COMPACTION_KEEP_LAST_TURNS = int(os.getenv("MCP_PROACTIVE_COMPACTION_KEEP_LAST_TURNS", "3"))
except (TypeError, ValueError):
    MCP_PROACTIVE_COMPACTION_KEEP_LAST_TURNS = 3
MCP_PROACTIVE_COMPACTION_KEEP_LAST_TURNS = max(1, min(25, MCP_PROACTIVE_COMPACTION_KEEP_LAST_TURNS))

# MCP long-chat memory (rolling summary + pinned identifiers).
# MCP_LONG_CHAT_MEMORY_ENABLED: Enable rolling memory (summary + pinned identifiers) for long portal chats.
MCP_LONG_CHAT_MEMORY_ENABLED = os.getenv("MCP_LONG_CHAT_MEMORY_ENABLED", "true").lower() in {"1", "true", "yes"}
try:
    # MCP_MEMORY_RECENT_MESSAGES: Number of recent messages kept verbatim in the prompt.
    MCP_MEMORY_RECENT_MESSAGES = int(os.getenv("MCP_MEMORY_RECENT_MESSAGES", "4"))
except (TypeError, ValueError):
    MCP_MEMORY_RECENT_MESSAGES = 4
try:
    # MCP_MEMORY_UPDATE_AFTER_MESSAGES: Refresh summary after N new messages.
    MCP_MEMORY_UPDATE_AFTER_MESSAGES = int(os.getenv("MCP_MEMORY_UPDATE_AFTER_MESSAGES", "10"))
except (TypeError, ValueError):
    MCP_MEMORY_UPDATE_AFTER_MESSAGES = 10
try:
    # MCP_MEMORY_SUMMARY_MAX_CHARS: Max characters for rolling summary.
    MCP_MEMORY_SUMMARY_MAX_CHARS = int(os.getenv("MCP_MEMORY_SUMMARY_MAX_CHARS", "1600"))
except (TypeError, ValueError):
    MCP_MEMORY_SUMMARY_MAX_CHARS = 1600
try:
    # MCP_MEMORY_TURN_MAX_CHARS: Max characters per turn included in memory payloads.
    MCP_MEMORY_TURN_MAX_CHARS = int(os.getenv("MCP_MEMORY_TURN_MAX_CHARS", "1200"))
except (TypeError, ValueError):
    MCP_MEMORY_TURN_MAX_CHARS = 1200
try:
    # MCP_MEMORY_PIN_MAX_ITEMS: Max pinned identifier-like items stored in conversation metadata.
    MCP_MEMORY_PIN_MAX_ITEMS = int(os.getenv("MCP_MEMORY_PIN_MAX_ITEMS", "6"))
except (TypeError, ValueError):
    MCP_MEMORY_PIN_MAX_ITEMS = 6
try:
    # MCP_MEMORY_PIN_VALUE_CHARS: Max length per pinned value stored in memory.
    MCP_MEMORY_PIN_VALUE_CHARS = int(os.getenv("MCP_MEMORY_PIN_VALUE_CHARS", "80"))
except (TypeError, ValueError):
    MCP_MEMORY_PIN_VALUE_CHARS = 80

# Structured memory v2 (replaces single "summary blob" with bounded sections).
try:
    # MCP_MEMORY_V2_ITEM_MAX_CHARS: Max characters per memory item string.
    MCP_MEMORY_V2_ITEM_MAX_CHARS = int(os.getenv("MCP_MEMORY_V2_ITEM_MAX_CHARS", "140"))
except (TypeError, ValueError):
    MCP_MEMORY_V2_ITEM_MAX_CHARS = 140
try:
    # MCP_MEMORY_V2_FACTS_MAX_ITEMS: Max "facts" items stored in memory_v2.
    MCP_MEMORY_V2_FACTS_MAX_ITEMS = int(os.getenv("MCP_MEMORY_V2_FACTS_MAX_ITEMS", "8"))
except (TypeError, ValueError):
    MCP_MEMORY_V2_FACTS_MAX_ITEMS = 8
try:
    # MCP_MEMORY_V2_PREFERENCES_MAX_ITEMS: Max "preferences" items stored in memory_v2.
    MCP_MEMORY_V2_PREFERENCES_MAX_ITEMS = int(os.getenv("MCP_MEMORY_V2_PREFERENCES_MAX_ITEMS", "6"))
except (TypeError, ValueError):
    MCP_MEMORY_V2_PREFERENCES_MAX_ITEMS = 6
try:
    # MCP_MEMORY_V2_OPEN_TASKS_MAX_ITEMS: Max "open_tasks" items stored in memory_v2.
    MCP_MEMORY_V2_OPEN_TASKS_MAX_ITEMS = int(os.getenv("MCP_MEMORY_V2_OPEN_TASKS_MAX_ITEMS", "8"))
except (TypeError, ValueError):
    MCP_MEMORY_V2_OPEN_TASKS_MAX_ITEMS = 8
try:
    # MCP_MEMORY_V2_DECISIONS_MAX_ITEMS: Max "decisions" items stored in memory_v2.
    MCP_MEMORY_V2_DECISIONS_MAX_ITEMS = int(os.getenv("MCP_MEMORY_V2_DECISIONS_MAX_ITEMS", "6"))
except (TypeError, ValueError):
    MCP_MEMORY_V2_DECISIONS_MAX_ITEMS = 6
try:
    # MCP_MEMORY_V2_ARTIFACT_REFS_MAX_ITEMS: Max tool artifact pointers stored in memory_v2.
    MCP_MEMORY_V2_ARTIFACT_REFS_MAX_ITEMS = int(os.getenv("MCP_MEMORY_V2_ARTIFACT_REFS_MAX_ITEMS", "6"))
except (TypeError, ValueError):
    MCP_MEMORY_V2_ARTIFACT_REFS_MAX_ITEMS = 6
try:
    # MCP_MEMORY_V2_ARTIFACT_LABEL_MAX_CHARS: Max characters for stored artifact labels.
    MCP_MEMORY_V2_ARTIFACT_LABEL_MAX_CHARS = int(os.getenv("MCP_MEMORY_V2_ARTIFACT_LABEL_MAX_CHARS", "120"))
except (TypeError, ValueError):
    MCP_MEMORY_V2_ARTIFACT_LABEL_MAX_CHARS = 120
try:
    # MCP_MEMORY_V2_ARTIFACT_PROMPT_VIEW_TEXT_MAX_CHARS: Max characters of prompt_view_text included in memory-update prompts.
    MCP_MEMORY_V2_ARTIFACT_PROMPT_VIEW_TEXT_MAX_CHARS = int(
        os.getenv("MCP_MEMORY_V2_ARTIFACT_PROMPT_VIEW_TEXT_MAX_CHARS", "600")
    )
except (TypeError, ValueError):
    MCP_MEMORY_V2_ARTIFACT_PROMPT_VIEW_TEXT_MAX_CHARS = 600
# RAG_TABLE_SIMILARITY_THRESHOLD: Minimum similarity to consider two tables "similar" for dedupe/routing.
RAG_TABLE_SIMILARITY_THRESHOLD = float(os.getenv("RAG_TABLE_SIMILARITY_THRESHOLD", "0.3"))
# RAG_TABLE_COLUMN_CACHE_SIZE: Cache size for inferred column mappings.
RAG_TABLE_COLUMN_CACHE_SIZE = int(os.getenv("RAG_TABLE_COLUMN_CACHE_SIZE", "32"))
# RAG_TABLE_COLUMN_SAMPLE: Rows sampled when inferring column types/hints.
RAG_TABLE_COLUMN_SAMPLE = int(os.getenv("RAG_TABLE_COLUMN_SAMPLE", "200"))
# RAG_TABLE_SMALL_ROW_LIMIT: Row count threshold for "small" table behavior.
RAG_TABLE_SMALL_ROW_LIMIT = int(os.getenv("RAG_TABLE_SMALL_ROW_LIMIT", "2000"))
# RAG_TABLE_LARGE_ROW_LIMIT: Row count threshold for "large" table behavior / dataset routing.
RAG_TABLE_LARGE_ROW_LIMIT = int(os.getenv("RAG_TABLE_LARGE_ROW_LIMIT", "20000"))
# RAG_TABLE_MAX_HARD_CAP: Hard cap on table rows processed/indexed.
RAG_TABLE_MAX_HARD_CAP = int(os.getenv("RAG_TABLE_MAX_HARD_CAP", "100000"))
# RAG_ENABLE_CROSS_ENCODER: Enable cross-encoder reranking (slower; improves precision).
RAG_ENABLE_CROSS_ENCODER = os.getenv("RAG_ENABLE_CROSS_ENCODER", "false").lower() in {"1", "true", "yes"}
# RAG_CROSS_ENCODER_MODEL: Cross-encoder model name used for reranking.
RAG_CROSS_ENCODER_MODEL = os.getenv("RAG_CROSS_ENCODER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
# RAG_CROSS_ENCODER_DEVICE: Device override for cross-encoder ("cpu", "cuda", etc).
RAG_CROSS_ENCODER_DEVICE = os.getenv("RAG_CROSS_ENCODER_DEVICE")
# RAG_CROSS_ENCODER_TIMEOUT_S: Timeout (seconds) for cross-encoder predict calls (P0 #4: prevents hangs).
try:
    RAG_CROSS_ENCODER_TIMEOUT_S = float(os.getenv("RAG_CROSS_ENCODER_TIMEOUT_S", "3.0"))
except (TypeError, ValueError):
    RAG_CROSS_ENCODER_TIMEOUT_S = 3.0
RAG_CROSS_ENCODER_TIMEOUT_S = max(0.5, min(30.0, RAG_CROSS_ENCODER_TIMEOUT_S))
# RAG_CROSS_ENCODER_POLICY: "always"|"auto"|"off".
# - When unset: the retrieval layer defaults to "auto" if MCP orchestrator is enabled, else "always".
RAG_CROSS_ENCODER_POLICY = (os.getenv("RAG_CROSS_ENCODER_POLICY", "") or "").strip().lower()
if RAG_CROSS_ENCODER_POLICY not in {"", "always", "auto", "off"}:
    RAG_CROSS_ENCODER_POLICY = ""
try:
    # RAG_CROSS_ENCODER_AUTO_MIN_TOKENS: Minimum query token count to run cross-encoder in "auto".
    RAG_CROSS_ENCODER_AUTO_MIN_TOKENS = int(os.getenv("RAG_CROSS_ENCODER_AUTO_MIN_TOKENS", "4"))
except (TypeError, ValueError):
    RAG_CROSS_ENCODER_AUTO_MIN_TOKENS = 4
RAG_CROSS_ENCODER_AUTO_MIN_TOKENS = max(0, RAG_CROSS_ENCODER_AUTO_MIN_TOKENS)
try:
    # RAG_CROSS_ENCODER_AUTO_MIN_CANDIDATES: Minimum candidate pool size to run cross-encoder in "auto".
    RAG_CROSS_ENCODER_AUTO_MIN_CANDIDATES = int(os.getenv("RAG_CROSS_ENCODER_AUTO_MIN_CANDIDATES", "12"))
except (TypeError, ValueError):
    RAG_CROSS_ENCODER_AUTO_MIN_CANDIDATES = 12
RAG_CROSS_ENCODER_AUTO_MIN_CANDIDATES = max(0, RAG_CROSS_ENCODER_AUTO_MIN_CANDIDATES)
try:
    # RAG_CROSS_ENCODER_AUTO_MAX_PAIRS: Max pairs scored by cross-encoder in "auto".
    RAG_CROSS_ENCODER_AUTO_MAX_PAIRS = int(os.getenv("RAG_CROSS_ENCODER_AUTO_MAX_PAIRS", "12"))
except (TypeError, ValueError):
    RAG_CROSS_ENCODER_AUTO_MAX_PAIRS = 12
RAG_CROSS_ENCODER_AUTO_MAX_PAIRS = max(1, RAG_CROSS_ENCODER_AUTO_MAX_PAIRS)
try:
    # RAG_CROSS_ENCODER_AUTO_MAX_CHARS: Max chars per candidate passed to cross-encoder in "auto".
    RAG_CROSS_ENCODER_AUTO_MAX_CHARS = int(os.getenv("RAG_CROSS_ENCODER_AUTO_MAX_CHARS", "1600"))
except (TypeError, ValueError):
    RAG_CROSS_ENCODER_AUTO_MAX_CHARS = 1600
RAG_CROSS_ENCODER_AUTO_MAX_CHARS = max(200, RAG_CROSS_ENCODER_AUTO_MAX_CHARS)
try:
    # RAG_CROSS_ENCODER_AUTO_MARGIN_SKIP: Skip cross-encoder in "auto" when base-score margin is >= this value.
    RAG_CROSS_ENCODER_AUTO_MARGIN_SKIP = float(os.getenv("RAG_CROSS_ENCODER_AUTO_MARGIN_SKIP", "0.25"))
except (TypeError, ValueError):
    RAG_CROSS_ENCODER_AUTO_MARGIN_SKIP = 0.25
RAG_CROSS_ENCODER_AUTO_MARGIN_SKIP = max(0.0, min(5.0, RAG_CROSS_ENCODER_AUTO_MARGIN_SKIP))
# RAG_CROSS_ENCODER_AUTO_SKIP_TABLE_INTENT: Skip cross-encoder in "auto" for table-intent queries.
RAG_CROSS_ENCODER_AUTO_SKIP_TABLE_INTENT = (
    os.getenv("RAG_CROSS_ENCODER_AUTO_SKIP_TABLE_INTENT", "true").lower() in {"1", "true", "yes"}
)
# TABLE_MAX_ROWS_DEFAULT: Default max rows to scan/preview for table operations.
TABLE_MAX_ROWS_DEFAULT = int(os.getenv("TABLE_MAX_ROWS_DEFAULT", "5000"))
# TABLE_MAX_COLUMNS_DEFAULT: Default max columns to include for table operations (0 = unlimited/auto).
TABLE_MAX_COLUMNS_DEFAULT = int(os.getenv("TABLE_MAX_COLUMNS_DEFAULT", "0"))
# DATASET_MODE_ENABLED: Store huge tables as datasets (files + lightweight metadata) instead of per-row DB storage.
DATASET_MODE_ENABLED = os.getenv("DATASET_MODE_ENABLED", "true").lower() in {"1", "true", "yes"}
try:
    # DATASET_MODE_ROW_THRESHOLD: Row threshold above which tables are stored/queried as datasets.
    DATASET_MODE_ROW_THRESHOLD = int(os.getenv("DATASET_MODE_ROW_THRESHOLD", str(RAG_TABLE_LARGE_ROW_LIMIT)))
except (TypeError, ValueError):
    DATASET_MODE_ROW_THRESHOLD = int(RAG_TABLE_LARGE_ROW_LIMIT)
try:
    # DATASET_MODE_PREVIEW_ROWS: Rows included in dataset previews/cards.
    DATASET_MODE_PREVIEW_ROWS = int(os.getenv("DATASET_MODE_PREVIEW_ROWS", "200"))
except (TypeError, ValueError):
    DATASET_MODE_PREVIEW_ROWS = 200
try:
    # DATASET_MODE_SAMPLE_ROWS: Rows sampled when profiling datasets.
    DATASET_MODE_SAMPLE_ROWS = int(os.getenv("DATASET_MODE_SAMPLE_ROWS", "20"))
except (TypeError, ValueError):
    DATASET_MODE_SAMPLE_ROWS = 20
# DATASET_STORAGE_FORMAT: On-disk format for dataset storage ("csv_gz", etc).
DATASET_STORAGE_FORMAT = os.getenv("DATASET_STORAGE_FORMAT", "csv_gz").strip() or "csv_gz"
# DATASET_QUERY_ENGINE: Dataset query engine ("duckdb"|"python"|"auto").
DATASET_QUERY_ENGINE = (os.getenv("DATASET_QUERY_ENGINE", "duckdb") or "duckdb").strip().lower() or "duckdb"
if DATASET_QUERY_ENGINE not in {"duckdb", "python", "auto"}:
    DATASET_QUERY_ENGINE = "duckdb"
try:
    # DATASET_QUERY_MAX_SECONDS: Time budget (seconds) for dataset queries.
    DATASET_QUERY_MAX_SECONDS = float(os.getenv("DATASET_QUERY_MAX_SECONDS", "2.5"))
except (TypeError, ValueError):
    DATASET_QUERY_MAX_SECONDS = 2.5
if DATASET_QUERY_MAX_SECONDS <= 0:
    DATASET_QUERY_MAX_SECONDS = 2.5
try:
    # DATASET_QUERY_MAX_SORT_WINDOW: Max rows considered for sorting before truncation.
    DATASET_QUERY_MAX_SORT_WINDOW = int(os.getenv("DATASET_QUERY_MAX_SORT_WINDOW", "500"))
except (TypeError, ValueError):
    DATASET_QUERY_MAX_SORT_WINDOW = 500
if DATASET_QUERY_MAX_SORT_WINDOW < 50:
    DATASET_QUERY_MAX_SORT_WINDOW = 50
try:
    # DATASET_QUERY_DEFAULT_COLUMNS: Default number of columns returned when caller does not specify.
    DATASET_QUERY_DEFAULT_COLUMNS = int(os.getenv("DATASET_QUERY_DEFAULT_COLUMNS", "8"))
except (TypeError, ValueError):
    DATASET_QUERY_DEFAULT_COLUMNS = 8
if DATASET_QUERY_DEFAULT_COLUMNS < 3:
    DATASET_QUERY_DEFAULT_COLUMNS = 3
try:
    # DATASET_QUERY_CELL_VALUE_CHARS: Max characters per cell value returned in dataset evidence.
    DATASET_QUERY_CELL_VALUE_CHARS = int(os.getenv("DATASET_QUERY_CELL_VALUE_CHARS", "160"))
except (TypeError, ValueError):
    DATASET_QUERY_CELL_VALUE_CHARS = 160
if DATASET_QUERY_CELL_VALUE_CHARS < 40:
    DATASET_QUERY_CELL_VALUE_CHARS = 40
try:
    # DATASET_QUERY_MAX_GROUPS: Max groups returned for group-by aggregates.
    DATASET_QUERY_MAX_GROUPS = int(os.getenv("DATASET_QUERY_MAX_GROUPS", "5000"))
except (TypeError, ValueError):
    DATASET_QUERY_MAX_GROUPS = 5000
if DATASET_QUERY_MAX_GROUPS < 100:
    DATASET_QUERY_MAX_GROUPS = 100
try:
    # DATASET_QUERY_MAX_ROWS_RETURNED: Max rows returned for dataset queries.
    DATASET_QUERY_MAX_ROWS_RETURNED = int(os.getenv("DATASET_QUERY_MAX_ROWS_RETURNED", "50"))
except (TypeError, ValueError):
    DATASET_QUERY_MAX_ROWS_RETURNED = 50
if DATASET_QUERY_MAX_ROWS_RETURNED < 1:
    DATASET_QUERY_MAX_ROWS_RETURNED = 1
if DATASET_QUERY_MAX_ROWS_RETURNED > 50:
    DATASET_QUERY_MAX_ROWS_RETURNED = 50
try:
    # DATASET_QUERY_MAX_COLUMNS_RETURNED: Max columns returned for dataset queries.
    DATASET_QUERY_MAX_COLUMNS_RETURNED = int(os.getenv("DATASET_QUERY_MAX_COLUMNS_RETURNED", "12"))
except (TypeError, ValueError):
    DATASET_QUERY_MAX_COLUMNS_RETURNED = 12
if DATASET_QUERY_MAX_COLUMNS_RETURNED < 3:
    DATASET_QUERY_MAX_COLUMNS_RETURNED = 3
if DATASET_QUERY_MAX_COLUMNS_RETURNED > 50:
    DATASET_QUERY_MAX_COLUMNS_RETURNED = 50
try:
    # DATASET_QUERY_MAX_COLUMNS_RETURNED_EXACT: Higher cap for exact-match dataset responses.
    DATASET_QUERY_MAX_COLUMNS_RETURNED_EXACT = int(os.getenv("DATASET_QUERY_MAX_COLUMNS_RETURNED_EXACT", "50"))
except (TypeError, ValueError):
    DATASET_QUERY_MAX_COLUMNS_RETURNED_EXACT = 50
if DATASET_QUERY_MAX_COLUMNS_RETURNED_EXACT < DATASET_QUERY_MAX_COLUMNS_RETURNED:
    DATASET_QUERY_MAX_COLUMNS_RETURNED_EXACT = DATASET_QUERY_MAX_COLUMNS_RETURNED
if DATASET_QUERY_MAX_COLUMNS_RETURNED_EXACT > 50:
    DATASET_QUERY_MAX_COLUMNS_RETURNED_EXACT = 50

# Dataset key indexing (routing layer for many datasets).
# DATASET_KEY_INDEX_ENABLED: Enable Bloom-filter style key index generation for datasets.
DATASET_KEY_INDEX_ENABLED = os.getenv("DATASET_KEY_INDEX_ENABLED", "true").lower() in {"1", "true", "yes"}
# DATASET_KEY_INDEX_ROUTING_ENABLED: Allow routing identifier queries to datasets using the key index.
DATASET_KEY_INDEX_ROUTING_ENABLED = os.getenv("DATASET_KEY_INDEX_ROUTING_ENABLED", "true").lower() in {"1", "true", "yes"}
# DATASET_KEY_INDEX_ALLOW_SENSITIVE: Whether key indexing may include sensitive identifier columns.
DATASET_KEY_INDEX_ALLOW_SENSITIVE = os.getenv("DATASET_KEY_INDEX_ALLOW_SENSITIVE", "false").lower() in {"1", "true", "yes"}
try:
    # DATASET_KEY_INDEX_MAX_COLUMNS: Max columns per dataset to include in key indexing.
    DATASET_KEY_INDEX_MAX_COLUMNS = int(os.getenv("DATASET_KEY_INDEX_MAX_COLUMNS", "4"))
except (TypeError, ValueError):
    DATASET_KEY_INDEX_MAX_COLUMNS = 4
DATASET_KEY_INDEX_MAX_COLUMNS = max(0, min(20, DATASET_KEY_INDEX_MAX_COLUMNS))
try:
    # DATASET_KEY_INDEX_BITS_PER_ITEM: Bits per item for Bloom filter sizing (higher = fewer false positives).
    DATASET_KEY_INDEX_BITS_PER_ITEM = int(os.getenv("DATASET_KEY_INDEX_BITS_PER_ITEM", "10"))
except (TypeError, ValueError):
    DATASET_KEY_INDEX_BITS_PER_ITEM = 10
DATASET_KEY_INDEX_BITS_PER_ITEM = max(4, min(24, DATASET_KEY_INDEX_BITS_PER_ITEM))
try:
    # DATASET_KEY_INDEX_MAX_BYTES: Max bytes per index blob (caps memory/DB usage).
    DATASET_KEY_INDEX_MAX_BYTES = int(os.getenv("DATASET_KEY_INDEX_MAX_BYTES", "2000000"))
except (TypeError, ValueError):
    DATASET_KEY_INDEX_MAX_BYTES = 2_000_000
DATASET_KEY_INDEX_MAX_BYTES = max(4096, min(25_000_000, DATASET_KEY_INDEX_MAX_BYTES))
try:
    # DATASET_KEY_INDEX_SUGGESTED_MIN_SCORE: Suggested minimum match score for routing hints.
    DATASET_KEY_INDEX_SUGGESTED_MIN_SCORE = float(os.getenv("DATASET_KEY_INDEX_SUGGESTED_MIN_SCORE", "0.9"))
except (TypeError, ValueError):
    DATASET_KEY_INDEX_SUGGESTED_MIN_SCORE = 0.9
DATASET_KEY_INDEX_SUGGESTED_MIN_SCORE = max(0.0, min(10.0, DATASET_KEY_INDEX_SUGGESTED_MIN_SCORE))
try:
    # DATASET_KEY_INDEX_CACHE_SIZE: Cache size for key-index lookups.
    DATASET_KEY_INDEX_CACHE_SIZE = int(os.getenv("DATASET_KEY_INDEX_CACHE_SIZE", "128"))
except (TypeError, ValueError):
    DATASET_KEY_INDEX_CACHE_SIZE = 128
DATASET_KEY_INDEX_CACHE_SIZE = max(8, min(2048, DATASET_KEY_INDEX_CACHE_SIZE))
try:
    # DATASET_KEY_INDEX_MAX_MATCHES: Max dataset candidates returned from key index routing.
    DATASET_KEY_INDEX_MAX_MATCHES = int(os.getenv("DATASET_KEY_INDEX_MAX_MATCHES", "8"))
except (TypeError, ValueError):
    DATASET_KEY_INDEX_MAX_MATCHES = 8
DATASET_KEY_INDEX_MAX_MATCHES = max(1, min(50, DATASET_KEY_INDEX_MAX_MATCHES))
try:
    # DATASET_KEY_INDEX_MAX_MATCHES_PER_UPLOAD: Max matches returned per upload for key routing.
    DATASET_KEY_INDEX_MAX_MATCHES_PER_UPLOAD = int(os.getenv("DATASET_KEY_INDEX_MAX_MATCHES_PER_UPLOAD", "8"))
except (TypeError, ValueError):
    DATASET_KEY_INDEX_MAX_MATCHES_PER_UPLOAD = 8
DATASET_KEY_INDEX_MAX_MATCHES_PER_UPLOAD = max(1, min(50, DATASET_KEY_INDEX_MAX_MATCHES_PER_UPLOAD))
try:
    # TABLE_AGGREGATE_MAX_ROWS_RETURNED: Hard cap for table_aggregate rows returned to the model.
    TABLE_AGGREGATE_MAX_ROWS_RETURNED = int(os.getenv("TABLE_AGGREGATE_MAX_ROWS_RETURNED", "200"))
except (TypeError, ValueError):
    TABLE_AGGREGATE_MAX_ROWS_RETURNED = 200
if TABLE_AGGREGATE_MAX_ROWS_RETURNED < 1:
    TABLE_AGGREGATE_MAX_ROWS_RETURNED = 1
if TABLE_AGGREGATE_MAX_ROWS_RETURNED > 200:
    TABLE_AGGREGATE_MAX_ROWS_RETURNED = 200
try:
    # TABLE_AGGREGATE_MAX_COLUMNS_RETURNED: Hard cap for table_aggregate columns returned to the model.
    TABLE_AGGREGATE_MAX_COLUMNS_RETURNED = int(os.getenv("TABLE_AGGREGATE_MAX_COLUMNS_RETURNED", "50"))
except (TypeError, ValueError):
    TABLE_AGGREGATE_MAX_COLUMNS_RETURNED = 50
if TABLE_AGGREGATE_MAX_COLUMNS_RETURNED < 3:
    TABLE_AGGREGATE_MAX_COLUMNS_RETURNED = 3
if TABLE_AGGREGATE_MAX_COLUMNS_RETURNED > 200:
    TABLE_AGGREGATE_MAX_COLUMNS_RETURNED = 200
try:
    # MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS: Window size (seconds) for per-tool rate limiting.
    MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS", "60"))
except (TypeError, ValueError):
    MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS = 60
if MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS < 10:
    MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS = 10
if MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS > 600:
    MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS = 600
try:
    # MCP_DATASET_QUERY_CALLS_PER_MINUTE: Per-tenant rate limit for dataset queries.
    MCP_DATASET_QUERY_CALLS_PER_MINUTE = int(os.getenv("MCP_DATASET_QUERY_CALLS_PER_MINUTE", "30"))
except (TypeError, ValueError):
    MCP_DATASET_QUERY_CALLS_PER_MINUTE = 30
if MCP_DATASET_QUERY_CALLS_PER_MINUTE < 0:
    MCP_DATASET_QUERY_CALLS_PER_MINUTE = 0
try:
    # MCP_TABLE_AGGREGATE_CALLS_PER_MINUTE: Per-tenant rate limit for table_aggregate calls.
    MCP_TABLE_AGGREGATE_CALLS_PER_MINUTE = int(os.getenv("MCP_TABLE_AGGREGATE_CALLS_PER_MINUTE", "60"))
except (TypeError, ValueError):
    MCP_TABLE_AGGREGATE_CALLS_PER_MINUTE = 60
if MCP_TABLE_AGGREGATE_CALLS_PER_MINUTE < 0:
    MCP_TABLE_AGGREGATE_CALLS_PER_MINUTE = 0
try:
    # MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE: Per-tenant rate limit for search_knowledge calls.
    MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE = int(os.getenv("MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE", "120"))
except (TypeError, ValueError):
    MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE = 120
if MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE < 0:
    MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE = 0
try:
    # MCP_READ_KNOWLEDGE_CALLS_PER_MINUTE: Per-tenant rate limit for read_knowledge/read_document calls.
    MCP_READ_KNOWLEDGE_CALLS_PER_MINUTE = int(os.getenv("MCP_READ_KNOWLEDGE_CALLS_PER_MINUTE", "120"))
except (TypeError, ValueError):
    MCP_READ_KNOWLEDGE_CALLS_PER_MINUTE = 120
if MCP_READ_KNOWLEDGE_CALLS_PER_MINUTE < 0:
    MCP_READ_KNOWLEDGE_CALLS_PER_MINUTE = 0
try:
    # MCP_LIST_TABLES_CALLS_PER_MINUTE: Per-tenant rate limit for list_tables calls.
    MCP_LIST_TABLES_CALLS_PER_MINUTE = int(os.getenv("MCP_LIST_TABLES_CALLS_PER_MINUTE", "120"))
except (TypeError, ValueError):
    MCP_LIST_TABLES_CALLS_PER_MINUTE = 120
if MCP_LIST_TABLES_CALLS_PER_MINUTE < 0:
    MCP_LIST_TABLES_CALLS_PER_MINUTE = 0

# MCP_DISABLE_TOOL_RATE_LIMITS: Disable cache-backed tool rate limits (useful for CI/load tests).
MCP_DISABLE_TOOL_RATE_LIMITS = os.getenv("MCP_DISABLE_TOOL_RATE_LIMITS", "false").lower() in {"1", "true", "yes"}

# MCP_UNCAPPED_LIMITS: Allow env var budgets above the default safety clamps.
# Default behavior keeps conservative upper bounds to protect latency/cost.
MCP_UNCAPPED_LIMITS = os.getenv("MCP_UNCAPPED_LIMITS", "false").lower() in {"1", "true", "yes"}

# MCP/orchestrator hard caps (cost controls).
# MCP_NEW_CONTRACT_ENABLED: Gate the "one voice + smart tools" contract (budgets,
# semantic dedup, agentic RAG prompt/response shapes). When disabled, the system
# falls back to legacy prompt + tool behaviors.
MCP_NEW_CONTRACT_ENABLED = os.getenv("MCP_NEW_CONTRACT_ENABLED", "true").lower() in {"1", "true", "yes"}
# MCP_AGENTIC_READ_V2_ENABLED: Gate the simplified agentic read contract:
# `read_document(items=[{id,cursor?}...], max_chars=...)` with tool-selected retrieval
# + deterministic continuation. This is intentionally separate from MCP_NEW_CONTRACT_ENABLED
# so we can roll out V2 read behavior gradually.
MCP_AGENTIC_READ_V2_ENABLED = os.getenv("MCP_AGENTIC_READ_V2_ENABLED", "false").lower() in {"1", "true", "yes"}
try:
    # MCP_MAX_TOOL_ITERATIONS: Hard cap on tool calls per turn.
    MCP_MAX_TOOL_ITERATIONS = int(os.getenv("MCP_MAX_TOOL_ITERATIONS", "10"))
except (TypeError, ValueError):
    MCP_MAX_TOOL_ITERATIONS = 10
if MCP_UNCAPPED_LIMITS:
    MCP_MAX_TOOL_ITERATIONS = max(1, MCP_MAX_TOOL_ITERATIONS)
else:
    MCP_MAX_TOOL_ITERATIONS = max(1, min(50, MCP_MAX_TOOL_ITERATIONS))
try:
    # MCP_MAX_SEARCHES_PER_TURN: Limit search_knowledge calls per user message (0 disables limit).
    MCP_MAX_SEARCHES_PER_TURN = int(os.getenv("MCP_MAX_SEARCHES_PER_TURN", "5"))
except (TypeError, ValueError):
    MCP_MAX_SEARCHES_PER_TURN = 5
if MCP_MAX_SEARCHES_PER_TURN < 0:
    MCP_MAX_SEARCHES_PER_TURN = 0
try:
    # MCP_MAX_READS_PER_TURN: Limit read_document calls per user message (0 disables limit).
    # This is primarily used for LLM-visible budgeting; enforcement is optional.
    MCP_MAX_READS_PER_TURN = int(os.getenv("MCP_MAX_READS_PER_TURN", "10"))
except (TypeError, ValueError):
    MCP_MAX_READS_PER_TURN = 10
if MCP_MAX_READS_PER_TURN < 0:
    MCP_MAX_READS_PER_TURN = 0

# MCP_ENFORCE_READ_BUDGET: When enabled, read_document calls beyond MCP_MAX_READS_PER_TURN
# raise a constraint error. Default is off (budget is still tracked in tool responses).
MCP_ENFORCE_READ_BUDGET = os.getenv("MCP_ENFORCE_READ_BUDGET", "false").lower() in {"1", "true", "yes"}
try:
    # MCP_READ_DOCUMENT_REPEAT_LIMIT: Force-final after repeating the same read_document signature this many times.
    MCP_READ_DOCUMENT_REPEAT_LIMIT = int(os.getenv("MCP_READ_DOCUMENT_REPEAT_LIMIT", "2"))
except (TypeError, ValueError):
    MCP_READ_DOCUMENT_REPEAT_LIMIT = 2
if MCP_READ_DOCUMENT_REPEAT_LIMIT < 0:
    MCP_READ_DOCUMENT_REPEAT_LIMIT = 0
MCP_READ_DOCUMENT_REPEAT_LIMIT = min(1000, MCP_READ_DOCUMENT_REPEAT_LIMIT)
try:
    # MCP_READ_DOCUMENT_THROTTLE_LIMIT: Force-final after hitting throttled read_document responses this many times.
    MCP_READ_DOCUMENT_THROTTLE_LIMIT = int(os.getenv("MCP_READ_DOCUMENT_THROTTLE_LIMIT", "2"))
except (TypeError, ValueError):
    MCP_READ_DOCUMENT_THROTTLE_LIMIT = 2
if MCP_READ_DOCUMENT_THROTTLE_LIMIT < 0:
    MCP_READ_DOCUMENT_THROTTLE_LIMIT = 0
MCP_READ_DOCUMENT_THROTTLE_LIMIT = min(1000, MCP_READ_DOCUMENT_THROTTLE_LIMIT)

# MCP enumeration flow controls.
MCP_ENUMERATION_AUTO_STRUCTURE_ENABLED = os.getenv("MCP_ENUMERATION_AUTO_STRUCTURE_ENABLED", "true").lower() in {"1", "true", "yes"}
try:
    # MCP_ENUMERATION_MAX_DOCUMENTS: Max documents to auto-structure per turn.
    MCP_ENUMERATION_MAX_DOCUMENTS = int(os.getenv("MCP_ENUMERATION_MAX_DOCUMENTS", "3"))
except (TypeError, ValueError):
    MCP_ENUMERATION_MAX_DOCUMENTS = 3
MCP_ENUMERATION_MAX_DOCUMENTS = max(1, min(25, MCP_ENUMERATION_MAX_DOCUMENTS))
MCP_ENUMERATION_AUTO_FETCH_ENABLED = os.getenv("MCP_ENUMERATION_AUTO_FETCH_ENABLED", "true").lower() in {"1", "true", "yes"}
try:
    # MCP_ENUMERATION_AUTO_FETCH_MAX_ROWS: Max rows to auto-fetch per table.
    MCP_ENUMERATION_AUTO_FETCH_MAX_ROWS = int(os.getenv("MCP_ENUMERATION_AUTO_FETCH_MAX_ROWS", "200"))
except (TypeError, ValueError):
    MCP_ENUMERATION_AUTO_FETCH_MAX_ROWS = 200
MCP_ENUMERATION_AUTO_FETCH_MAX_ROWS = max(1, min(200, MCP_ENUMERATION_AUTO_FETCH_MAX_ROWS))
try:
    # MCP_ENUMERATION_AUTO_FETCH_MAX_TABLES: Max tables to auto-fetch per document.
    MCP_ENUMERATION_AUTO_FETCH_MAX_TABLES = int(os.getenv("MCP_ENUMERATION_AUTO_FETCH_MAX_TABLES", "3"))
except (TypeError, ValueError):
    MCP_ENUMERATION_AUTO_FETCH_MAX_TABLES = 3
MCP_ENUMERATION_AUTO_FETCH_MAX_TABLES = max(1, min(20, MCP_ENUMERATION_AUTO_FETCH_MAX_TABLES))
try:
    # RAG_MAX_CHUNK_READS_PER_TURN: Max chunk/page reads per turn (document reads via tools).
    RAG_MAX_CHUNK_READS_PER_TURN = int(os.getenv("RAG_MAX_CHUNK_READS_PER_TURN", "3"))
except (TypeError, ValueError):
    RAG_MAX_CHUNK_READS_PER_TURN = 3
if MCP_UNCAPPED_LIMITS:
    RAG_MAX_CHUNK_READS_PER_TURN = max(1, RAG_MAX_CHUNK_READS_PER_TURN)
else:
    RAG_MAX_CHUNK_READS_PER_TURN = max(1, min(20, RAG_MAX_CHUNK_READS_PER_TURN))
try:
    # RAG_MAX_CHUNK_PAGES_PER_TURN: Max distinct pages that can be read per turn.
    RAG_MAX_CHUNK_PAGES_PER_TURN = int(os.getenv("RAG_MAX_CHUNK_PAGES_PER_TURN", "3"))
except (TypeError, ValueError):
    RAG_MAX_CHUNK_PAGES_PER_TURN = 3
if MCP_UNCAPPED_LIMITS:
    RAG_MAX_CHUNK_PAGES_PER_TURN = max(1, RAG_MAX_CHUNK_PAGES_PER_TURN)
else:
    RAG_MAX_CHUNK_PAGES_PER_TURN = max(1, min(20, RAG_MAX_CHUNK_PAGES_PER_TURN))
try:
    # RAG_MAX_CHAR_BUDGET_PER_TURN: Character budget for prompt + tool evidence per turn.
    RAG_MAX_CHAR_BUDGET_PER_TURN = int(os.getenv("RAG_MAX_CHAR_BUDGET_PER_TURN", "200000"))
except (TypeError, ValueError):
    RAG_MAX_CHAR_BUDGET_PER_TURN = 200000
RAG_MAX_CHAR_BUDGET_PER_TURN = max(4000, min(200000, RAG_MAX_CHAR_BUDGET_PER_TURN))
try:
    # RAG_MAX_CHAR_BUDGET_PER_MINUTE: Rolling character budget per tenant per minute.
    RAG_MAX_CHAR_BUDGET_PER_MINUTE = int(os.getenv("RAG_MAX_CHAR_BUDGET_PER_MINUTE", "400000"))
except (TypeError, ValueError):
    RAG_MAX_CHAR_BUDGET_PER_MINUTE = 400000
RAG_MAX_CHAR_BUDGET_PER_MINUTE = max(4000, min(500000, RAG_MAX_CHAR_BUDGET_PER_MINUTE))
try:
    # RAG_CHAR_BUDGET_WINDOW_SECONDS: Window size (seconds) for per-minute character budgeting.
    RAG_CHAR_BUDGET_WINDOW_SECONDS = int(os.getenv("RAG_CHAR_BUDGET_WINDOW_SECONDS", "60"))
except (TypeError, ValueError):
    RAG_CHAR_BUDGET_WINDOW_SECONDS = 60
RAG_CHAR_BUDGET_WINDOW_SECONDS = max(30, min(600, RAG_CHAR_BUDGET_WINDOW_SECONDS))

# Observability / SLO thresholds (used for warning-level structured logs).
try:
    # MCP_SLO_SEARCH_WARN_MS: Log warning when search_knowledge exceeds this duration (ms).
    MCP_SLO_SEARCH_WARN_MS = int(os.getenv("MCP_SLO_SEARCH_WARN_MS", "1200"))
except (TypeError, ValueError):
    MCP_SLO_SEARCH_WARN_MS = 1200
if MCP_SLO_SEARCH_WARN_MS < 0:
    MCP_SLO_SEARCH_WARN_MS = 0
if MCP_SLO_SEARCH_WARN_MS > 120_000:
    MCP_SLO_SEARCH_WARN_MS = 120_000

try:
    # MCP_SLO_READ_KNOWLEDGE_WARN_MS: Log warning when read_knowledge exceeds this duration (ms).
    MCP_SLO_READ_KNOWLEDGE_WARN_MS = int(os.getenv("MCP_SLO_READ_KNOWLEDGE_WARN_MS", "1500"))
except (TypeError, ValueError):
    MCP_SLO_READ_KNOWLEDGE_WARN_MS = 1500
if MCP_SLO_READ_KNOWLEDGE_WARN_MS < 0:
    MCP_SLO_READ_KNOWLEDGE_WARN_MS = 0
if MCP_SLO_READ_KNOWLEDGE_WARN_MS > 120_000:
    MCP_SLO_READ_KNOWLEDGE_WARN_MS = 120_000

try:
    # MCP_SLO_TABLE_AGGREGATE_WARN_MS: Log warning when table_aggregate exceeds this duration (ms).
    MCP_SLO_TABLE_AGGREGATE_WARN_MS = int(os.getenv("MCP_SLO_TABLE_AGGREGATE_WARN_MS", "1200"))
except (TypeError, ValueError):
    MCP_SLO_TABLE_AGGREGATE_WARN_MS = 1200
if MCP_SLO_TABLE_AGGREGATE_WARN_MS < 0:
    MCP_SLO_TABLE_AGGREGATE_WARN_MS = 0
if MCP_SLO_TABLE_AGGREGATE_WARN_MS > 120_000:
    MCP_SLO_TABLE_AGGREGATE_WARN_MS = 120_000

try:
    # MCP_SLO_DATASET_QUERY_WARN_MS: Log warning when dataset query exceeds this duration (ms).
    MCP_SLO_DATASET_QUERY_WARN_MS = int(os.getenv("MCP_SLO_DATASET_QUERY_WARN_MS", "1500"))
except (TypeError, ValueError):
    MCP_SLO_DATASET_QUERY_WARN_MS = 1500
if MCP_SLO_DATASET_QUERY_WARN_MS < 0:
    MCP_SLO_DATASET_QUERY_WARN_MS = 0
if MCP_SLO_DATASET_QUERY_WARN_MS > 120_000:
    MCP_SLO_DATASET_QUERY_WARN_MS = 120_000

try:
    # MCP_SLO_TURN_WARN_MS: Log warning when a full MCP turn exceeds this duration (ms).
    MCP_SLO_TURN_WARN_MS = int(os.getenv("MCP_SLO_TURN_WARN_MS", "15000"))
except (TypeError, ValueError):
    MCP_SLO_TURN_WARN_MS = 15000
if MCP_SLO_TURN_WARN_MS < 0:
    MCP_SLO_TURN_WARN_MS = 0
if MCP_SLO_TURN_WARN_MS > 600_000:
    MCP_SLO_TURN_WARN_MS = 600_000

try:
    # MCP_SLO_TOOL_CALLS_WARN: Log warning when tool calls per turn exceed this count.
    MCP_SLO_TOOL_CALLS_WARN = int(os.getenv("MCP_SLO_TOOL_CALLS_WARN", "6"))
except (TypeError, ValueError):
    MCP_SLO_TOOL_CALLS_WARN = 6
if MCP_SLO_TOOL_CALLS_WARN < 0:
    MCP_SLO_TOOL_CALLS_WARN = 0
if MCP_SLO_TOOL_CALLS_WARN > 50:
    MCP_SLO_TOOL_CALLS_WARN = 50

try:
    # INGEST_SLO_WARN_MS: Log warning when ingestion job exceeds this duration (ms).
    INGEST_SLO_WARN_MS = int(os.getenv("INGEST_SLO_WARN_MS", "60000"))
except (TypeError, ValueError):
    INGEST_SLO_WARN_MS = 60000
if INGEST_SLO_WARN_MS < 0:
    INGEST_SLO_WARN_MS = 0
if INGEST_SLO_WARN_MS > 3_600_000:
    INGEST_SLO_WARN_MS = 3_600_000

try:
    # INGEST_WORKER_HEALTH_INTERVAL_SECONDS: Heartbeat interval (seconds) for ingestion worker health.
    INGEST_WORKER_HEALTH_INTERVAL_SECONDS = int(os.getenv("INGEST_WORKER_HEALTH_INTERVAL_SECONDS", "60"))
except (TypeError, ValueError):
    INGEST_WORKER_HEALTH_INTERVAL_SECONDS = 60
if INGEST_WORKER_HEALTH_INTERVAL_SECONDS < 0:
    INGEST_WORKER_HEALTH_INTERVAL_SECONDS = 0
if INGEST_WORKER_HEALTH_INTERVAL_SECONDS > 3_600:
    INGEST_WORKER_HEALTH_INTERVAL_SECONDS = 3_600

try:
    # INGEST_QUEUE_WARN_BACKLOG: Warn when queued ingestion jobs exceed this count.
    INGEST_QUEUE_WARN_BACKLOG = int(os.getenv("INGEST_QUEUE_WARN_BACKLOG", "50"))
except (TypeError, ValueError):
    INGEST_QUEUE_WARN_BACKLOG = 50
if INGEST_QUEUE_WARN_BACKLOG < 0:
    INGEST_QUEUE_WARN_BACKLOG = 0

try:
    # INGEST_QUEUE_WARN_OLDEST_SECONDS: Warn when oldest queued ingest job is older than this (seconds).
    INGEST_QUEUE_WARN_OLDEST_SECONDS = int(os.getenv("INGEST_QUEUE_WARN_OLDEST_SECONDS", "900"))
except (TypeError, ValueError):
    INGEST_QUEUE_WARN_OLDEST_SECONDS = 900
if INGEST_QUEUE_WARN_OLDEST_SECONDS < 0:
    INGEST_QUEUE_WARN_OLDEST_SECONDS = 0
if INGEST_QUEUE_WARN_OLDEST_SECONDS > 86_400:
    INGEST_QUEUE_WARN_OLDEST_SECONDS = 86_400

try:
    # INGEST_QUEUE_WARN_FAILED_LAST_HOUR: Warn when failed ingestion jobs in last hour exceed this count.
    INGEST_QUEUE_WARN_FAILED_LAST_HOUR = int(os.getenv("INGEST_QUEUE_WARN_FAILED_LAST_HOUR", "10"))
except (TypeError, ValueError):
    INGEST_QUEUE_WARN_FAILED_LAST_HOUR = 10
if INGEST_QUEUE_WARN_FAILED_LAST_HOUR < 0:
    INGEST_QUEUE_WARN_FAILED_LAST_HOUR = 0
try:
    # DATASET_CARD_MAX_CHARS: Max characters shown in dataset summary “cards”.
    DATASET_CARD_MAX_CHARS = int(os.getenv("DATASET_CARD_MAX_CHARS", str(RAG_SEARCH_PREVIEW_CHAR_LIMIT)))
except (TypeError, ValueError):
    DATASET_CARD_MAX_CHARS = int(RAG_SEARCH_PREVIEW_CHAR_LIMIT)
if DATASET_CARD_MAX_CHARS < 200:
    DATASET_CARD_MAX_CHARS = 200
try:
    # DATASET_CARD_MAX_COLUMNS: Max columns listed in dataset cards/previews.
    DATASET_CARD_MAX_COLUMNS = int(os.getenv("DATASET_CARD_MAX_COLUMNS", "60"))
except (TypeError, ValueError):
    DATASET_CARD_MAX_COLUMNS = 60
if DATASET_CARD_MAX_COLUMNS < 10:
    DATASET_CARD_MAX_COLUMNS = 10
try:
    # DATASET_CARD_MAX_SHEETS: Max sheets listed in dataset cards/previews.
    DATASET_CARD_MAX_SHEETS = int(os.getenv("DATASET_CARD_MAX_SHEETS", "8"))
except (TypeError, ValueError):
    DATASET_CARD_MAX_SHEETS = 8
if DATASET_CARD_MAX_SHEETS < 1:
    DATASET_CARD_MAX_SHEETS = 1
# INGEST_MAX_ACTIVE_JOBS_PER_BUSINESS: Concurrency cap for active ingestion jobs per tenant.
INGEST_MAX_ACTIVE_JOBS_PER_BUSINESS = int(os.getenv("INGEST_MAX_ACTIVE_JOBS_PER_BUSINESS", "3"))
# INGEST_SYNC_EMBED_CHUNK_LIMIT: If below this chunk count, embed synchronously in-process.
INGEST_SYNC_EMBED_CHUNK_LIMIT = int(os.getenv("INGEST_SYNC_EMBED_CHUNK_LIMIT", "200"))
# INGEST_EMBED_BATCH_SIZE: Batch size for embedding jobs.
INGEST_EMBED_BATCH_SIZE = int(os.getenv("INGEST_EMBED_BATCH_SIZE", "64"))
# INGEST_EMBEDDING_BACKLOG_THRESHOLD: Warn/pressure when pending embedding backlog exceeds this.
INGEST_EMBEDDING_BACKLOG_THRESHOLD = int(os.getenv("INGEST_EMBEDDING_BACKLOG_THRESHOLD", "500"))
try:
    # INGEST_JOB_MAX_ATTEMPTS: Max attempts for a single ingestion job before marking failed.
    INGEST_JOB_MAX_ATTEMPTS = int(os.getenv("INGEST_JOB_MAX_ATTEMPTS", "3"))
except (TypeError, ValueError):
    INGEST_JOB_MAX_ATTEMPTS = 3
if INGEST_JOB_MAX_ATTEMPTS < 1:
    INGEST_JOB_MAX_ATTEMPTS = 1
if INGEST_JOB_MAX_ATTEMPTS > 20:
    INGEST_JOB_MAX_ATTEMPTS = 20
try:
    # INGEST_EMBED_JOB_MAX_ATTEMPTS: Max attempts for embedding jobs before marking failed.
    INGEST_EMBED_JOB_MAX_ATTEMPTS = int(os.getenv("INGEST_EMBED_JOB_MAX_ATTEMPTS", "5"))
except (TypeError, ValueError):
    INGEST_EMBED_JOB_MAX_ATTEMPTS = 5
if INGEST_EMBED_JOB_MAX_ATTEMPTS < 1:
    INGEST_EMBED_JOB_MAX_ATTEMPTS = 1
if INGEST_EMBED_JOB_MAX_ATTEMPTS > 50:
    INGEST_EMBED_JOB_MAX_ATTEMPTS = 50
try:
    # INGEST_JOB_LEASE_SECONDS: Lease/lock time (seconds) before an ingest job is considered stale.
    INGEST_JOB_LEASE_SECONDS = int(os.getenv("INGEST_JOB_LEASE_SECONDS", "3600"))
except (TypeError, ValueError):
    INGEST_JOB_LEASE_SECONDS = 3600
if INGEST_JOB_LEASE_SECONDS < 60:
    INGEST_JOB_LEASE_SECONDS = 60
if INGEST_JOB_LEASE_SECONDS > 86_400:
    INGEST_JOB_LEASE_SECONDS = 86_400
# INGEST_JOB_REQUEUE_STALE_ENABLED: Requeue stale/abandoned ingestion jobs automatically.
INGEST_JOB_REQUEUE_STALE_ENABLED = os.getenv("INGEST_JOB_REQUEUE_STALE_ENABLED", "true").lower() in {"1", "true", "yes"}
try:
    # INGEST_JOB_RETRY_BASE_SECONDS: Base backoff (seconds) between ingestion job retries.
    INGEST_JOB_RETRY_BASE_SECONDS = float(os.getenv("INGEST_JOB_RETRY_BASE_SECONDS", "5.0"))
except (TypeError, ValueError):
    INGEST_JOB_RETRY_BASE_SECONDS = 5.0
if INGEST_JOB_RETRY_BASE_SECONDS < 0.1:
    INGEST_JOB_RETRY_BASE_SECONDS = 0.1
try:
    # INGEST_EMBED_JOB_RETRY_BASE_SECONDS: Base backoff (seconds) between embedding job retries.
    INGEST_EMBED_JOB_RETRY_BASE_SECONDS = float(os.getenv("INGEST_EMBED_JOB_RETRY_BASE_SECONDS", str(INGEST_JOB_RETRY_BASE_SECONDS)))
except (TypeError, ValueError):
    INGEST_EMBED_JOB_RETRY_BASE_SECONDS = float(INGEST_JOB_RETRY_BASE_SECONDS)
if INGEST_EMBED_JOB_RETRY_BASE_SECONDS < 0.1:
    INGEST_EMBED_JOB_RETRY_BASE_SECONDS = 0.1
try:
    # INGEST_JOB_RETRY_MAX_SECONDS: Max backoff cap (seconds) for ingestion retries.
    INGEST_JOB_RETRY_MAX_SECONDS = float(os.getenv("INGEST_JOB_RETRY_MAX_SECONDS", "300.0"))
except (TypeError, ValueError):
    INGEST_JOB_RETRY_MAX_SECONDS = 300.0
if INGEST_JOB_RETRY_MAX_SECONDS < 1.0:
    INGEST_JOB_RETRY_MAX_SECONDS = 1.0
if INGEST_JOB_RETRY_MAX_SECONDS > 86_400.0:
    INGEST_JOB_RETRY_MAX_SECONDS = 86_400.0
try:
    # INGEST_JOB_RETRY_JITTER_SECONDS: Random jitter (seconds) added to retry delays.
    INGEST_JOB_RETRY_JITTER_SECONDS = float(os.getenv("INGEST_JOB_RETRY_JITTER_SECONDS", "2.0"))
except (TypeError, ValueError):
    INGEST_JOB_RETRY_JITTER_SECONDS = 2.0
if INGEST_JOB_RETRY_JITTER_SECONDS < 0.0:
    INGEST_JOB_RETRY_JITTER_SECONDS = 0.0
if INGEST_JOB_RETRY_JITTER_SECONDS > 120.0:
    INGEST_JOB_RETRY_JITTER_SECONDS = 120.0
try:
    # RAG_EVAL_TOP_K: Top-K considered in offline evaluation metrics.
    RAG_EVAL_TOP_K = int(os.getenv("RAG_EVAL_TOP_K", "3"))
except (TypeError, ValueError):
    RAG_EVAL_TOP_K = 3
if RAG_EVAL_TOP_K < 1:
    RAG_EVAL_TOP_K = 1
if RAG_EVAL_TOP_K > 20:
    RAG_EVAL_TOP_K = 20
RAG_EVAL_THRESHOLDS = {
    "minimums": {
        # RAG_EVAL_IDENTIFIER_TOP1: Minimum acceptable top-1 accuracy for identifier queries.
        "identifier_top1": float(os.getenv("RAG_EVAL_IDENTIFIER_TOP1", "0.9")),
        # RAG_EVAL_NOT_FOUND_ACC: Minimum acceptable accuracy for "not found" responses.
        "not_found_accuracy": float(os.getenv("RAG_EVAL_NOT_FOUND_ACC", "0.95")),
        # RAG_EVAL_MRR: Minimum acceptable mean reciprocal rank.
        "mrr": float(os.getenv("RAG_EVAL_MRR", "0.92")),
        # RAG_EVAL_SOURCE_ACC: Minimum acceptable source/citation accuracy.
        "source_accuracy": float(os.getenv("RAG_EVAL_SOURCE_ACC", "0.97")),
        # RAG_EVAL_BEHAVIOR_ACC: Minimum acceptable behavior/policy adherence score.
        "behavior_accuracy": float(os.getenv("RAG_EVAL_BEHAVIOR_ACC", "0.9")),
    },
    "maximums": {
        # RAG_EVAL_VECTOR_P95_MS: Maximum acceptable p95 vector search duration (ms).
        "vector.p95": float(os.getenv("RAG_EVAL_VECTOR_P95_MS", "350")),
    },
}
try:
    # MCP_LOAD_TEST_P95_MAX_MS: Load test threshold for p95 turn latency (ms).
    MCP_LOAD_TEST_P95_MAX_MS = int(os.getenv("MCP_LOAD_TEST_P95_MAX_MS", "1500"))
except (TypeError, ValueError):
    MCP_LOAD_TEST_P95_MAX_MS = 1500
MCP_LOAD_TEST_P95_MAX_MS = max(1, min(120_000, MCP_LOAD_TEST_P95_MAX_MS))
try:
    # MCP_LOAD_TEST_MAX_ERROR_RATE: Load test threshold for error rate (0..1).
    MCP_LOAD_TEST_MAX_ERROR_RATE = float(os.getenv("MCP_LOAD_TEST_MAX_ERROR_RATE", "0.02"))
except (TypeError, ValueError):
    MCP_LOAD_TEST_MAX_ERROR_RATE = 0.02
MCP_LOAD_TEST_MAX_ERROR_RATE = max(0.0, min(1.0, MCP_LOAD_TEST_MAX_ERROR_RATE))
try:
    # MCP_LOAD_TEST_MAX_THROTTLED_RATE: Load test threshold for throttled/limited rate (0..1).
    MCP_LOAD_TEST_MAX_THROTTLED_RATE = float(os.getenv("MCP_LOAD_TEST_MAX_THROTTLED_RATE", "0.02"))
except (TypeError, ValueError):
    MCP_LOAD_TEST_MAX_THROTTLED_RATE = 0.02
MCP_LOAD_TEST_MAX_THROTTLED_RATE = max(0.0, min(1.0, MCP_LOAD_TEST_MAX_THROTTLED_RATE))
# RAG_DRIFT_TRUNCATION_THRESHOLD: Drift alert threshold for truncation rate.
RAG_DRIFT_TRUNCATION_THRESHOLD = float(os.getenv("RAG_DRIFT_TRUNCATION_THRESHOLD", "0.2"))
# RAG_DRIFT_ALIAS_HIT_THRESHOLD: Drift alert threshold for alias-hit rate (lower means worse).
RAG_DRIFT_ALIAS_HIT_THRESHOLD = float(os.getenv("RAG_DRIFT_ALIAS_HIT_THRESHOLD", "0.85"))
# RAG_DRIFT_NOT_FOUND_THRESHOLD: Drift alert threshold for not-found rate (higher means worse).
RAG_DRIFT_NOT_FOUND_THRESHOLD = float(os.getenv("RAG_DRIFT_NOT_FOUND_THRESHOLD", "0.3"))
# Enable the portal streaming state machine by default so progressive spinner phases
# ("Searching knowledge…", "Exploring deeper insights…", etc.) are surfaced unless
# an environment override disables it.
# PORTAL_STREAM_STATE_MACHINE: Enable/disable the portal streaming state machine.
PORTAL_STREAM_STATE_MACHINE = os.getenv("PORTAL_STREAM_STATE_MACHINE", "true").lower() in {"1", "true", "yes"}
try:
    # PORTAL_SPINNER_PHASE_INTERVAL: Seconds between spinner/status phase changes in the portal UI.
    PORTAL_SPINNER_PHASE_INTERVAL = float(os.getenv("PORTAL_SPINNER_PHASE_INTERVAL", "5.5"))
except (TypeError, ValueError):
    PORTAL_SPINNER_PHASE_INTERVAL = 5.5
if PORTAL_SPINNER_PHASE_INTERVAL < 0:
    PORTAL_SPINNER_PHASE_INTERVAL = 0.0
# PORTAL_ASSET_VERSION: Static asset cache-buster (useful for CDN/browser caching).
PORTAL_ASSET_VERSION = os.getenv("PORTAL_ASSET_VERSION")
if not PORTAL_ASSET_VERSION:
    PORTAL_ASSET_VERSION = str(int(time.time()))

# PORTAL_DISABLE_PLANNER: Disable the portal postflight (planner) pass entirely.
PORTAL_DISABLE_PLANNER = os.getenv("PORTAL_DISABLE_PLANNER", "false").lower() in {"1", "true", "yes"}
# PORTAL_FORCE_PLANNER: Force-enable the portal postflight pass (overrides auto-skips).
PORTAL_FORCE_PLANNER = os.getenv("PORTAL_FORCE_PLANNER", "false").lower() in {"1", "true", "yes"}

# PORTAL_DEBUG_TOOL_TRACE: When enabled, include a bounded, sanitized tool trace +
# search/read payload in portal streaming responses (rendered in the UI under each
# assistant message). Enabled by default; disable via env if needed.
PORTAL_DEBUG_TOOL_TRACE = os.getenv("PORTAL_DEBUG_TOOL_TRACE", "true").lower() in {"1", "true", "yes"}

# PORTAL_ALLOW_MCP_TOOL_PREFERENCES: Allow the portal to persist per-agent "Always allow this tool" preferences.
PORTAL_ALLOW_MCP_TOOL_PREFERENCES = os.getenv("PORTAL_ALLOW_MCP_TOOL_PREFERENCES", "false").lower() in {"1", "true", "yes"}

# ---------------------------------------------------------------------------
# Portal file uploads + artifacts

# PORTAL_FILE_UPLOAD_MAX_BYTES: Maximum upload size for portal attachments (bytes).
PORTAL_FILE_UPLOAD_MAX_BYTES = int(os.getenv("PORTAL_FILE_UPLOAD_MAX_BYTES", str(25 * 1024 * 1024)))
# PORTAL_PDF_MAX_PAGES: Hard cap on PDF pages for portal uploads/artifacts.
PORTAL_PDF_MAX_PAGES = int(os.getenv("PORTAL_PDF_MAX_PAGES", "250"))
# PORTAL_FILE_DOWNLOAD_TTL_SECONDS: Signed download link TTL for portal files.
PORTAL_FILE_DOWNLOAD_TTL_SECONDS = int(os.getenv("PORTAL_FILE_DOWNLOAD_TTL_SECONDS", "3600"))

# Retrieval indexing caps (keep deterministic + bounded).
PORTAL_FILE_MAX_CHUNKS = int(os.getenv("PORTAL_FILE_MAX_CHUNKS", "200"))
PORTAL_FILE_CHUNK_CHARS = int(os.getenv("PORTAL_FILE_CHUNK_CHARS", "1200"))
PORTAL_FILE_CHUNK_OVERLAP_CHARS = int(os.getenv("PORTAL_FILE_CHUNK_OVERLAP_CHARS", "160"))

# ==============================================================================
# SECURITY MIDDLEWARE & HEADERS
# ==============================================================================

# SECURE_SSL_REDIRECT: Force HTTP->HTTPS redirects (enable only after SSL is verified).
SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "false").lower() in {"1", "true", "yes"}

# SECURE_HSTS_SECONDS: HTTP Strict Transport Security (seconds; 0 disables).
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_HSTS_PRELOAD = SECURE_HSTS_SECONDS > 0

# SESSION_COOKIE_SECURE: Mark session cookies as secure (HTTPS only).
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}
# CSRF_COOKIE_SECURE: Mark CSRF cookies as secure (HTTPS only).
CSRF_COOKIE_SECURE = os.getenv("CSRF_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}

# Prevent clickjacking
X_FRAME_OPTIONS = 'DENY'

# Content type sniffing protection
SECURE_CONTENT_TYPE_NOSNIFF = True

# XSS protection (browsers)
SECURE_BROWSER_XSS_FILTER = True

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "pocketai.middleware.FrontendAuthBoundaryMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "pocketai.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "frontend" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "frontend.context_processors.site_globals",
            ],
        },
    },
]

WSGI_APPLICATION = "pocketai.wsgi.application"
ASGI_APPLICATION = "pocketai.asgi.application"

# ==============================================================================
# DATABASE (Environment-based)
# ==============================================================================

# DATABASE_URL: Optional full database DSN (e.g., Railway/Render/Heroku style).
_database_url = os.getenv("DATABASE_URL")

if _database_url:
    # Parse DATABASE_URL (format: postgresql://user:pass@host:port/dbname)
    import re
    match = re.match(
        r"postgresql://(?P<user>[^:]+):(?P<password>[^@]+)@(?P<host>[^:]+):(?P<port>\d+)/(?P<name>.+)",
        _database_url
    )
    if match:
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': match.group('name'),
                'USER': match.group('user'),
                'PASSWORD': match.group('password'),
                'HOST': match.group('host'),
                'PORT': match.group('port'),
            }
        }
    else:
        # Fallback to dj-database-url if available
        try:
            import dj_database_url
            DATABASES = {'default': dj_database_url.parse(_database_url)}
        except ImportError:
            raise ValueError(
                "Invalid DATABASE_URL format. Install dj-database-url or use individual POSTGRES_* variables."
            )
else:
    # Option 2: Individual environment variables
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            # POSTGRES_DB: Postgres database name.
            'NAME': os.getenv('POSTGRES_DB', 'djangopocket'),
            # POSTGRES_USER: Postgres username.
            'USER': os.getenv('POSTGRES_USER', 'djangopocket'),
            # POSTGRES_PASSWORD: Postgres password (must be set in production).
            'PASSWORD': os.getenv('POSTGRES_PASSWORD', 'adham123'),  # Default for local dev only
            # POSTGRES_HOST: Postgres hostname.
            'HOST': os.getenv('POSTGRES_HOST', 'localhost'),
            # POSTGRES_PORT: Postgres port.
            'PORT': os.getenv('POSTGRES_PORT', '5432'),
            # DB_CONN_MAX_AGE: Connection pooling max age (seconds; 0 disables persistent connections).
            'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', '0')),  # Connection pooling
        }
    }

# Warn if using default password in production
if DATABASES['default']['PASSWORD'] == 'adham123' and _is_production():
    import warnings
    warnings.warn(
        "Using default database password in production! Set POSTGRES_PASSWORD environment variable.",
        RuntimeWarning,
        stacklevel=2,
    )

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "frontend" / "static"]
STATIC_ROOT = BASE_DIR / "var" / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "var" / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/"

LOG_DIR = BASE_DIR / "var" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
DEEPSEEK_LOG_FILE = LOG_DIR / "deepseek_calls.log"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        },
        "http_request": {
            "()": "django.utils.log.ServerFormatter",
            "format": "🌐 {server_time} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
        "console_http": {
            "class": "logging.StreamHandler",
            "formatter": "http_request",
        },
        "rag_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(BASE_DIR / "var" / "logs" / "rag.log"),
            "maxBytes": 1024 * 1024,
            "backupCount": 3,
            "formatter": "verbose",
        },
        "deepseek_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(DEEPSEEK_LOG_FILE),
            "maxBytes": 1024 * 1024,
            "backupCount": 3,
            "formatter": "verbose",
        },
    },
    "loggers": {
        "django.server": {"handlers": ["console_http"], "level": "INFO", "propagate": False},
        "apps.llm.llm_provider": {"handlers": ["console", "deepseek_file"], "level": "INFO", "propagate": False},
        "apps.knowledge.knowledge_ingestion": {"handlers": ["console", "rag_file"], "level": "INFO", "propagate": False},
        "apps.rag.ai_orchestrator": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "apps.mcp.tools": {"handlers": ["console", "rag_file"], "level": "INFO", "propagate": False},
        "apps.mcp.orchestrator": {"handlers": ["console", "rag_file"], "level": "INFO", "propagate": False},
        "apps.api.chat_portal": {"handlers": ["console", "rag_file"], "level": "INFO", "propagate": False},
    },
}
