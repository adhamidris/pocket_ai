"""Django settings for the server-rendered PocketAI project."""

from pathlib import Path
import os

# Base directory of the Django project (the folder that contains manage.py)
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: replace before production
SECRET_KEY = "django-insecure-change-me"

DEBUG = True

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


ALLOWED_HOSTS: list[str] = []

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

EMBED_PROVIDER = os.getenv("EMBED_PROVIDER", "local")
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
EMBED_DIM = int(os.getenv("EMBED_DIM", "384"))
EMBED_DISTANCE = os.getenv("EMBED_DISTANCE", "cosine")  # 'cosine'|'l2'|'ip'

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
