"""WSGI config for PocketAI Django project."""

import os

from django.core.wsgi import get_wsgi_application

from pocketai.env import load_project_env
from core.tracing import configure_tracing

load_project_env()

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pocketai.settings")

configure_tracing()

application = get_wsgi_application()
