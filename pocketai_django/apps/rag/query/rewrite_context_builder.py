from __future__ import annotations

from typing import Mapping

from apps.rag.query.rewrite_types import RewriteContext
from apps.rag.lexicon.tenant import TenantLexiconService


def build_rewrite_context_from_tool_context(tool_context, *, conversation=None) -> RewriteContext:
    """
    Build a RewriteContext from a ToolExecutionContext.

    This helper function extracts the relevant fields from ToolExecutionContext
    and creates a RewriteContext for query rewriting.

    Args:
        tool_context: ToolExecutionContext instance

    Returns:
        RewriteContext for query rewriting
    """
    if tool_context is None:
        return RewriteContext()

    # Get document context
    doc_context = tool_context.get_document_context_for_query()

    previous_queries: list[str] = []

    # Prefer conversation-level customer messages so follow-up detection works across turns.
    if conversation is not None:
        try:
            from apps.conversations.models import ConversationSender

            raw_messages = list(
                conversation.messages.filter(sender=ConversationSender.CUSTOMER)
                .order_by("-sent_at", "-created_at")
                .values_list("body", flat=True)[:6]
            )
            raw_messages.reverse()  # chronological
            if raw_messages:
                # Drop the most recent customer message (current query) so we only use prior context.
                raw_messages = raw_messages[:-1]
            for body in raw_messages[-5:]:
                if isinstance(body, str) and body.strip():
                    previous_queries.append(body.strip())
        except Exception:
            previous_queries = []

    # Fallback to in-turn search history when conversation messages are unavailable.
    if not previous_queries:
        search_history = getattr(tool_context, "search_history", [])
        for entry in search_history[-5:]:  # Last 5 queries
            query = entry.get("query") if isinstance(entry, Mapping) else None
            if query:
                previous_queries.append(str(query))

    tenant_entity_terms: tuple[str, ...] = ()
    tenant_attribute_terms: tuple[str, ...] = ()
    business_profile = getattr(conversation, "business_profile", None) if conversation is not None else None
    if business_profile is not None:
        try:
            snapshot = TenantLexiconService().get_snapshot(business_profile=business_profile, use_cache=True)
            if isinstance(snapshot, Mapping):
                entity_values = snapshot.get("entity_terms")
                if isinstance(entity_values, (list, tuple, set)):
                    tenant_entity_terms = tuple(str(value).strip() for value in entity_values if str(value).strip())[:200]
                attribute_values = snapshot.get("attribute_terms")
                if isinstance(attribute_values, (list, tuple, set)):
                    tenant_attribute_terms = tuple(
                        str(value).strip() for value in attribute_values if str(value).strip()
                    )[:200]
        except Exception:
            tenant_entity_terms = ()
            tenant_attribute_terms = ()

    return RewriteContext(
        primary_document_title=doc_context.get("primary_document_title"),
        primary_upload_id=doc_context.get("primary_upload_id"),
        referenced_documents=tuple(doc_context.get("referenced_upload_ids", [])),
        previous_queries=tuple(previous_queries),
        tenant_entity_terms=tenant_entity_terms,
        tenant_attribute_terms=tenant_attribute_terms,
    )
