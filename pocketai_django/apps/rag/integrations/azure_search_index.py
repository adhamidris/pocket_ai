from __future__ import annotations

from apps.rag.integrations.azure_ai_search import AzureAISearchConfig, AzureAISearchError


def ensure_index(*, config: AzureAISearchConfig, embedding_dim: int) -> None:
    """
    Create or update the knowledge chunk index in Azure AI Search.

    Safe to call repeatedly. Requires admin key.
    """

    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents.indexes import SearchIndexClient
        from azure.search.documents.indexes.models import (
            HnswAlgorithmConfiguration,
            HnswParameters,
            SearchField,
            SearchFieldDataType,
            SearchIndex,
            SearchableField,
            SemanticConfiguration,
            SemanticField,
            SemanticPrioritizedFields,
            SemanticSearch,
            SimpleField,
            VectorSearch,
            VectorSearchAlgorithmMetric,
            VectorSearchProfile,
        )
    except Exception as exc:  # pragma: no cover - optional dependency
        raise AzureAISearchError(f"azure-search-documents is required: {exc}") from exc

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True, sortable=False, facetable=False),
        SimpleField(name="business_id", type=SearchFieldDataType.String, filterable=True, sortable=False, facetable=False),
        SimpleField(name="upload_id", type=SearchFieldDataType.String, filterable=True, sortable=False, facetable=False),
        SimpleField(name="chunk_id", type=SearchFieldDataType.String, filterable=True, sortable=False, facetable=False),
        SimpleField(name="chunk_index", type=SearchFieldDataType.Int32, filterable=True, sortable=True, facetable=False),
        SearchableField(name="title", type=SearchFieldDataType.String, searchable=True, filterable=False, sortable=False, facetable=False, retrievable=True),
        SearchableField(name="content", type=SearchFieldDataType.String, searchable=True, filterable=False, sortable=False, facetable=False, retrievable=True),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            retrievable=False,
            vector_search_dimensions=int(embedding_dim),
            vector_search_profile_name="default",
        ),
        SimpleField(name="format", type=SearchFieldDataType.String, filterable=True, sortable=False, facetable=False),
        SimpleField(name="index_type", type=SearchFieldDataType.String, filterable=True, sortable=False, facetable=False),
        SimpleField(name="is_table_chunk", type=SearchFieldDataType.Boolean, filterable=True, sortable=False, facetable=False),
        SimpleField(name="updated_at", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True, facetable=False),
    ]

    vector_search = VectorSearch(
        profiles=[VectorSearchProfile(name="default", algorithm_configuration_name="hnsw")],
        algorithms=[
            HnswAlgorithmConfiguration(
                name="hnsw",
                parameters=HnswParameters(
                    metric=VectorSearchAlgorithmMetric.COSINE,
                    m=6,
                    ef_construction=400,
                    ef_search=200,
                ),
            )
        ],
    )

    semantic_search = SemanticSearch(
        default_configuration_name="default",
        configurations=[
            SemanticConfiguration(
                name="default",
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="content")],
                ),
            )
        ],
    )

    index = SearchIndex(
        name=config.index_name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )

    client = SearchIndexClient(config.endpoint, AzureKeyCredential(config.admin_key))
    client.create_or_update_index(index)

