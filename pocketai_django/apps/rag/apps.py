from __future__ import annotations

import logging
import sys

from django.apps import AppConfig
from django.conf import settings


logger = logging.getLogger(__name__)


class RagConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.rag"

    def ready(self) -> None:
        # Avoid implicit network/model downloads during test runs.
        # Local/dev/prod warmup remains available via `warm_embeddings` and docker entrypoints.
        if "test" in sys.argv:
            return
        if not getattr(settings, "RAG_WARM_EMBEDDINGS_ON_STARTUP", True):
            return
        try:
            from .embeddings import warm_rag_embeddings

            warm_rag_embeddings(log=logger)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("RAG embedding warmup skipped: %s", exc)
