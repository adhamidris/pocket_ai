from __future__ import annotations

from django.db.models import QuerySet

from apps.accounts.models import KnowledgeVisibility


CUSTOMER_VISIBILITY_POLICY_KEY = "exclude_internal_v1"


def apply_customer_visible_uploads(qs: QuerySet) -> QuerySet:
    """
    Apply customer-facing visibility rules to a KnowledgeUpload queryset.

    Today this means excluding INTERNAL uploads from any portal/RAG retrieval.
    """

    return qs.exclude(visibility=KnowledgeVisibility.INTERNAL)


def apply_customer_visible_chunks(qs: QuerySet) -> QuerySet:
    """
    Apply customer-facing visibility rules to a KnowledgeUploadChunk queryset.

    Today this means excluding chunks whose parent upload is INTERNAL.
    """

    return qs.exclude(upload__visibility=KnowledgeVisibility.INTERNAL)
