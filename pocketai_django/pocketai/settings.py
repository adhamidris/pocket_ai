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
        os.getenv("DJANGO_ENV") == "production",
        os.getenv("RAILWAY_ENVIRONMENT") == "production",
        os.getenv("RENDER") is not None,
        os.getenv("FLY_APP_NAME") is not None,
        os.getenv("DJANGO_DEBUG", "").lower() == "false",
    ])


# DEBUG: Default to False (safe), allow override for development
DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() in {"1", "true", "yes"}

if DEBUG and _is_production():
    import warnings
    warnings.warn(
        "DEBUG=True detected in production environment! This is a CRITICAL security risk.",
        RuntimeWarning,
        stacklevel=2,
    )

# SECRET_KEY: Load from environment or use insecure default (with warning)
_default_secret_key = "django-insecure-change-me"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", _default_secret_key)

if SECRET_KEY == _default_secret_key:
    import sys
    warning_msg = (
        "\n"
        "⚠️  WARNING: Using insecure default SECRET_KEY!\n"
        "   This is DANGEROUS in production. Generate a secure key:\n"
        "   python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'\n"
        "   Then set DJANGO_SECRET_KEY environment variable.\n"
    )
    if _is_production():
        # In production, this is a critical error
        print(warning_msg, file=sys.stderr)
        raise RuntimeError("Cannot start in production with default SECRET_KEY")
    else:
        # In development, just warn
        print(warning_msg, file=sys.stderr)

# ALLOWED_HOSTS: Load from environment (comma-separated)
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
    _sentry_dsn = os.getenv("SENTRY_DSN", "").strip()
    if _sentry_dsn:
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
            environment=os.getenv('SENTRY_ENVIRONMENT', 'production'),
            release=os.getenv('SENTRY_RELEASE', 'unknown'),
            
            # Performance Monitoring
            traces_sample_rate=float(os.getenv('SENTRY_TRACES_SAMPLE_RATE', '0.1')),  # 10% of requests
            profiles_sample_rate=float(os.getenv('SENTRY_PROFILES_SAMPLE_RATE', '0.1')),  # 10% of traces
            
            # Privacy: Don't send PII (critical for multi-tenant SaaS)
            send_default_pii=False,
            
            # Ignore common noise
            ignore_errors=[
                'SuspiciousOperation',
                'PermissionDenied',
            ],
        )
        print(f"✅ Sentry initialized for environment: {os.getenv('SENTRY_ENVIRONMENT', 'production')}")



