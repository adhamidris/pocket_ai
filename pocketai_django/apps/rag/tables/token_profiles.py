from __future__ import annotations

import uuid
from collections import Counter
from typing import Sequence

from django.db.models import Q

from apps.accounts.models import KnowledgeVisibility
from apps.knowledge.models import KnowledgeUploadTable
from apps.rag.tables.semantics import normalize_column_name


class TableTokenProfileMixin:

    def _table_generic_tokens_for_business(
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
        cached = self._table_generic_token_cache.get(scope_key)
        if cached is not None:
            self._table_generic_token_cache.move_to_end(scope_key)
            return cached
        qs = KnowledgeUploadTable.objects.filter(upload__business_profile=business_profile).exclude(
            upload__visibility=KnowledgeVisibility.INTERNAL,
        )
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                self._table_generic_token_cache[scope_key] = set()
                return set()
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
        rows = list(
            qs.order_by("-updated_at").values_list(
                "column_schema",
                "title",
                "section_heading",
                "upload__display_name",
                "upload__source_name",
                "upload__external_reference",
            )[: self.table_column_sample_limit]
        )
        total_tables = len(rows)
        if not total_tables:
            self._table_generic_token_cache[scope_key] = set()
            return set()
        required_tables = min(self.table_generic_min_tables, total_tables)
        df: Counter[str] = Counter()
        for column_schema, title, section_heading, display_name, source_name, external_reference in rows:
            tokens: set[str] = set()
            for value in column_schema or []:
                normalized = normalize_column_name(str(value))
                tokens.update(self._table_tokenize(normalized or str(value)))
            for value in (title, section_heading, display_name, source_name, external_reference):
                if not value:
                    continue
                normalized = normalize_column_name(str(value))
                tokens.update(self._table_tokenize(normalized or str(value)))
            for token in tokens:
                if not token or token.isdigit():
                    continue
                if len(token) < self.table_specific_min_length:
                    continue
                df[token] += 1
        generic: set[str] = set()
        if total_tables:
            for token, count in df.items():
                if count < required_tables:
                    continue
                if (count / total_tables) >= self.table_generic_df_threshold:
                    generic.add(token)
        if self.table_generic_topk > 0 and df:
            # Only treat frequently-occurring tokens as "generic".
            # The previous behavior could swallow rare but critical entity tokens
            # when `table_generic_topk` is large relative to the number of tables.
            for token, count in df.most_common(self.table_generic_topk):
                if count < required_tables:
                    continue
                generic.add(token)
        self._table_generic_token_cache[scope_key] = generic
        if len(self._table_generic_token_cache) > self.table_header_token_cache_limit:
            self._table_generic_token_cache.popitem(last=False)
        return generic

    def _table_header_tokens(self, table_id: str | uuid.UUID | None) -> set[str]:
        if not table_id:
            return set()
        cache_key = str(table_id).strip()
        if not cache_key:
            return set()
        cached = self._table_header_token_cache.get(cache_key)
        if cached is not None:
            self._table_header_token_cache.move_to_end(cache_key)
            return cached
        parsed_table_id: uuid.UUID | None = None
        if isinstance(table_id, uuid.UUID):
            parsed_table_id = table_id
        else:
            try:
                parsed_table_id = uuid.UUID(cache_key)
            except (TypeError, ValueError, AttributeError):
                self._table_header_token_cache[cache_key] = set()
                return set()
        payload = (
            KnowledgeUploadTable.objects.filter(id=parsed_table_id)
            .values("column_schema", "title", "section_heading")
            .first()
        )
        tokens: set[str] = set()
        if payload:
            for value in payload.get("column_schema") or []:
                normalized = normalize_column_name(str(value))
                tokens.update(self._table_tokenize(normalized or str(value)))
            for value in (payload.get("title"), payload.get("section_heading")):
                if value:
                    normalized = normalize_column_name(str(value))
                    tokens.update(self._table_tokenize(normalized or str(value)))
        self._table_header_token_cache[cache_key] = tokens
        if len(self._table_header_token_cache) > self.table_header_token_cache_limit:
            self._table_header_token_cache.popitem(last=False)
        return tokens
