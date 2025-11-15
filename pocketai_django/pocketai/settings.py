"""Django settings for the server-rendered PocketAI project."""

from pathlib import Path
import base64
import binascii
import hashlib
import os

# Base directory of the Django project (the folder that contains manage.py)
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: replace before production
SECRET_KEY = "django-insecure-change-me"

DEBUG = True

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


ALLOWED_HOSTS: list[str] = []


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

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Project apps
    "apps.accounts",
    "apps.cases",
    "apps.customers",
    "apps.conversations",
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
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
EMBED_DIM = int(os.getenv("EMBED_DIM", "384"))
EMBED_DISTANCE = os.getenv("EMBED_DISTANCE", "cosine")  # 'cosine'|'l2'|'ip'
INGEST_MAX_JSON_ENTITIES_DEFAULT = int(os.getenv("INGEST_MAX_JSON_ENTITIES_DEFAULT", "1000"))
INGEST_MAX_JSON_ENTITY_CANDIDATES = int(os.getenv("INGEST_MAX_JSON_ENTITY_CANDIDATES", "4000"))
INGEST_ALIAS_WARNING_THRESHOLD = int(os.getenv("INGEST_ALIAS_WARNING_THRESHOLD", "2000"))
RAG_MAX_SNIPPETS_PER_SEARCH = int(os.getenv("RAG_MAX_SNIPPETS_PER_SEARCH", "3"))
RAG_ALIAS_MAX_CHUNKS_PER_UPLOAD = int(os.getenv("RAG_ALIAS_MAX_CHUNKS_PER_UPLOAD", "2"))
RAG_ANN_MAX_CHUNKS_PER_UPLOAD = int(os.getenv("RAG_ANN_MAX_CHUNKS_PER_UPLOAD", "3"))
RAG_SEARCH_PREVIEW_CHAR_LIMIT = int(os.getenv("RAG_SEARCH_PREVIEW_CHAR_LIMIT", "800"))
RAG_IVFFLAT_PROBES = int(os.getenv("RAG_IVFFLAT_PROBES", "8"))
RAG_QUERY_VECTOR_CACHE_MAX_BYTES = int(os.getenv("RAG_QUERY_VECTOR_CACHE_MAX_BYTES", "16384"))
RAG_NEIGHBOR_WINDOW_CACHE_SIZE = int(os.getenv("RAG_NEIGHBOR_WINDOW_CACHE_SIZE", "128"))
RAG_BUSINESS_OVERRIDE_KEY = os.getenv("RAG_BUSINESS_OVERRIDE_KEY", "rag_overrides")
RAG_TABLE_RESULT_LIMIT = int(os.getenv("RAG_TABLE_RESULT_LIMIT", "3"))
RAG_TABLE_SIMILARITY_THRESHOLD = float(os.getenv("RAG_TABLE_SIMILARITY_THRESHOLD", "0.3"))
RAG_TABLE_COLUMN_CACHE_SIZE = int(os.getenv("RAG_TABLE_COLUMN_CACHE_SIZE", "32"))
RAG_TABLE_COLUMN_SAMPLE = int(os.getenv("RAG_TABLE_COLUMN_SAMPLE", "200"))
TABLE_MAX_ROWS_DEFAULT = int(os.getenv("TABLE_MAX_ROWS_DEFAULT", "5000"))
TABLE_MAX_COLUMNS_DEFAULT = int(os.getenv("TABLE_MAX_COLUMNS_DEFAULT", "80"))
INGEST_MAX_ACTIVE_JOBS_PER_BUSINESS = int(os.getenv("INGEST_MAX_ACTIVE_JOBS_PER_BUSINESS", "3"))
INGEST_SYNC_EMBED_CHUNK_LIMIT = int(os.getenv("INGEST_SYNC_EMBED_CHUNK_LIMIT", "200"))
INGEST_EMBED_BATCH_SIZE = int(os.getenv("INGEST_EMBED_BATCH_SIZE", "64"))
INGEST_EMBEDDING_BACKLOG_THRESHOLD = int(os.getenv("INGEST_EMBEDDING_BACKLOG_THRESHOLD", "500"))
RAG_EVAL_THRESHOLDS = {
    "minimums": {
        "identifier_top1": float(os.getenv("RAG_EVAL_IDENTIFIER_TOP1", "0.9")),
        "not_found_accuracy": float(os.getenv("RAG_EVAL_NOT_FOUND_ACC", "0.95")),
        "mrr": float(os.getenv("RAG_EVAL_MRR", "0.92")),
    },
    "maximums": {
        "vector.p95": float(os.getenv("RAG_EVAL_VECTOR_P95_MS", "350")),
    },
}
RAG_DRIFT_TRUNCATION_THRESHOLD = float(os.getenv("RAG_DRIFT_TRUNCATION_THRESHOLD", "0.2"))
RAG_DRIFT_ALIAS_HIT_THRESHOLD = float(os.getenv("RAG_DRIFT_ALIAS_HIT_THRESHOLD", "0.85"))
RAG_DRIFT_NOT_FOUND_THRESHOLD = float(os.getenv("RAG_DRIFT_NOT_FOUND_THRESHOLD", "0.3"))

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

# Database: placeholder SQLite setup until backend migration
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'djangopocket',
        'USER': 'djangopocket',
        'PASSWORD': 'adham123',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}

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
        "deepseek_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(DEEPSEEK_LOG_FILE),
            "maxBytes": 1024 * 1024,
            "backupCount": 3,
            "formatter": "verbose",
        },
    },
    "loggers": {
        "apps.services.llm_provider": {
            "handlers": ["console", "deepseek_file"],
            "level": "INFO",  # use DEBUG if you want even more detail
            "propagate": False,
        },
        "apps.services.knowledge_ingestion": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "apps.services.ai_orchestrator": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