def _split_scopes(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [scope.strip() for scope in raw.split() if scope.strip()]


def _integration_credentials_key() -> str:
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


GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
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
GOOGLE_OAUTH_SCOPES = _split_scopes(os.getenv("GOOGLE_OAUTH_SCOPES")) or _default_google_scopes
INTEGRATIONS_DASHBOARD_URL = os.getenv(
    "INTEGRATIONS_DASHBOARD_URL",
    "/dashboard/knowledge?panel=integrations",
)
INTEGRATION_CREDENTIALS_KEY = _integration_credentials_key()
INTEGRATION_CREDENTIAL_ROTATION_DAYS = int(os.getenv("INTEGRATION_CREDENTIAL_ROTATION_DAYS", "30"))
INTEGRATION_CREDENTIAL_MAX_ERRORS = int(os.getenv("INTEGRATION_CREDENTIAL_MAX_ERRORS", "3"))
INGEST_NORMALIZE_TABLES = os.getenv("INGEST_NORMALIZE_TABLES", "true").lower() in {"1", "true", "yes"}
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

EMBED_PROVIDER = os.getenv("EMBED_PROVIDER", "local")
# Default to a multilingual FastEmbed model so Arabic/mixed-language tenants work out of the box.
# Keep `EMBED_DIM=384` unless you intentionally migrate the `VectorField` dimension in Postgres.
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
EMBED_DIM = int(os.getenv("EMBED_DIM", "384"))
EMBED_DISTANCE = os.getenv("EMBED_DISTANCE", "cosine")  # 'cosine'|'l2'|'ip'
INGEST_MAX_JSON_ENTITIES_DEFAULT = int(os.getenv("INGEST_MAX_JSON_ENTITIES_DEFAULT", "1000"))
INGEST_MAX_JSON_ENTITY_CANDIDATES = int(os.getenv("INGEST_MAX_JSON_ENTITY_CANDIDATES", "4000"))
INGEST_ALIAS_WARNING_THRESHOLD = int(os.getenv("INGEST_ALIAS_WARNING_THRESHOLD", "2000"))
RAG_MAX_SNIPPETS_PER_SEARCH = int(os.getenv("RAG_MAX_SNIPPETS_PER_SEARCH", "3"))
RAG_ALIAS_MAX_CHUNKS_PER_UPLOAD = int(os.getenv("RAG_ALIAS_MAX_CHUNKS_PER_UPLOAD", "2"))
RAG_ANN_MAX_CHUNKS_PER_UPLOAD = int(os.getenv("RAG_ANN_MAX_CHUNKS_PER_UPLOAD", "3"))
RAG_SEARCH_PREVIEW_CHAR_LIMIT = int(os.getenv("RAG_SEARCH_PREVIEW_CHAR_LIMIT", "800"))
RAG_FTS_ENABLED = os.getenv("RAG_FTS_ENABLED", "true").lower() in {"1", "true", "yes"}
try:
    RAG_DB_STATEMENT_TIMEOUT_MS = int(os.getenv("RAG_DB_STATEMENT_TIMEOUT_MS", "15000"))
except (TypeError, ValueError):
    RAG_DB_STATEMENT_TIMEOUT_MS = 15000
if RAG_DB_STATEMENT_TIMEOUT_MS < 0:
    RAG_DB_STATEMENT_TIMEOUT_MS = 0
try:
    RAG_DB_LOCK_TIMEOUT_MS = int(os.getenv("RAG_DB_LOCK_TIMEOUT_MS", "2000"))
except (TypeError, ValueError):
    RAG_DB_LOCK_TIMEOUT_MS = 2000
if RAG_DB_LOCK_TIMEOUT_MS < 0:
    RAG_DB_LOCK_TIMEOUT_MS = 0
RAG_RERANK_POOL = int(os.getenv("RAG_RERANK_POOL", "60"))
RAG_RERANK_BUDGET_MS = int(os.getenv("RAG_RERANK_BUDGET_MS", "0"))
RAG_SNIPPET_RERANK_BUDGET_MS = int(os.getenv("RAG_SNIPPET_RERANK_BUDGET_MS", "0"))
RAG_MMR_LAMBDA = float(os.getenv("RAG_MMR_LAMBDA", "0.7"))
RAG_VECTOR_DISTANCE_CEILING = float(os.getenv("RAG_VECTOR_DISTANCE_CEILING", "0.5"))
RAG_WEIGHT_VECTOR = float(os.getenv("RAG_WEIGHT_VECTOR", "1.0"))
RAG_WEIGHT_LEXICAL = float(os.getenv("RAG_WEIGHT_LEXICAL", "0.8"))
RAG_WEIGHT_ALIAS = float(os.getenv("RAG_WEIGHT_ALIAS", "1.2"))
RAG_WEIGHT_ENTITY = float(os.getenv("RAG_WEIGHT_ENTITY", "0.4"))
RAG_WEIGHT_RECENCY = float(os.getenv("RAG_WEIGHT_RECENCY", "0.25"))
RAG_LEXICAL_THRESHOLD_SHORT = float(os.getenv("RAG_LEXICAL_THRESHOLD_SHORT", "0.25"))
RAG_LEXICAL_THRESHOLD_MEDIUM = float(os.getenv("RAG_LEXICAL_THRESHOLD_MEDIUM", "0.2"))
RAG_LEXICAL_THRESHOLD_LONG = float(os.getenv("RAG_LEXICAL_THRESHOLD_LONG", "0.15"))
RAG_IVFFLAT_PROBES = int(os.getenv("RAG_IVFFLAT_PROBES", "8"))
RAG_QUERY_VECTOR_CACHE_MAX_BYTES = int(os.getenv("RAG_QUERY_VECTOR_CACHE_MAX_BYTES", "16384"))
RAG_NEIGHBOR_WINDOW_CACHE_SIZE = int(os.getenv("RAG_NEIGHBOR_WINDOW_CACHE_SIZE", "128"))
RAG_BUSINESS_OVERRIDE_KEY = os.getenv("RAG_BUSINESS_OVERRIDE_KEY", "rag_overrides")
RAG_TABLE_RESULT_LIMIT = int(os.getenv("RAG_TABLE_RESULT_LIMIT", "3"))
RAG_TABLE_HEADER_MATCH_BONUS = float(os.getenv("RAG_TABLE_HEADER_MATCH_BONUS", "0.12"))
RAG_TABLE_SPECIFIC_MISS_PENALTY = float(os.getenv("RAG_TABLE_SPECIFIC_MISS_PENALTY", "0.25"))
RAG_TABLE_SPECIFIC_MIN_LENGTH = int(os.getenv("RAG_TABLE_SPECIFIC_MIN_LENGTH", "4"))
RAG_TABLE_HEADER_TOKEN_CACHE = int(os.getenv("RAG_TABLE_HEADER_TOKEN_CACHE", "256"))
RAG_TABLE_GENERIC_TOKEN_DF = float(os.getenv("RAG_TABLE_GENERIC_TOKEN_DF", "0.35"))
RAG_TABLE_GENERIC_TOKEN_TOPK = int(os.getenv("RAG_TABLE_GENERIC_TOKEN_TOPK", "40"))
RAG_TABLE_GENERIC_MIN_TABLES = int(os.getenv("RAG_TABLE_GENERIC_MIN_TABLES", "2"))
RAG_TABLE_DOMINANT_MIN_TABLES = int(os.getenv("RAG_TABLE_DOMINANT_MIN_TABLES", "2"))
RAG_TABLE_DOMINANT_UPLOAD_RATIO = float(os.getenv("RAG_TABLE_DOMINANT_UPLOAD_RATIO", "0.35"))
RAG_TABLE_ROW_LABEL_SAMPLE_LIMIT = int(os.getenv("RAG_TABLE_ROW_LABEL_SAMPLE_LIMIT", "200"))
RAG_TABLE_CONTEXT_CACHE_SIZE = int(os.getenv("RAG_TABLE_CONTEXT_CACHE_SIZE", "128"))
RAG_PDFPLUMBER_ENABLED = os.getenv("RAG_PDFPLUMBER_ENABLED", "true").lower() in {"1", "true", "yes"}
RAG_PDF_TABLE_EXTRACTOR = os.getenv("RAG_PDF_TABLE_EXTRACTOR", "auto").strip().lower() or "auto"
_raw_pdfplumber_settings = os.getenv("RAG_PDFPLUMBER_TABLE_SETTINGS", "").strip()
if _raw_pdfplumber_settings:
    try:
        RAG_PDFPLUMBER_TABLE_SETTINGS = json.loads(_raw_pdfplumber_settings)
    except json.JSONDecodeError:
        RAG_PDFPLUMBER_TABLE_SETTINGS = None
else:
    RAG_PDFPLUMBER_TABLE_SETTINGS = None

RAG_AZURE_DI_ENABLED = os.getenv("RAG_AZURE_DI_ENABLED", "true").lower() in {"1", "true", "yes"}
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
AZURE_DOCUMENT_INTELLIGENCE_KEY = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")
AZURE_DOCUMENT_INTELLIGENCE_MODEL = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_MODEL", "prebuilt-layout")
AZURE_DOCUMENT_INTELLIGENCE_API_VERSION = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_API_VERSION", "2023-07-31")
AZURE_DOCUMENT_INTELLIGENCE_BASE_PATH = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_BASE_PATH", "formrecognizer")
AZURE_DOCUMENT_INTELLIGENCE_LOCALE = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_LOCALE", "")
RAG_AZURE_DI_TIMEOUT_SECONDS = float(os.getenv("RAG_AZURE_DI_TIMEOUT_SECONDS", "60"))
RAG_AZURE_DI_POLL_INTERVAL_SECONDS = float(os.getenv("RAG_AZURE_DI_POLL_INTERVAL_SECONDS", "1.5"))
RAG_AZURE_DI_MAX_POLLS = int(os.getenv("RAG_AZURE_DI_MAX_POLLS", "40"))

RAG_TABLE_VLM_ENABLED = os.getenv("RAG_TABLE_VLM_ENABLED", "true").lower() in {"1", "true", "yes"}
RAG_TABLE_VLM_MODEL = os.getenv("RAG_TABLE_VLM_MODEL", "gpt-4o")
RAG_TABLE_VLM_CONFIDENCE_THRESHOLD = float(os.getenv("RAG_TABLE_VLM_CONFIDENCE_THRESHOLD", "0.6"))
RAG_TABLE_VLM_MAX_REPAIRS_PER_UPLOAD = int(os.getenv("RAG_TABLE_VLM_MAX_REPAIRS_PER_UPLOAD", "3"))

RAG_TABLE_SCHEMA_CHUNKING = os.getenv("RAG_TABLE_SCHEMA_CHUNKING", "true").lower() in {"1", "true", "yes"}
RAG_TABLE_PARENT_MAX_ROWS = int(os.getenv("RAG_TABLE_PARENT_MAX_ROWS", "200"))
RAG_TABLE_PARENT_MAX_CHARS = int(os.getenv("RAG_TABLE_PARENT_MAX_CHARS", "16000"))
RAG_TABLE_CHILD_MAX_ROWS = int(os.getenv("RAG_TABLE_CHILD_MAX_ROWS", "500"))
RAG_TABLE_HEADER_PROPAGATION_ENABLED = os.getenv("RAG_TABLE_HEADER_PROPAGATION_ENABLED", "true").lower() in {"1", "true", "yes"}
RAG_TABLE_HEADER_PROPAGATION_MIN_OVERLAP = float(os.getenv("RAG_TABLE_HEADER_PROPAGATION_MIN_OVERLAP", "0.45"))
RAG_TABLE_DEDUPE_ENABLED = os.getenv("RAG_TABLE_DEDUPE_ENABLED", "true").lower() in {"1", "true", "yes"}
RAG_TABLE_DEDUPE_MIN_OVERLAP = float(os.getenv("RAG_TABLE_DEDUPE_MIN_OVERLAP", "0.6"))
RAG_TABLE_POSTPROCESS_ROW_LIMIT = int(os.getenv("RAG_TABLE_POSTPROCESS_ROW_LIMIT", "40"))

RAG_OCR_NORMALIZATION_ENABLED = os.getenv("RAG_OCR_NORMALIZATION_ENABLED", "true").lower() in {"1", "true", "yes"}
RAG_OCR_NORMALIZATION_REPLACEMENTS = os.getenv("RAG_OCR_NORMALIZATION_REPLACEMENTS")
RAG_OCR_PERCENT_FIX_ENABLED = os.getenv("RAG_OCR_PERCENT_FIX_ENABLED", "true").lower() in {"1", "true", "yes"}
RAG_OCR_PERCENT_SPACE_FIX_ENABLED = os.getenv("RAG_OCR_PERCENT_SPACE_FIX_ENABLED", "true").lower() in {"1", "true", "yes"}
RAG_OCR_PERCENT_SANITY_MAX = float(os.getenv("RAG_OCR_PERCENT_SANITY_MAX", "100"))
RAG_OCR_CURRENCY_SPACING_ENABLED = os.getenv("RAG_OCR_CURRENCY_SPACING_ENABLED", "true").lower() in {"1", "true", "yes"}
# Default to MCP orchestrator for new deployments; can be disabled per-env.
RAG_USE_MCP_ORCHESTRATOR = os.getenv("RAG_USE_MCP_ORCHESTRATOR", "true").lower() in {"1", "true", "yes"}
# Retrieval should be predictable by default: one strong query per user turn.
# Fanout variants can be enabled explicitly via env.
MCP_SEARCH_MAX_QUERY_VARIANTS = int(os.getenv("MCP_SEARCH_MAX_QUERY_VARIANTS", "1"))
MCP_SEARCH_FANOUT_BUDGET_MS = int(os.getenv("MCP_SEARCH_FANOUT_BUDGET_MS", "0"))
MCP_SEARCH_FANOUT_RRF_K = int(os.getenv("MCP_SEARCH_FANOUT_RRF_K", "60"))
MCP_SEARCH_FANOUT_PARALLEL = os.getenv("MCP_SEARCH_FANOUT_PARALLEL", "false").lower() in {"1", "true", "yes"}
try:
    MCP_SEARCH_FANOUT_PARALLEL_MAX_WORKERS = int(os.getenv("MCP_SEARCH_FANOUT_PARALLEL_MAX_WORKERS", "4"))
except (TypeError, ValueError):
    MCP_SEARCH_FANOUT_PARALLEL_MAX_WORKERS = 4
MCP_SEARCH_FANOUT_PARALLEL_MAX_WORKERS = max(1, min(8, MCP_SEARCH_FANOUT_PARALLEL_MAX_WORKERS))
# MCP prompt-safe tool output limits (evidence packets sent back to the LLM).
MCP_PROMPT_MAX_SNIPPETS = int(os.getenv("MCP_PROMPT_MAX_SNIPPETS", "4"))
MCP_PROMPT_SNIPPET_CONTENT_CHARS = int(os.getenv("MCP_PROMPT_SNIPPET_CONTENT_CHARS", "1200"))
MCP_PROMPT_TABLE_MAX_ROWS = int(os.getenv("MCP_PROMPT_TABLE_MAX_ROWS", "12"))
MCP_PROMPT_TABLE_MAX_CONTRIBUTIONS = int(os.getenv("MCP_PROMPT_TABLE_MAX_CONTRIBUTIONS", "25"))
MCP_PROMPT_TABLE_MAX_CELLS = int(os.getenv("MCP_PROMPT_TABLE_MAX_CELLS", "12"))
MCP_PROMPT_TABLE_MAX_CELLS_EXACT = int(os.getenv("MCP_PROMPT_TABLE_MAX_CELLS_EXACT", "60"))
# Logging privacy toggles (default: safe/no PII in logs).
MCP_LOG_PII = os.getenv("MCP_LOG_PII", "false").lower() in {"1", "true", "yes"}
MCP_LOG_SNIPPET_PREVIEWS = os.getenv("MCP_LOG_SNIPPET_PREVIEWS", "false").lower() in {"1", "true", "yes"}
MCP_LOG_FULL_SNIPPET_CONTENT = os.getenv("MCP_LOG_FULL_SNIPPET_CONTENT", "false").lower() in {"1", "true", "yes"}
# Tabular prompt safety: apply per-upload column privacy + PII masking before tool
# results are stored/re-injected into prompts.
MCP_TABULAR_PRIVACY_ENABLED = os.getenv("MCP_TABULAR_PRIVACY_ENABLED", "true").lower() in {"1", "true", "yes"}
MCP_TABULAR_PII_REDACTION_ENABLED = os.getenv("MCP_TABULAR_PII_REDACTION_ENABLED", "true").lower() in {"1", "true", "yes"}
# Verified lookup mode: require a verified conversation context before returning
# tabular PII fields (addresses/phones/emails/etc) from tools like read_knowledge.
MCP_VERIFIED_LOOKUP_ENABLED = os.getenv("MCP_VERIFIED_LOOKUP_ENABLED", "true").lower() in {"1", "true", "yes"}
MCP_VERIFIED_LOOKUP_REQUIRE_FOR_PII = os.getenv("MCP_VERIFIED_LOOKUP_REQUIRE_FOR_PII", "true").lower() in {"1", "true", "yes"}
MCP_VERIFIED_LOOKUP_ALLOW_CUSTOMER_MATCH = os.getenv("MCP_VERIFIED_LOOKUP_ALLOW_CUSTOMER_MATCH", "true").lower() in {"1", "true", "yes"}
# MCP prompt/context governor. Defaults are conservative to avoid provider context overflows.
MCP_CONTEXT_GOVERNOR_ENABLED = os.getenv("MCP_CONTEXT_GOVERNOR_ENABLED", "true").lower() in {"1", "true", "yes"}
MCP_MAX_CONTEXT_TOKENS = int(os.getenv("MCP_MAX_CONTEXT_TOKENS", "8192"))
MCP_RESPONSE_TOKEN_RESERVE = int(os.getenv("MCP_RESPONSE_TOKEN_RESERVE", "1200"))
try:
    MCP_MAX_INPUT_TOKENS = int(
        os.getenv(
            "MCP_MAX_INPUT_TOKENS",
            str(max(1000, MCP_MAX_CONTEXT_TOKENS - MCP_RESPONSE_TOKEN_RESERVE)),
        )
    )
except (TypeError, ValueError):
    MCP_MAX_INPUT_TOKENS = max(1000, MCP_MAX_CONTEXT_TOKENS - MCP_RESPONSE_TOKEN_RESERVE)

# MCP long-chat memory (rolling summary + pinned identifiers).
MCP_LONG_CHAT_MEMORY_ENABLED = os.getenv("MCP_LONG_CHAT_MEMORY_ENABLED", "true").lower() in {"1", "true", "yes"}
try:
    MCP_MEMORY_RECENT_MESSAGES = int(os.getenv("MCP_MEMORY_RECENT_MESSAGES", "4"))
except (TypeError, ValueError):
    MCP_MEMORY_RECENT_MESSAGES = 4
try:
    MCP_MEMORY_UPDATE_AFTER_MESSAGES = int(os.getenv("MCP_MEMORY_UPDATE_AFTER_MESSAGES", "10"))
except (TypeError, ValueError):
    MCP_MEMORY_UPDATE_AFTER_MESSAGES = 10
try:
    MCP_MEMORY_SUMMARY_MAX_CHARS = int(os.getenv("MCP_MEMORY_SUMMARY_MAX_CHARS", "1600"))
except (TypeError, ValueError):
    MCP_MEMORY_SUMMARY_MAX_CHARS = 1600
try:
    MCP_MEMORY_TURN_MAX_CHARS = int(os.getenv("MCP_MEMORY_TURN_MAX_CHARS", "1200"))
except (TypeError, ValueError):
    MCP_MEMORY_TURN_MAX_CHARS = 1200
try:
    MCP_MEMORY_PIN_MAX_ITEMS = int(os.getenv("MCP_MEMORY_PIN_MAX_ITEMS", "6"))
except (TypeError, ValueError):
    MCP_MEMORY_PIN_MAX_ITEMS = 6
try:
    MCP_MEMORY_PIN_VALUE_CHARS = int(os.getenv("MCP_MEMORY_PIN_VALUE_CHARS", "80"))
except (TypeError, ValueError):
    MCP_MEMORY_PIN_VALUE_CHARS = 80
RAG_TABLE_SIMILARITY_THRESHOLD = float(os.getenv("RAG_TABLE_SIMILARITY_THRESHOLD", "0.3"))
RAG_TABLE_COLUMN_CACHE_SIZE = int(os.getenv("RAG_TABLE_COLUMN_CACHE_SIZE", "32"))
RAG_TABLE_COLUMN_SAMPLE = int(os.getenv("RAG_TABLE_COLUMN_SAMPLE", "200"))
RAG_TABLE_SMALL_ROW_LIMIT = int(os.getenv("RAG_TABLE_SMALL_ROW_LIMIT", "2000"))
RAG_TABLE_LARGE_ROW_LIMIT = int(os.getenv("RAG_TABLE_LARGE_ROW_LIMIT", "20000"))
RAG_TABLE_MAX_HARD_CAP = int(os.getenv("RAG_TABLE_MAX_HARD_CAP", "100000"))
RAG_ENABLE_CROSS_ENCODER = os.getenv("RAG_ENABLE_CROSS_ENCODER", "false").lower() in {"1", "true", "yes"}
RAG_CROSS_ENCODER_MODEL = os.getenv("RAG_CROSS_ENCODER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
RAG_CROSS_ENCODER_DEVICE = os.getenv("RAG_CROSS_ENCODER_DEVICE")
TABLE_MAX_ROWS_DEFAULT = int(os.getenv("TABLE_MAX_ROWS_DEFAULT", "5000"))
TABLE_MAX_COLUMNS_DEFAULT = int(os.getenv("TABLE_MAX_COLUMNS_DEFAULT", "0"))
# Dataset mode (store huge tables as files + lightweight metadata).
DATASET_MODE_ENABLED = os.getenv("DATASET_MODE_ENABLED", "true").lower() in {"1", "true", "yes"}
try:
    DATASET_MODE_ROW_THRESHOLD = int(os.getenv("DATASET_MODE_ROW_THRESHOLD", str(RAG_TABLE_LARGE_ROW_LIMIT)))
except (TypeError, ValueError):
    DATASET_MODE_ROW_THRESHOLD = int(RAG_TABLE_LARGE_ROW_LIMIT)
try:
    DATASET_MODE_PREVIEW_ROWS = int(os.getenv("DATASET_MODE_PREVIEW_ROWS", "200"))
except (TypeError, ValueError):
    DATASET_MODE_PREVIEW_ROWS = 200
try:
    DATASET_MODE_SAMPLE_ROWS = int(os.getenv("DATASET_MODE_SAMPLE_ROWS", "20"))
except (TypeError, ValueError):
    DATASET_MODE_SAMPLE_ROWS = 20
DATASET_STORAGE_FORMAT = os.getenv("DATASET_STORAGE_FORMAT", "csv_gz").strip() or "csv_gz"
DATASET_QUERY_ENGINE = (os.getenv("DATASET_QUERY_ENGINE", "duckdb") or "duckdb").strip().lower() or "duckdb"
if DATASET_QUERY_ENGINE not in {"duckdb", "python", "auto"}:
    DATASET_QUERY_ENGINE = "duckdb"
try:
    DATASET_QUERY_MAX_SECONDS = float(os.getenv("DATASET_QUERY_MAX_SECONDS", "2.5"))
except (TypeError, ValueError):
    DATASET_QUERY_MAX_SECONDS = 2.5
if DATASET_QUERY_MAX_SECONDS <= 0:
    DATASET_QUERY_MAX_SECONDS = 2.5
try:
    DATASET_QUERY_MAX_SORT_WINDOW = int(os.getenv("DATASET_QUERY_MAX_SORT_WINDOW", "500"))
except (TypeError, ValueError):
    DATASET_QUERY_MAX_SORT_WINDOW = 500
if DATASET_QUERY_MAX_SORT_WINDOW < 50:
    DATASET_QUERY_MAX_SORT_WINDOW = 50
try:
    DATASET_QUERY_DEFAULT_COLUMNS = int(os.getenv("DATASET_QUERY_DEFAULT_COLUMNS", "8"))
except (TypeError, ValueError):
    DATASET_QUERY_DEFAULT_COLUMNS = 8
if DATASET_QUERY_DEFAULT_COLUMNS < 3:
    DATASET_QUERY_DEFAULT_COLUMNS = 3
try:
    DATASET_QUERY_CELL_VALUE_CHARS = int(os.getenv("DATASET_QUERY_CELL_VALUE_CHARS", "160"))
except (TypeError, ValueError):
    DATASET_QUERY_CELL_VALUE_CHARS = 160
if DATASET_QUERY_CELL_VALUE_CHARS < 40:
    DATASET_QUERY_CELL_VALUE_CHARS = 40
try:
    DATASET_QUERY_MAX_GROUPS = int(os.getenv("DATASET_QUERY_MAX_GROUPS", "5000"))
except (TypeError, ValueError):
    DATASET_QUERY_MAX_GROUPS = 5000
if DATASET_QUERY_MAX_GROUPS < 100:
    DATASET_QUERY_MAX_GROUPS = 100
try:
    DATASET_QUERY_MAX_ROWS_RETURNED = int(os.getenv("DATASET_QUERY_MAX_ROWS_RETURNED", "50"))
except (TypeError, ValueError):
    DATASET_QUERY_MAX_ROWS_RETURNED = 50
if DATASET_QUERY_MAX_ROWS_RETURNED < 1:
    DATASET_QUERY_MAX_ROWS_RETURNED = 1
if DATASET_QUERY_MAX_ROWS_RETURNED > 50:
    DATASET_QUERY_MAX_ROWS_RETURNED = 50
try:
    DATASET_QUERY_MAX_COLUMNS_RETURNED = int(os.getenv("DATASET_QUERY_MAX_COLUMNS_RETURNED", "12"))
except (TypeError, ValueError):
    DATASET_QUERY_MAX_COLUMNS_RETURNED = 12
if DATASET_QUERY_MAX_COLUMNS_RETURNED < 3:
    DATASET_QUERY_MAX_COLUMNS_RETURNED = 3
if DATASET_QUERY_MAX_COLUMNS_RETURNED > 50:
    DATASET_QUERY_MAX_COLUMNS_RETURNED = 50
try:
    DATASET_QUERY_MAX_COLUMNS_RETURNED_EXACT = int(os.getenv("DATASET_QUERY_MAX_COLUMNS_RETURNED_EXACT", "50"))
except (TypeError, ValueError):
    DATASET_QUERY_MAX_COLUMNS_RETURNED_EXACT = 50
if DATASET_QUERY_MAX_COLUMNS_RETURNED_EXACT < DATASET_QUERY_MAX_COLUMNS_RETURNED:
    DATASET_QUERY_MAX_COLUMNS_RETURNED_EXACT = DATASET_QUERY_MAX_COLUMNS_RETURNED
if DATASET_QUERY_MAX_COLUMNS_RETURNED_EXACT > 50:
    DATASET_QUERY_MAX_COLUMNS_RETURNED_EXACT = 50

# Dataset key indexing (routing layer for many datasets).
DATASET_KEY_INDEX_ENABLED = os.getenv("DATASET_KEY_INDEX_ENABLED", "true").lower() in {"1", "true", "yes"}
DATASET_KEY_INDEX_ROUTING_ENABLED = os.getenv("DATASET_KEY_INDEX_ROUTING_ENABLED", "true").lower() in {"1", "true", "yes"}
DATASET_KEY_INDEX_ALLOW_SENSITIVE = os.getenv("DATASET_KEY_INDEX_ALLOW_SENSITIVE", "false").lower() in {"1", "true", "yes"}
try:
    DATASET_KEY_INDEX_MAX_COLUMNS = int(os.getenv("DATASET_KEY_INDEX_MAX_COLUMNS", "4"))
except (TypeError, ValueError):
    DATASET_KEY_INDEX_MAX_COLUMNS = 4
DATASET_KEY_INDEX_MAX_COLUMNS = max(0, min(20, DATASET_KEY_INDEX_MAX_COLUMNS))
try:
    DATASET_KEY_INDEX_BITS_PER_ITEM = int(os.getenv("DATASET_KEY_INDEX_BITS_PER_ITEM", "10"))
except (TypeError, ValueError):
    DATASET_KEY_INDEX_BITS_PER_ITEM = 10
DATASET_KEY_INDEX_BITS_PER_ITEM = max(4, min(24, DATASET_KEY_INDEX_BITS_PER_ITEM))
try:
    DATASET_KEY_INDEX_MAX_BYTES = int(os.getenv("DATASET_KEY_INDEX_MAX_BYTES", "2000000"))
except (TypeError, ValueError):
    DATASET_KEY_INDEX_MAX_BYTES = 2_000_000
DATASET_KEY_INDEX_MAX_BYTES = max(4096, min(25_000_000, DATASET_KEY_INDEX_MAX_BYTES))
try:
    DATASET_KEY_INDEX_SUGGESTED_MIN_SCORE = float(os.getenv("DATASET_KEY_INDEX_SUGGESTED_MIN_SCORE", "0.9"))
except (TypeError, ValueError):
    DATASET_KEY_INDEX_SUGGESTED_MIN_SCORE = 0.9
DATASET_KEY_INDEX_SUGGESTED_MIN_SCORE = max(0.0, min(10.0, DATASET_KEY_INDEX_SUGGESTED_MIN_SCORE))
try:
    DATASET_KEY_INDEX_CACHE_SIZE = int(os.getenv("DATASET_KEY_INDEX_CACHE_SIZE", "128"))
except (TypeError, ValueError):
    DATASET_KEY_INDEX_CACHE_SIZE = 128
DATASET_KEY_INDEX_CACHE_SIZE = max(8, min(2048, DATASET_KEY_INDEX_CACHE_SIZE))
try:
    DATASET_KEY_INDEX_MAX_MATCHES = int(os.getenv("DATASET_KEY_INDEX_MAX_MATCHES", "8"))
except (TypeError, ValueError):
    DATASET_KEY_INDEX_MAX_MATCHES = 8
DATASET_KEY_INDEX_MAX_MATCHES = max(1, min(50, DATASET_KEY_INDEX_MAX_MATCHES))
try:
    DATASET_KEY_INDEX_MAX_MATCHES_PER_UPLOAD = int(os.getenv("DATASET_KEY_INDEX_MAX_MATCHES_PER_UPLOAD", "8"))
except (TypeError, ValueError):
    DATASET_KEY_INDEX_MAX_MATCHES_PER_UPLOAD = 8
DATASET_KEY_INDEX_MAX_MATCHES_PER_UPLOAD = max(1, min(50, DATASET_KEY_INDEX_MAX_MATCHES_PER_UPLOAD))
try:
    TABLE_AGGREGATE_MAX_ROWS_RETURNED = int(os.getenv("TABLE_AGGREGATE_MAX_ROWS_RETURNED", "200"))
except (TypeError, ValueError):
    TABLE_AGGREGATE_MAX_ROWS_RETURNED = 200
if TABLE_AGGREGATE_MAX_ROWS_RETURNED < 1:
    TABLE_AGGREGATE_MAX_ROWS_RETURNED = 1
if TABLE_AGGREGATE_MAX_ROWS_RETURNED > 200:
    TABLE_AGGREGATE_MAX_ROWS_RETURNED = 200
try:
    TABLE_AGGREGATE_MAX_COLUMNS_RETURNED = int(os.getenv("TABLE_AGGREGATE_MAX_COLUMNS_RETURNED", "50"))
except (TypeError, ValueError):
    TABLE_AGGREGATE_MAX_COLUMNS_RETURNED = 50
if TABLE_AGGREGATE_MAX_COLUMNS_RETURNED < 3:
    TABLE_AGGREGATE_MAX_COLUMNS_RETURNED = 3
if TABLE_AGGREGATE_MAX_COLUMNS_RETURNED > 200:
    TABLE_AGGREGATE_MAX_COLUMNS_RETURNED = 200
try:
    MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS", "60"))
except (TypeError, ValueError):
    MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS = 60
if MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS < 10:
    MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS = 10
if MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS > 600:
    MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS = 600
try:
    MCP_DATASET_QUERY_CALLS_PER_MINUTE = int(os.getenv("MCP_DATASET_QUERY_CALLS_PER_MINUTE", "30"))
except (TypeError, ValueError):
    MCP_DATASET_QUERY_CALLS_PER_MINUTE = 30
if MCP_DATASET_QUERY_CALLS_PER_MINUTE < 0:
    MCP_DATASET_QUERY_CALLS_PER_MINUTE = 0
try:
    MCP_TABLE_AGGREGATE_CALLS_PER_MINUTE = int(os.getenv("MCP_TABLE_AGGREGATE_CALLS_PER_MINUTE", "60"))
except (TypeError, ValueError):
    MCP_TABLE_AGGREGATE_CALLS_PER_MINUTE = 60
if MCP_TABLE_AGGREGATE_CALLS_PER_MINUTE < 0:
    MCP_TABLE_AGGREGATE_CALLS_PER_MINUTE = 0
try:
    MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE = int(os.getenv("MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE", "120"))
except (TypeError, ValueError):
    MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE = 120
if MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE < 0:
    MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE = 0
try:
    MCP_READ_KNOWLEDGE_CALLS_PER_MINUTE = int(os.getenv("MCP_READ_KNOWLEDGE_CALLS_PER_MINUTE", "120"))
except (TypeError, ValueError):
    MCP_READ_KNOWLEDGE_CALLS_PER_MINUTE = 120
if MCP_READ_KNOWLEDGE_CALLS_PER_MINUTE < 0:
    MCP_READ_KNOWLEDGE_CALLS_PER_MINUTE = 0
try:
    MCP_LIST_TABLES_CALLS_PER_MINUTE = int(os.getenv("MCP_LIST_TABLES_CALLS_PER_MINUTE", "120"))
except (TypeError, ValueError):
    MCP_LIST_TABLES_CALLS_PER_MINUTE = 120
if MCP_LIST_TABLES_CALLS_PER_MINUTE < 0:
    MCP_LIST_TABLES_CALLS_PER_MINUTE = 0

# Disable cache-backed tool rate limits (useful for CI/load tests).
MCP_DISABLE_TOOL_RATE_LIMITS = os.getenv("MCP_DISABLE_TOOL_RATE_LIMITS", "false").lower() in {"1", "true", "yes"}

# MCP/orchestrator hard caps (cost controls).
try:
    MCP_MAX_TOOL_ITERATIONS = int(os.getenv("MCP_MAX_TOOL_ITERATIONS", "10"))
except (TypeError, ValueError):
    MCP_MAX_TOOL_ITERATIONS = 10
MCP_MAX_TOOL_ITERATIONS = max(1, min(50, MCP_MAX_TOOL_ITERATIONS))
try:
    MCP_MAX_SEARCHES_PER_TURN = int(os.getenv("MCP_MAX_SEARCHES_PER_TURN", "1"))
except (TypeError, ValueError):
    MCP_MAX_SEARCHES_PER_TURN = 1
if MCP_MAX_SEARCHES_PER_TURN < 0:
    MCP_MAX_SEARCHES_PER_TURN = 0
try:
    RAG_MAX_CHUNK_READS_PER_TURN = int(os.getenv("RAG_MAX_CHUNK_READS_PER_TURN", "3"))
except (TypeError, ValueError):
    RAG_MAX_CHUNK_READS_PER_TURN = 3
RAG_MAX_CHUNK_READS_PER_TURN = max(1, min(20, RAG_MAX_CHUNK_READS_PER_TURN))
try:
    RAG_MAX_CHUNK_PAGES_PER_TURN = int(os.getenv("RAG_MAX_CHUNK_PAGES_PER_TURN", "3"))
except (TypeError, ValueError):
    RAG_MAX_CHUNK_PAGES_PER_TURN = 3
RAG_MAX_CHUNK_PAGES_PER_TURN = max(1, min(20, RAG_MAX_CHUNK_PAGES_PER_TURN))
try:
    RAG_MAX_CHAR_BUDGET_PER_TURN = int(os.getenv("RAG_MAX_CHAR_BUDGET_PER_TURN", "48000"))
except (TypeError, ValueError):
    RAG_MAX_CHAR_BUDGET_PER_TURN = 48000
RAG_MAX_CHAR_BUDGET_PER_TURN = max(4000, min(200000, RAG_MAX_CHAR_BUDGET_PER_TURN))
try:
    RAG_MAX_CHAR_BUDGET_PER_MINUTE = int(os.getenv("RAG_MAX_CHAR_BUDGET_PER_MINUTE", "64000"))
except (TypeError, ValueError):
    RAG_MAX_CHAR_BUDGET_PER_MINUTE = 64000
RAG_MAX_CHAR_BUDGET_PER_MINUTE = max(4000, min(500000, RAG_MAX_CHAR_BUDGET_PER_MINUTE))
try:
    RAG_CHAR_BUDGET_WINDOW_SECONDS = int(os.getenv("RAG_CHAR_BUDGET_WINDOW_SECONDS", "60"))
except (TypeError, ValueError):
    RAG_CHAR_BUDGET_WINDOW_SECONDS = 60
RAG_CHAR_BUDGET_WINDOW_SECONDS = max(30, min(600, RAG_CHAR_BUDGET_WINDOW_SECONDS))

# Observability / SLO thresholds (used for warning-level structured logs).
try:
    MCP_SLO_SEARCH_WARN_MS = int(os.getenv("MCP_SLO_SEARCH_WARN_MS", "1200"))
except (TypeError, ValueError):
    MCP_SLO_SEARCH_WARN_MS = 1200
if MCP_SLO_SEARCH_WARN_MS < 0:
    MCP_SLO_SEARCH_WARN_MS = 0
if MCP_SLO_SEARCH_WARN_MS > 120_000:
    MCP_SLO_SEARCH_WARN_MS = 120_000

try:
    MCP_SLO_READ_KNOWLEDGE_WARN_MS = int(os.getenv("MCP_SLO_READ_KNOWLEDGE_WARN_MS", "1500"))
except (TypeError, ValueError):
    MCP_SLO_READ_KNOWLEDGE_WARN_MS = 1500
if MCP_SLO_READ_KNOWLEDGE_WARN_MS < 0:
    MCP_SLO_READ_KNOWLEDGE_WARN_MS = 0
if MCP_SLO_READ_KNOWLEDGE_WARN_MS > 120_000:
    MCP_SLO_READ_KNOWLEDGE_WARN_MS = 120_000

try:
    MCP_SLO_TABLE_AGGREGATE_WARN_MS = int(os.getenv("MCP_SLO_TABLE_AGGREGATE_WARN_MS", "1200"))
except (TypeError, ValueError):
    MCP_SLO_TABLE_AGGREGATE_WARN_MS = 1200
if MCP_SLO_TABLE_AGGREGATE_WARN_MS < 0:
    MCP_SLO_TABLE_AGGREGATE_WARN_MS = 0
if MCP_SLO_TABLE_AGGREGATE_WARN_MS > 120_000:
    MCP_SLO_TABLE_AGGREGATE_WARN_MS = 120_000

try:
    MCP_SLO_DATASET_QUERY_WARN_MS = int(os.getenv("MCP_SLO_DATASET_QUERY_WARN_MS", "1500"))
except (TypeError, ValueError):
    MCP_SLO_DATASET_QUERY_WARN_MS = 1500
if MCP_SLO_DATASET_QUERY_WARN_MS < 0:
    MCP_SLO_DATASET_QUERY_WARN_MS = 0
if MCP_SLO_DATASET_QUERY_WARN_MS > 120_000:
    MCP_SLO_DATASET_QUERY_WARN_MS = 120_000

try:
    MCP_SLO_TURN_WARN_MS = int(os.getenv("MCP_SLO_TURN_WARN_MS", "15000"))
except (TypeError, ValueError):
    MCP_SLO_TURN_WARN_MS = 15000
if MCP_SLO_TURN_WARN_MS < 0:
    MCP_SLO_TURN_WARN_MS = 0
if MCP_SLO_TURN_WARN_MS > 600_000:
    MCP_SLO_TURN_WARN_MS = 600_000

try:
    MCP_SLO_TOOL_CALLS_WARN = int(os.getenv("MCP_SLO_TOOL_CALLS_WARN", "6"))
except (TypeError, ValueError):
    MCP_SLO_TOOL_CALLS_WARN = 6
if MCP_SLO_TOOL_CALLS_WARN < 0:
    MCP_SLO_TOOL_CALLS_WARN = 0
if MCP_SLO_TOOL_CALLS_WARN > 50:
    MCP_SLO_TOOL_CALLS_WARN = 50

try:
    INGEST_SLO_WARN_MS = int(os.getenv("INGEST_SLO_WARN_MS", "60000"))
except (TypeError, ValueError):
    INGEST_SLO_WARN_MS = 60000
if INGEST_SLO_WARN_MS < 0:
    INGEST_SLO_WARN_MS = 0
if INGEST_SLO_WARN_MS > 3_600_000:
    INGEST_SLO_WARN_MS = 3_600_000

try:
    INGEST_WORKER_HEALTH_INTERVAL_SECONDS = int(os.getenv("INGEST_WORKER_HEALTH_INTERVAL_SECONDS", "60"))
except (TypeError, ValueError):
    INGEST_WORKER_HEALTH_INTERVAL_SECONDS = 60
if INGEST_WORKER_HEALTH_INTERVAL_SECONDS < 0:
    INGEST_WORKER_HEALTH_INTERVAL_SECONDS = 0
if INGEST_WORKER_HEALTH_INTERVAL_SECONDS > 3_600:
    INGEST_WORKER_HEALTH_INTERVAL_SECONDS = 3_600

try:
    INGEST_QUEUE_WARN_BACKLOG = int(os.getenv("INGEST_QUEUE_WARN_BACKLOG", "50"))
except (TypeError, ValueError):
    INGEST_QUEUE_WARN_BACKLOG = 50
if INGEST_QUEUE_WARN_BACKLOG < 0:
    INGEST_QUEUE_WARN_BACKLOG = 0

try:
    INGEST_QUEUE_WARN_OLDEST_SECONDS = int(os.getenv("INGEST_QUEUE_WARN_OLDEST_SECONDS", "900"))
except (TypeError, ValueError):
    INGEST_QUEUE_WARN_OLDEST_SECONDS = 900
if INGEST_QUEUE_WARN_OLDEST_SECONDS < 0:
    INGEST_QUEUE_WARN_OLDEST_SECONDS = 0
if INGEST_QUEUE_WARN_OLDEST_SECONDS > 86_400:
    INGEST_QUEUE_WARN_OLDEST_SECONDS = 86_400

try:
    INGEST_QUEUE_WARN_FAILED_LAST_HOUR = int(os.getenv("INGEST_QUEUE_WARN_FAILED_LAST_HOUR", "10"))
except (TypeError, ValueError):
    INGEST_QUEUE_WARN_FAILED_LAST_HOUR = 10
if INGEST_QUEUE_WARN_FAILED_LAST_HOUR < 0:
    INGEST_QUEUE_WARN_FAILED_LAST_HOUR = 0
try:
    DATASET_CARD_MAX_CHARS = int(os.getenv("DATASET_CARD_MAX_CHARS", str(RAG_SEARCH_PREVIEW_CHAR_LIMIT)))
except (TypeError, ValueError):
    DATASET_CARD_MAX_CHARS = int(RAG_SEARCH_PREVIEW_CHAR_LIMIT)
if DATASET_CARD_MAX_CHARS < 200:
    DATASET_CARD_MAX_CHARS = 200
try:
    DATASET_CARD_MAX_COLUMNS = int(os.getenv("DATASET_CARD_MAX_COLUMNS", "60"))
except (TypeError, ValueError):
    DATASET_CARD_MAX_COLUMNS = 60
if DATASET_CARD_MAX_COLUMNS < 10:
    DATASET_CARD_MAX_COLUMNS = 10
try:
    DATASET_CARD_MAX_SHEETS = int(os.getenv("DATASET_CARD_MAX_SHEETS", "8"))
except (TypeError, ValueError):
    DATASET_CARD_MAX_SHEETS = 8
if DATASET_CARD_MAX_SHEETS < 1:
    DATASET_CARD_MAX_SHEETS = 1
INGEST_MAX_ACTIVE_JOBS_PER_BUSINESS = int(os.getenv("INGEST_MAX_ACTIVE_JOBS_PER_BUSINESS", "3"))
INGEST_SYNC_EMBED_CHUNK_LIMIT = int(os.getenv("INGEST_SYNC_EMBED_CHUNK_LIMIT", "200"))
INGEST_EMBED_BATCH_SIZE = int(os.getenv("INGEST_EMBED_BATCH_SIZE", "64"))
INGEST_EMBEDDING_BACKLOG_THRESHOLD = int(os.getenv("INGEST_EMBEDDING_BACKLOG_THRESHOLD", "500"))
try:
    INGEST_JOB_MAX_ATTEMPTS = int(os.getenv("INGEST_JOB_MAX_ATTEMPTS", "3"))
except (TypeError, ValueError):
    INGEST_JOB_MAX_ATTEMPTS = 3
if INGEST_JOB_MAX_ATTEMPTS < 1:
    INGEST_JOB_MAX_ATTEMPTS = 1
if INGEST_JOB_MAX_ATTEMPTS > 20:
    INGEST_JOB_MAX_ATTEMPTS = 20
try:
    INGEST_EMBED_JOB_MAX_ATTEMPTS = int(os.getenv("INGEST_EMBED_JOB_MAX_ATTEMPTS", "5"))
except (TypeError, ValueError):
    INGEST_EMBED_JOB_MAX_ATTEMPTS = 5
if INGEST_EMBED_JOB_MAX_ATTEMPTS < 1:
    INGEST_EMBED_JOB_MAX_ATTEMPTS = 1
if INGEST_EMBED_JOB_MAX_ATTEMPTS > 50:
    INGEST_EMBED_JOB_MAX_ATTEMPTS = 50
try:
    INGEST_JOB_LEASE_SECONDS = int(os.getenv("INGEST_JOB_LEASE_SECONDS", "3600"))
except (TypeError, ValueError):
    INGEST_JOB_LEASE_SECONDS = 3600
if INGEST_JOB_LEASE_SECONDS < 60:
    INGEST_JOB_LEASE_SECONDS = 60
if INGEST_JOB_LEASE_SECONDS > 86_400:
    INGEST_JOB_LEASE_SECONDS = 86_400
INGEST_JOB_REQUEUE_STALE_ENABLED = os.getenv("INGEST_JOB_REQUEUE_STALE_ENABLED", "true").lower() in {"1", "true", "yes"}
try:
    INGEST_JOB_RETRY_BASE_SECONDS = float(os.getenv("INGEST_JOB_RETRY_BASE_SECONDS", "5.0"))
except (TypeError, ValueError):
    INGEST_JOB_RETRY_BASE_SECONDS = 5.0
if INGEST_JOB_RETRY_BASE_SECONDS < 0.1:
    INGEST_JOB_RETRY_BASE_SECONDS = 0.1
try:
    INGEST_EMBED_JOB_RETRY_BASE_SECONDS = float(os.getenv("INGEST_EMBED_JOB_RETRY_BASE_SECONDS", str(INGEST_JOB_RETRY_BASE_SECONDS)))
except (TypeError, ValueError):
    INGEST_EMBED_JOB_RETRY_BASE_SECONDS = float(INGEST_JOB_RETRY_BASE_SECONDS)
if INGEST_EMBED_JOB_RETRY_BASE_SECONDS < 0.1:
    INGEST_EMBED_JOB_RETRY_BASE_SECONDS = 0.1
try:
    INGEST_JOB_RETRY_MAX_SECONDS = float(os.getenv("INGEST_JOB_RETRY_MAX_SECONDS", "300.0"))
except (TypeError, ValueError):
    INGEST_JOB_RETRY_MAX_SECONDS = 300.0
if INGEST_JOB_RETRY_MAX_SECONDS < 1.0:
    INGEST_JOB_RETRY_MAX_SECONDS = 1.0
if INGEST_JOB_RETRY_MAX_SECONDS > 86_400.0:
    INGEST_JOB_RETRY_MAX_SECONDS = 86_400.0
try:
    INGEST_JOB_RETRY_JITTER_SECONDS = float(os.getenv("INGEST_JOB_RETRY_JITTER_SECONDS", "2.0"))
except (TypeError, ValueError):
    INGEST_JOB_RETRY_JITTER_SECONDS = 2.0
if INGEST_JOB_RETRY_JITTER_SECONDS < 0.0:
    INGEST_JOB_RETRY_JITTER_SECONDS = 0.0
if INGEST_JOB_RETRY_JITTER_SECONDS > 120.0:
    INGEST_JOB_RETRY_JITTER_SECONDS = 120.0
try:
    RAG_EVAL_TOP_K = int(os.getenv("RAG_EVAL_TOP_K", "3"))
except (TypeError, ValueError):
    RAG_EVAL_TOP_K = 3
if RAG_EVAL_TOP_K < 1:
    RAG_EVAL_TOP_K = 1
if RAG_EVAL_TOP_K > 20:
    RAG_EVAL_TOP_K = 20
RAG_EVAL_THRESHOLDS = {
    "minimums": {
        "identifier_top1": float(os.getenv("RAG_EVAL_IDENTIFIER_TOP1", "0.9")),
        "not_found_accuracy": float(os.getenv("RAG_EVAL_NOT_FOUND_ACC", "0.95")),
        "mrr": float(os.getenv("RAG_EVAL_MRR", "0.92")),
        "source_accuracy": float(os.getenv("RAG_EVAL_SOURCE_ACC", "0.97")),
        "behavior_accuracy": float(os.getenv("RAG_EVAL_BEHAVIOR_ACC", "0.9")),
    },
    "maximums": {
        "vector.p95": float(os.getenv("RAG_EVAL_VECTOR_P95_MS", "350")),
    },
}
try:
    MCP_LOAD_TEST_P95_MAX_MS = int(os.getenv("MCP_LOAD_TEST_P95_MAX_MS", "1500"))
except (TypeError, ValueError):
    MCP_LOAD_TEST_P95_MAX_MS = 1500
MCP_LOAD_TEST_P95_MAX_MS = max(1, min(120_000, MCP_LOAD_TEST_P95_MAX_MS))
try:
    MCP_LOAD_TEST_MAX_ERROR_RATE = float(os.getenv("MCP_LOAD_TEST_MAX_ERROR_RATE", "0.02"))
except (TypeError, ValueError):
    MCP_LOAD_TEST_MAX_ERROR_RATE = 0.02
MCP_LOAD_TEST_MAX_ERROR_RATE = max(0.0, min(1.0, MCP_LOAD_TEST_MAX_ERROR_RATE))
try:
    MCP_LOAD_TEST_MAX_THROTTLED_RATE = float(os.getenv("MCP_LOAD_TEST_MAX_THROTTLED_RATE", "0.02"))
except (TypeError, ValueError):
    MCP_LOAD_TEST_MAX_THROTTLED_RATE = 0.02
MCP_LOAD_TEST_MAX_THROTTLED_RATE = max(0.0, min(1.0, MCP_LOAD_TEST_MAX_THROTTLED_RATE))
RAG_DRIFT_TRUNCATION_THRESHOLD = float(os.getenv("RAG_DRIFT_TRUNCATION_THRESHOLD", "0.2"))
RAG_DRIFT_ALIAS_HIT_THRESHOLD = float(os.getenv("RAG_DRIFT_ALIAS_HIT_THRESHOLD", "0.85"))
RAG_DRIFT_NOT_FOUND_THRESHOLD = float(os.getenv("RAG_DRIFT_NOT_FOUND_THRESHOLD", "0.3"))
# Enable the portal streaming state machine by default so progressive spinner phases
# ("Searching knowledge…", "Exploring deeper insights…", etc.) are surfaced unless
# an environment override disables it.
PORTAL_STREAM_STATE_MACHINE = os.getenv("PORTAL_STREAM_STATE_MACHINE", "true").lower() in {"1", "true", "yes"}
try:
    PORTAL_SPINNER_PHASE_INTERVAL = float(os.getenv("PORTAL_SPINNER_PHASE_INTERVAL", "5.5"))
except (TypeError, ValueError):
    PORTAL_SPINNER_PHASE_INTERVAL = 5.5
if PORTAL_SPINNER_PHASE_INTERVAL < 0:
    PORTAL_SPINNER_PHASE_INTERVAL = 0.0
PORTAL_ASSET_VERSION = os.getenv("PORTAL_ASSET_VERSION")
if not PORTAL_ASSET_VERSION:
    PORTAL_ASSET_VERSION = str(int(time.time()))

# ==============================================================================
# SECURITY MIDDLEWARE & HEADERS
# ==============================================================================

# HTTPS enforcement (production only - enable AFTER SSL is verified)
SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "false").lower() in {"1", "true", "yes"}

# HTTP Strict Transport Security (HSTS) - enforce HTTPS for 1 year
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_HSTS_PRELOAD = SECURE_HSTS_SECONDS > 0

# Secure cookies (HTTPS only)
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}
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

# Option 1: Use DATABASE_URL if provided (Railway, Heroku, Render)
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
            'NAME': os.getenv('POSTGRES_DB', 'djangopocket'),
            'USER': os.getenv('POSTGRES_USER', 'djangopocket'),
            'PASSWORD': os.getenv('POSTGRES_PASSWORD', 'adham123'),  # Default for local dev only
            'HOST': os.getenv('POSTGRES_HOST', 'localhost'),
            'PORT': os.getenv('POSTGRES_PORT', '5432'),
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
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
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
        "apps.llm.llm_provider": {"handlers": ["console", "deepseek_file"], "level": "INFO", "propagate": False},
        "apps.knowledge.knowledge_ingestion": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "apps.rag.ai_orchestrator": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "apps.mcp.tools": {"handlers": ["console", "rag_file"], "level": "INFO", "propagate": False},
        "apps.mcp.orchestrator": {"handlers": ["console", "rag_file"], "level": "INFO", "propagate": False},
        "apps.api.chat_portal": {"handlers": ["console", "rag_file"], "level": "INFO", "propagate": False},
    },
}
