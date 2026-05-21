from __future__ import annotations

import uuid
from typing import Mapping, Sequence

from django.db.models import Q

from apps.accounts.models import KnowledgeStatus, KnowledgeVisibility
from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadTableCell


class TableRowLabelMixin:

    def _table_row_label_tokens_for_business(
        self,
        business_profile,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> set[str]:
        business_id = getattr(business_profile, "id", None)
        if not business_id:
            return set()
        scope_key = (
            business_id,
            self._upload_scope_token(
                allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            ),
        )
        cached = self._table_row_label_cache.get(scope_key)
        if cached is not None:
            self._table_row_label_cache.move_to_end(scope_key)
            return cached
        uploads_qs = KnowledgeUpload.objects.filter(
            business_profile=business_profile,
            status=KnowledgeStatus.ACTIVE,
        ).exclude(visibility=KnowledgeVisibility.INTERNAL)
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                self._table_row_label_cache[scope_key] = set()
                return set()
            uploads_qs = uploads_qs.filter(id__in=allowed_upload_ids)
        else:
            clauses: list[Q] = []
            if allowed_explicit_upload_ids:
                clauses.append(Q(id__in=allowed_explicit_upload_ids))
            if clauses:
                clause = clauses[0]
                for extra in clauses[1:]:
                    clause |= extra
                uploads_qs = uploads_qs.filter(clause)
        uploads_qs = self._filter_queryable_table_uploads(
            uploads_qs,
            format_lookup="ingestion_metadata__format",
        )
        profiles = list(
            uploads_qs.order_by("-updated_at").values_list("ingestion_metadata__table_profile", flat=True)[
                : self.table_row_label_sample_limit
            ]
        )
        cell_qs = KnowledgeUploadTableCell.objects.filter(
            table__upload__business_profile=business_profile,
            column_index=0,
        ).exclude(table__upload__visibility=KnowledgeVisibility.INTERNAL)
        tokens: set[str] = set()
        for profile in profiles:
            if not isinstance(profile, Mapping):
                continue
            for token in profile.get("row_label_tokens") or []:
                cleaned = str(token).strip().lower()
                if cleaned:
                    tokens.add(cleaned)
        if not tokens:
            if allowed_upload_ids is not None:
                cell_qs = cell_qs.filter(table__upload_id__in=allowed_upload_ids)
            else:
                clauses = []
                if allowed_explicit_upload_ids:
                    clauses.append(Q(table__upload_id__in=allowed_explicit_upload_ids))
                if clauses:
                    clause = clauses[0]
                    for extra in clauses[1:]:
                        clause |= extra
                    cell_qs = cell_qs.filter(clause)
            cell_qs = self._filter_queryable_table_uploads(
                cell_qs,
                format_lookup="table__upload__ingestion_metadata__format",
            ).exclude(row__metadata__row_type="header")
            labels = list(
                cell_qs.order_by("-row__updated_at")
                .values_list("raw_text", flat=True)[: self.table_row_label_sample_limit]
            )
            for label in labels:
                if not label:
                    continue
                tokens.update(self._table_tokenize(str(label)))
        self._table_row_label_cache[scope_key] = tokens
        if len(self._table_row_label_cache) > self.table_context_cache_limit:
            self._table_row_label_cache.popitem(last=False)
        return tokens
