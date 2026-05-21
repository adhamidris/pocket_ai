from __future__ import annotations

import uuid
from typing import Mapping, Sequence

from django.db.models import Q

from apps.accounts.models import KnowledgeVisibility
from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadTable


class TableContextSupportMixin:

    def _table_row_result_cap_for_business(self, business_profile, requested: int | None = None) -> int:
        """
        Resolve how many table rows we can consider before final ranking.

        Requested `limit` remains the primary driver. Business overrides and
        default caps act as floors for candidate collection, not hard ceilings.
        """
        requested_cap = 0
        if requested is not None:
            try:
                requested_cap = max(1, int(requested))
            except (TypeError, ValueError):
                requested_cap = 0
        base = max(1, int(self.table_result_cap))
        if not business_profile:
            return max(base, requested_cap)
        override = self._business_override(business_profile, "table_results_limit", base)
        try:
            override_cap = max(1, int(override))
        except (TypeError, ValueError):
            override_cap = base
        return max(base, override_cap, requested_cap)

    def _table_ingestion_diagnostics(self, upload: KnowledgeUpload | None) -> dict[str, object]:
        diagnostics: dict[str, object] = {
            "table_truncated": False,
            "total_rows": None,
            "indexed_rows": None,
            "row_cap": None,
            "partial_tables": None,
            "partial_index": False,
            "truncated_rows": None,
            "truncated_columns": None,
            "truncated_tables": None,
        }
        if not upload:
            return diagnostics
        metadata = getattr(upload, "ingestion_metadata", None)
        if isinstance(metadata, Mapping):
            table_stats = metadata.get("table_stats")
            if isinstance(table_stats, Mapping):
                diagnostics["total_rows"] = table_stats.get("total_rows")
                diagnostics["indexed_rows"] = table_stats.get("indexed_rows")
                diagnostics["row_cap"] = table_stats.get("row_cap")
                diagnostics["partial_tables"] = table_stats.get("partial_tables")
                diagnostics["partial_index"] = bool(table_stats.get("partial_index"))
            table_truncation = metadata.get("table_truncation")
            if isinstance(table_truncation, Mapping):
                diagnostics["truncated_rows"] = table_truncation.get("truncated_rows")
                diagnostics["truncated_columns"] = table_truncation.get("truncated_columns")
                diagnostics["truncated_tables"] = table_truncation.get("truncated_tables")
        indexed_rows = self._coerce_int(diagnostics.get("indexed_rows"))
        total_rows = self._coerce_int(diagnostics.get("total_rows"))
        truncated_rows = self._coerce_int(diagnostics.get("truncated_rows"))
        truncated_tables = self._coerce_int(diagnostics.get("truncated_tables"))
        partial_tables = self._coerce_int(diagnostics.get("partial_tables"))
        partial_index = bool(diagnostics.get("partial_index"))
        table_truncated = (
            truncated_rows > 0
            or truncated_tables > 0
            or partial_tables > 0
            or partial_index
            or (indexed_rows and total_rows and indexed_rows < total_rows)
        )
        diagnostics["table_truncated"] = table_truncated
        return diagnostics

    def _table_column_hints(self, business_profile) -> set[str]:
        hints = set(self.table_column_hint_base)
        metadata = getattr(business_profile, "metadata", None)
        overrides = metadata.get(self.business_override_key) if isinstance(metadata, dict) else None
        if isinstance(overrides, dict):
            extra = overrides.get("table_column_hints")
            if isinstance(extra, (list, tuple, set)):
                hints.update(str(item).strip().lower() for item in extra if str(item).strip())
        return hints

    def _filter_queryable_table_uploads(self, qs, *, format_lookup: str):
        """
        Keep rows where the upload format is missing/NULL or not in non-queryable formats.

        We use a positive filter (IS NULL OR NOT IN) instead of exclude(IN) because
        SQL NULL semantics can otherwise drop rows where the JSON key is absent.
        """
        if not self.non_queryable_table_formats:
            return qs
        formats = sorted(self.non_queryable_table_formats)
        return qs.filter(Q(**{f"{format_lookup}__isnull": True}) | ~Q(**{f"{format_lookup}__in": formats}))

    def _business_has_tables(
        self,
        business_profile,
        cached_columns: set[str] | None = None,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> bool:
        business_id = getattr(business_profile, "id", None)
        if not business_id:
            return False
        if cached_columns is not None and cached_columns:
            return True
        scope_key = (
            business_id,
            self._upload_scope_token(
                allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            ),
        )
        cached = self._table_presence_cache.get(scope_key)
        if cached is not None:
            self._table_presence_cache.move_to_end(scope_key)
            return cached
        qs = KnowledgeUploadTable.objects.filter(upload__business_profile=business_profile).exclude(
            upload__visibility=KnowledgeVisibility.INTERNAL,
        )
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                self._table_presence_cache[scope_key] = False
                self._table_presence_cache.move_to_end(scope_key)
                if len(self._table_presence_cache) > self.table_column_cache_limit:
                    self._table_presence_cache.popitem(last=False)
                return False
            qs = qs.filter(upload_id__in=allowed_upload_ids)
        else:
            clauses: list[Q] = []
            if allowed_explicit_upload_ids:
                clauses.append(Q(upload_id__in=allowed_explicit_upload_ids))
            if clauses:
                clause = clauses[0]
                for extra in clauses[1:]:
                    clause |= extra
                qs = qs.filter(clause)
        qs = self._filter_queryable_table_uploads(qs, format_lookup="upload__ingestion_metadata__format")
        exists = qs.exists()
        self._table_presence_cache[scope_key] = exists
        self._table_presence_cache.move_to_end(scope_key)
        if len(self._table_presence_cache) > self.table_column_cache_limit:
            self._table_presence_cache.popitem(last=False)
        return exists
