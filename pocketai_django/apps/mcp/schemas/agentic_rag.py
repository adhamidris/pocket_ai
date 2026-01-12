"""
Agentic RAG response schemas.

This module defines the clean 2-tool contract for LLM-driven retrieval:
- search_knowledge: returns metadata (IDs, titles, types, previews) — no full content
- read_document: returns full content for specified IDs

The LLM workflow is:
1. search_knowledge(query) → see what exists
2. read_document(ids, max_chars) → get content needed to answer
3. respond or search again with different terms
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal, Sequence


# -----------------------------------------------------------------------------
# Search Response (metadata + previews, no full content)
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchResultItem:
    """Single search result with metadata for LLM decision-making."""

    id: str  # chunk UUID
    document_id: str  # upload UUID (provenance only)
    title: str  # human-readable title
    type: Literal["table", "text"]  # content type
    source: str  # filename or document name
    preview: str | None = None  # short hint only; do not answer from preview
    read_id: str | None = None  # preferred id for read_document(ids)
    char_estimate: int = 0  # estimated chars if read (for token planning)
    row_count: int | None = None  # for tables: number of rows
    column_count: int | None = None  # for tables: number of columns
    table_id: str | None = None
    row_index: int | None = None
    read_hint: dict | None = None

    def as_dict(self) -> dict:
        result = {
            "id": self.id,
            "document_id": self.document_id,
            "title": self.title,
            "type": self.type,
            "source": self.source,
            "char_estimate": self.char_estimate,
        }
        if self.preview:
            result["preview"] = self.preview
        if self.read_id:
            result["read_id"] = self.read_id
        if self.type == "table":
            if self.row_count is not None:
                result["row_count"] = self.row_count
            if self.column_count is not None:
                result["column_count"] = self.column_count
            if self.table_id is not None:
                result["table_id"] = self.table_id
            if self.row_index is not None:
                result["row_index"] = self.row_index
        if self.read_hint:
            result["read_hint"] = self.read_hint
        return result


@dataclass(frozen=True)
class SearchResponse:
    """
    Search tool response — metadata only.

    The LLM uses this to decide what to read, not to answer directly.
    """

    status: Literal["ok", "empty", "error"]
    results: tuple[SearchResultItem, ...] = field(default_factory=tuple)
    total_found: int = 0
    hint: str | None = None  # guidance if empty: "Try different search terms"
    error: str | None = None

    def as_dict(self) -> dict:
        data: dict = {
            "status": self.status,
            "results": [r.as_dict() for r in self.results],
            "total_found": self.total_found,
        }
        if self.hint:
            data["hint"] = self.hint
        if self.error:
            data["error"] = self.error
        return data


# -----------------------------------------------------------------------------
# Read Response (full content)
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadContentItem:
    """Single read result with full content."""

    id: str  # chunk UUID
    title: str
    content: str  # full text/table content
    type: Literal["table", "text"]
    truncated: bool = False  # True if content was cut due to max_chars

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "type": self.type,
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class ReadResponse:
    """
    Read tool response — full content.

    The LLM uses this content to formulate the answer.
    """

    status: Literal["ok", "partial", "error"]
    contents: tuple[ReadContentItem, ...] = field(default_factory=tuple)
    total_chars: int = 0
    truncated_ids: tuple[str, ...] = field(default_factory=tuple)  # IDs that hit char limit
    error: str | None = None

    def as_dict(self) -> dict:
        data: dict = {
            "status": self.status,
            "contents": [c.as_dict() for c in self.contents],
            "total_chars": self.total_chars,
        }
        if self.truncated_ids:
            data["truncated_ids"] = list(self.truncated_ids)
        if self.error:
            data["error"] = self.error
        return data


# -----------------------------------------------------------------------------
# Helpers for building responses
# -----------------------------------------------------------------------------


def build_search_response(
    *,
    results: Sequence[SearchResultItem],
    total_found: int | None = None,
    hint: str | None = None,
) -> SearchResponse:
    """Build a successful search response."""
    items = tuple(results)
    return SearchResponse(
        status="ok" if items else "empty",
        results=items,
        total_found=total_found if total_found is not None else len(items),
        hint=hint if not items else None,
    )


def build_read_response(
    *,
    contents: Sequence[ReadContentItem],
    truncated_ids: Sequence[str] | None = None,
) -> ReadResponse:
    """Build a successful read response."""
    items = tuple(contents)
    total_chars = sum(len(c.content) for c in items)
    truncated = tuple(truncated_ids) if truncated_ids else tuple()
    return ReadResponse(
        status="partial" if truncated else "ok",
        contents=items,
        total_chars=total_chars,
        truncated_ids=truncated,
    )


def build_error_response(error: str, *, tool: Literal["search", "read"]) -> dict:
    """Build an error response for either tool."""
    return {
        "status": "error",
        "error": error,
        "results" if tool == "search" else "contents": [],
    }
