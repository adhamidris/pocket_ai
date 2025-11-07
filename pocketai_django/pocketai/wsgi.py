"""WSGI config for PocketAI Django project."""

import os

from django.core.wsgi import get_wsgi_application

from pocketai.env import load_project_env

load_project_env()

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pocketai.settings")

application = get_wsgi_application()
