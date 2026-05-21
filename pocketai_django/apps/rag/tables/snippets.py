from __future__ import annotations

from apps.rag.tables.direct_snippets import TableDirectSnippetMixin
from apps.rag.tables.fallback_snippets import TableFallbackSnippetMixin


class TableSnippetMixin(TableDirectSnippetMixin, TableFallbackSnippetMixin):
    pass
