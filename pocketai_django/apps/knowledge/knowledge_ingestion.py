from __future__ import annotations

from apps.accounts.feature_flags import FeatureFlagService
from apps.knowledge import ingestion_service as _ingestion_service
from apps.knowledge.ingestion_azure_di import AzureDocumentIntelligenceExtractor
from apps.knowledge.ingestion_contracts import (
    EnhancedContextDocument,
    ExtractionResult,
    KnowledgeIngestionError,
    PageBlockPayload,
    PageLayout,
    PageRendererResult,
    PdfSpan,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
)
from apps.knowledge.ingestion_geometry_tables import GeometryTableReconstructor
from apps.knowledge.ingestion_jobs import IngestionJobResult, get_ingestion_queue_health, queue_ingestion_job
from apps.knowledge.ingestion_service import DocxDocument, KnowledgeIngestionService as _KnowledgeIngestionService
from apps.knowledge.ingestion_signals import COLUMN_ROLE_INFERENCE_VERSION
from apps.rag.embeddings import build_embedding_service


class KnowledgeIngestionService(_KnowledgeIngestionService):
    """
    Compatibility facade for legacy imports.

    New code should import KnowledgeIngestionService from
    apps.knowledge.ingestion_service. This subclass preserves older test patch
    points such as apps.knowledge.knowledge_ingestion.build_embedding_service.
    """

    def __init__(self, *args, **kwargs):
        _ingestion_service.build_embedding_service = build_embedding_service
        super().__init__(*args, **kwargs)


__all__ = [
    "AzureDocumentIntelligenceExtractor",
    "COLUMN_ROLE_INFERENCE_VERSION",
    "DocxDocument",
    "EnhancedContextDocument",
    "ExtractionResult",
    "FeatureFlagService",
    "GeometryTableReconstructor",
    "IngestionJobResult",
    "KnowledgeIngestionError",
    "KnowledgeIngestionService",
    "PageBlockPayload",
    "PageLayout",
    "PageRendererResult",
    "PdfSpan",
    "TableCellPayload",
    "TablePayload",
    "TableRowPayload",
    "build_embedding_service",
    "get_ingestion_queue_health",
    "queue_ingestion_job",
]
