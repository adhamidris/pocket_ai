from __future__ import annotations

import uuid
from typing import Mapping, Sequence

from django.db.models import Q

from apps.accounts.models import KnowledgeStatus, KnowledgeVisibility
from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadTable
from apps.rag.tables.row_labels import TableRowLabelMixin


class TableProfileMixin(TableRowLabelMixin):

    def _table_columns_for_business(
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
        cached = self._table_column_cache.get(scope_key)
        if cached is not None:
            self._table_column_cache.move_to_end(scope_key)
            return cached
        columns: set[str] = set()
        uploads_qs = KnowledgeUpload.objects.filter(
            business_profile=business_profile,
            status=KnowledgeStatus.ACTIVE,
        ).exclude(visibility=KnowledgeVisibility.INTERNAL)
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                self._table_column_cache[scope_key] = set()
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
                : self.table_column_sample_limit
            ]
        )
        for profile in profiles:
            if not isinstance(profile, Mapping):
                continue
            for column in profile.get("columns") or []:
                lowered = str(column).strip().lower()
                if lowered:
                    columns.add(lowered)
        if not columns:
            qs = KnowledgeUploadTable.objects.filter(upload__business_profile=business_profile).exclude(
                upload__visibility=KnowledgeVisibility.INTERNAL,
            )
            if allowed_upload_ids is not None:
                qs = qs.filter(upload_id__in=allowed_upload_ids)
            else:
                clauses = []
                if allowed_explicit_upload_ids:
                    clauses.append(Q(upload_id__in=allowed_explicit_upload_ids))
                if clauses:
                    clause = clauses[0]
                    for extra in clauses[1:]:
                        clause |= extra
                    qs = qs.filter(clause)
            qs = self._filter_queryable_table_uploads(qs, format_lookup="upload__ingestion_metadata__format")
            qs = qs.order_by("-updated_at").values_list("column_schema", flat=True)[: self.table_column_sample_limit]
            for schema in qs:
                if not isinstance(schema, (list, tuple)):
                    continue
                for column in schema:
                    if not column:
                        continue
                    lowered = str(column).strip().lower()
                    if lowered:
                        columns.add(lowered)
        self._table_column_cache[scope_key] = columns
        if len(self._table_column_cache) > self.table_column_cache_limit:
            self._table_column_cache.popitem(last=False)
        return columns

    def _table_profile_for_business(
        self,
        business_profile,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> dict[str, object]:
        business_id = getattr(business_profile, "id", None)
        if not business_id:
            return {
                "table_uploads": 0,
                "total_uploads": 0,
                "table_upload_ratio": 0.0,
                "table_count": 0,
                "dominant": False,
            }
        scope_key = (
            business_id,
            self._upload_scope_token(
                allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            ),
        )
        cached = self._table_context_cache.get(scope_key)
        if cached is not None:
            self._table_context_cache.move_to_end(scope_key)
            return cached

        uploads_qs = KnowledgeUpload.objects.filter(
            business_profile=business_profile,
            status=KnowledgeStatus.ACTIVE,
        ).exclude(visibility=KnowledgeVisibility.INTERNAL)
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                profile = {
                    "table_uploads": 0,
                    "total_uploads": 0,
                    "table_upload_ratio": 0.0,
                    "table_count": 0,
                    "dominant": False,
                }
                self._table_context_cache[scope_key] = profile
                if len(self._table_context_cache) > self.table_context_cache_limit:
                    self._table_context_cache.popitem(last=False)
                return profile
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
        total_uploads = uploads_qs.count()

        table_qs = KnowledgeUploadTable.objects.filter(
            upload__business_profile=business_profile,
        ).exclude(upload__visibility=KnowledgeVisibility.INTERNAL)
        if allowed_upload_ids is not None:
            table_qs = table_qs.filter(upload_id__in=allowed_upload_ids)
        else:
            clauses = []
            if allowed_explicit_upload_ids:
                clauses.append(Q(upload_id__in=allowed_explicit_upload_ids))
            if clauses:
                clause = clauses[0]
                for extra in clauses[1:]:
                    clause |= extra
                table_qs = table_qs.filter(clause)
        table_qs = self._filter_queryable_table_uploads(
            table_qs,
            format_lookup="upload__ingestion_metadata__format",
        )
        table_uploads = table_qs.values("upload_id").distinct().count()
        table_count = table_qs.count()
        upload_ratio = (table_uploads / total_uploads) if total_uploads else 0.0
        dominant = bool(
            table_count >= self.table_dominant_min_tables
            and upload_ratio >= self.table_dominant_upload_ratio
        )
        profile = {
            "table_uploads": table_uploads,
            "total_uploads": total_uploads,
            "table_upload_ratio": round(upload_ratio, 4),
            "table_count": table_count,
            "dominant": dominant,
        }
        self._table_context_cache[scope_key] = profile
        if len(self._table_context_cache) > self.table_context_cache_limit:
            self._table_context_cache.popitem(last=False)
        return profile
