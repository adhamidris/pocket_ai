from __future__ import annotations

import logging

from django.apps import AppConfig
from django.conf import settings


logger = logging.getLogger(__name__)


class ServicesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.services"
    verbose_name = "Services"

    def ready(self) -> None:
        if not getattr(settings, "RAG_USE_MCP_ORCHESTRATOR", False):
            return
        try:
            from apps.services.embeddings import warm_rag_embeddings

            warm_rag_embeddings(log=logger)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("RAG embedding warmup skipped: %s", exc)
