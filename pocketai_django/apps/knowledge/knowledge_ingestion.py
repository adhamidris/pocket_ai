from __future__ import annotations

from apps.accounts.feature_flags import FeatureFlagService
from apps.knowledge.ingestion import service as _ingestion_service
from apps.knowledge.ingestion.azure_di import AzureDocumentIntelligenceExtractor
from apps.knowledge.ingestion.contracts import (
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
from apps.knowledge.tables.geometry_tools.reconstructor import GeometryTableReconstructor
from apps.knowledge.ingestion.jobs import IngestionJobResult, get_ingestion_queue_health, queue_ingestion_job
from apps.knowledge.ingestion.service import DocxDocument, KnowledgeIngestionService as _KnowledgeIngestionService
from apps.knowledge.ingestion.signals import COLUMN_ROLE_INFERENCE_VERSION
from apps.rag.embeddings import build_embedding_service


class KnowledgeIngestionService(_KnowledgeIngestionService):
    """
    Compatibility facade for legacy imports.

    New code should import KnowledgeIngestionService from
    apps.knowledge.ingestion.service. This subclass preserves older test patch
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
