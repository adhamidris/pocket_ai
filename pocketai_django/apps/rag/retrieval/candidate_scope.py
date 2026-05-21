from __future__ import annotations

import uuid
from typing import Sequence

from django.db.models import Q

from apps.accounts.models import KnowledgeStatus
from apps.knowledge.access.visibility import apply_customer_visible_chunks
from apps.knowledge.models import KnowledgeUploadChunk


class CandidateScopeMixin:

    def _fetch_chunks_by_ids(
        self,
        *,
        business_profile,
        chunk_ids: Sequence[uuid.UUID],
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> dict[uuid.UUID, KnowledgeUploadChunk]:
        if not chunk_ids:
            return {}
        qs = KnowledgeUploadChunk.objects.filter(
            business_profile=business_profile,
            upload__status=KnowledgeStatus.ACTIVE,
            id__in=chunk_ids,
        )
        qs = self._apply_chunk_scope(
            qs,
            business_profile=business_profile,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        qs = apply_customer_visible_chunks(qs.select_related("upload"))
        return {chunk.id: chunk for chunk in qs}

    def _base_chunk_queryset(
        self,
        business_profile,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ):
        qs = KnowledgeUploadChunk.objects.filter(
            business_profile=business_profile,
            upload__status=KnowledgeStatus.ACTIVE,
        ).filter(
            Q(metadata__search_tier__isnull=True) | ~Q(metadata__search_tier="drill_down")
        )
        qs = self._apply_chunk_scope(
            qs,
            business_profile=business_profile,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        return apply_customer_visible_chunks(qs.select_related("upload"))

    def _apply_chunk_scope(
        self,
        queryset,
        *,
        business_profile,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ):
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                return queryset.none()
            return queryset.filter(upload_id__in=allowed_upload_ids)

        clauses: list[Q] = []
        if allowed_explicit_upload_ids:
            clauses.append(Q(upload_id__in=allowed_explicit_upload_ids))
        if not clauses:
            return queryset
        combined = clauses[0]
        for clause in clauses[1:]:
            combined |= clause
        return queryset.filter(combined)
