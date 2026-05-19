from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from apps.knowledge.ingestion.contracts import ExtractionResult
from apps.knowledge.lexicon.learning import TenantLexiconAutoLearningService
from apps.knowledge.models import KnowledgeUpload


logger = logging.getLogger(__name__)


class IngestionLexiconAutoLearningMixin:

    def _auto_learn_tenant_lexicon(
        self,
        *,
        upload: KnowledgeUpload,
        extraction: ExtractionResult,
        structured_summary: Mapping[str, Any] | None,
        ingestion_metadata: Mapping[str, Any] | None,
        entity_payloads: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self.tenant_lexicon_auto_learning_enabled:
            return {"enabled": False, "term_count": 0, "synonym_count": 0, "language_code": "und"}
        if self._tenant_lexicon_auto_learning_service is None:
            self._tenant_lexicon_auto_learning_service = TenantLexiconAutoLearningService()
        try:
            stats = self._tenant_lexicon_auto_learning_service.learn_from_ingestion(
                upload=upload,
                extraction=extraction,
                structured_summary=structured_summary,
                ingestion_metadata=ingestion_metadata,
                entity_payloads=entity_payloads,
            )
            logger.info(
                "lexicon.autolearn.summary upload=%s business=%s terms=%s synonyms=%s enabled=%s",
                upload.id,
                upload.business_profile_id,
                stats.get("term_count", 0),
                stats.get("synonym_count", 0),
                stats.get("enabled", True),
            )
            return stats
        except Exception as exc:  # pragma: no cover - ingestion must stay resilient
            logger.warning(
                "lexicon.autolearn.failed upload=%s business=%s error=%s",
                upload.id,
                upload.business_profile_id,
                exc,
            )
            return {
                "enabled": True,
                "term_count": 0,
                "synonym_count": 0,
                "error": str(exc)[:240],
            }
