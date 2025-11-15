from importlib import import_module

__all__ = [
    "CaseDetail",
    "CaseListResult",
    "CaseMetrics",
    "CaseSummary",
    "CaseServiceError",
    "AgentListItem",
    "AgentKnowledgeItem",
    "AgentListResult",
    "AgentDetail",
    "AgentStats",
    "add_case_message",
    "add_case_note",
    "add_history_entry",
    "AgentListValidationError",
    "agent_identifier",
    "bulk_attach_documents",
    "create_case",
    "display_role_label",
    "display_tone_label",
    "CustomerDetail",
    "CustomerListResult",
    "CustomerSummary",
    "DocumentDetail",
    "DocumentListItem",
    "DocumentListResult",
    "DocumentFileMeta",
    "DocumentUrlMeta",
    "DocumentTextMeta",
    "ScrapedDocument",
    "CsvPreview",
    "CsvPreviewError",
    "get_agent_detail",
    "get_case_detail",
    "get_customer_detail",
    "get_document_detail",
    "initials_from_name",
    "list_agents",
    "list_cases",
    "list_customers",
    "list_documents",
    "preview_csv_upload",
    "scrape_document_source",
    "update_case",
    "DocumentListValidationError",
    "DocumentScrapeError",
    "EmbeddingService",
    "EmbeddingProviderError",
    "build_embedding_service",
    "IntegrationSyncService",
    "KnowledgeIngestionService",
    "KnowledgeIngestionError",
    "IngestionJobResult",
    "queue_ingestion_job",
    "RAGEvaluationHarness",
]

_LAZY_ATTRS = {}


def _register(module_path: str, names: list[str]) -> None:
    for name in names:
        _LAZY_ATTRS[name] = module_path


_register(
    "apps.services.agents",
    [
        "AgentDetail",
        "AgentKnowledgeItem",
        "AgentListItem",
        "AgentListResult",
        "AgentListValidationError",
        "AgentStats",
        "agent_identifier",
        "display_role_label",
        "display_tone_label",
        "get_agent_detail",
        "initials_from_name",
        "list_agents",
    ],
)
_register(
    "apps.services.cases",
    [
        "CaseDetail",
        "CaseListResult",
        "CaseMetrics",
        "CaseSummary",
        "CaseServiceError",
        "add_case_message",
        "add_case_note",
        "add_history_entry",
        "bulk_attach_documents",
        "create_case",
        "get_case_detail",
        "list_cases",
        "update_case",
    ],
)
_register(
    "apps.services.customers",
    [
        "CustomerDetail",
        "CustomerListResult",
        "CustomerSummary",
        "get_customer_detail",
        "list_customers",
    ],
)
_register(
    "apps.services.documents",
    [
        "CsvPreview",
        "CsvPreviewError",
        "DocumentDetail",
        "DocumentFileMeta",
        "DocumentListItem",
        "DocumentListResult",
        "DocumentListValidationError",
        "DocumentScrapeError",
        "DocumentTextMeta",
        "DocumentUrlMeta",
        "ScrapedDocument",
        "get_document_detail",
        "list_documents",
        "preview_csv_upload",
        "scrape_document_source",
    ],
)
_register(
    "apps.services.embeddings",
    [
        "EmbeddingProviderError",
        "EmbeddingService",
        "build_embedding_service",
    ],
)
_register(
    "apps.services.integration_sync",
    [
        "IntegrationSyncService",
    ],
)
_register(
    "apps.services.knowledge_ingestion",
    [
        "IngestionJobResult",
        "KnowledgeIngestionError",
        "KnowledgeIngestionService",
        "queue_ingestion_job",
    ],
)
_register(
    "apps.services.evaluation.harness",
    [
        "RAGEvaluationHarness",
    ],
)


def __getattr__(name: str):
    module_path = _LAZY_ATTRS.get(name)
    if module_path is None:
        raise AttributeError(f"module 'apps.services' has no attribute '{name}'")
    module = import_module(module_path)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(list(__all__))
