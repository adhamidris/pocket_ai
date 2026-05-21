from __future__ import annotations

import dataclasses
import uuid
from typing import TYPE_CHECKING, Any, Mapping, MutableMapping, Optional, Sequence

from apps.rag.query.classifier import QueryClassification

if TYPE_CHECKING:
    from apps.rag.contracts import AliasSearchResult, QueryTraits


@dataclasses.dataclass
class RetrievalContext:
    """
    Context object containing all parameters needed for retrieval.
    
    This encapsulates the search parameters so strategies don't need
    to know about the full orchestrator interface.
    """
    business_profile: Any
    query: str
    traits: "QueryTraits"
    classification: QueryClassification
    table_context: Mapping[str, Any]
    
    # Search configuration
    limit: int
    alias_result: Optional["AliasSearchResult"] = None
    session_cache: Optional[MutableMapping[str, object]] = None
    identifier_filter: Optional[Mapping[str, str]] = None
    
    # Scope filters
    allowed_upload_ids: Optional[Sequence[uuid.UUID]] = None
    allowed_explicit_upload_ids: Optional[Sequence[uuid.UUID]] = None
    
    # Feature flags snapshot
    feature_state: Any = None
    
    # Request tracking
    request_id: Optional[uuid.UUID] = None


@dataclasses.dataclass
class RetrievalHints:
    """
    Hints for the retrieval layer based on strategy decisions.
    
    These hints influence how the underlying search methods behave
    without requiring changes to their interfaces.
    """
    snippet_limit_multiplier: float = 1.0
    diversify_tables: bool = False
    prefer_table_headers: bool = False
    include_all_tables: bool = False
    min_tables_coverage: str = "default"  # "default", "all", or a number
    filter_by_entity_names: bool = False
    entity_names_filter: Sequence[str] = dataclasses.field(default_factory=list)
    comprehensive_intent: bool = False
    expand_table_rows: bool = True
    prefer_section_context: bool = False
    modality_bias: str = "mixed"
    
    def to_dict(self) -> dict[str, Any]:
        """Convert hints to a dictionary for diagnostics."""
        return {
            "snippet_limit_multiplier": self.snippet_limit_multiplier,
            "diversify_tables": self.diversify_tables,
            "prefer_table_headers": self.prefer_table_headers,
            "include_all_tables": self.include_all_tables,
            "min_tables_coverage": self.min_tables_coverage,
            "filter_by_entity_names": self.filter_by_entity_names,
            "entity_names_filter": list(self.entity_names_filter),
            "comprehensive_intent": self.comprehensive_intent,
            "expand_table_rows": self.expand_table_rows,
            "prefer_section_context": self.prefer_section_context,
            "modality_bias": self.modality_bias,
        }


@dataclasses.dataclass
class StrategyResult:
    """
    Result from a retrieval strategy execution.
    
    Contains the retrieval hints and any strategy-specific diagnostics.
    The actual search execution is still performed by the orchestrator
    using these hints.
    """
    hints: RetrievalHints
    diagnostics: dict[str, Any] = dataclasses.field(default_factory=dict)
    
    # Strategy can optionally modify the effective limit
    effective_limit: Optional[int] = None

