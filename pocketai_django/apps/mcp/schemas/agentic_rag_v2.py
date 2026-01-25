"""
Agentic RAG V2 schemas (Phase 0 spec scaffolding).

V2 goal: a single, stable read interface for the LLM:
  read_document(items=[{id,cursor?}...], max_chars=...)

The tool decides retrieval strategy internally (page blocks vs structured tables
vs chunk windows) and returns deterministic continuation cursors when content
doesn't fit. Oversized outputs can be stored as artifacts with a small prompt_view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence


# -----------------------------------------------------------------------------
# Search response (V2 agentic view: minimal read knobs)
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchResultItemV2:
    id: str
    title: str
    type: Literal["table", "text"]
    source: str
    preview: str | None = None
    char_estimate: int = 0
    read_hint: dict | None = None  # e.g. {"suggested_max_chars": 12000}

    def as_dict(self) -> dict:
        out: dict = {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "source": self.source,
            "char_estimate": self.char_estimate,
        }
        if self.preview:
            out["preview"] = self.preview
        if self.read_hint:
            out["read_hint"] = dict(self.read_hint)
        return out


@dataclass(frozen=True)
class SearchResponseV2:
    status: Literal["ok", "empty", "duplicate", "throttled", "error"]
    results: tuple[SearchResultItemV2, ...] = field(default_factory=tuple)
    total_found: int = 0
    hint: str | None = None
    error_code: str | None = None

    def as_dict(self) -> dict:
        out: dict = {
            "status": self.status,
            "results": [item.as_dict() for item in self.results],
            "total_found": self.total_found,
        }
        if self.hint:
            out["hint"] = self.hint
        if self.error_code:
            out["error_code"] = self.error_code
        return out


# -----------------------------------------------------------------------------
# Read request/response (V2)
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadRequestItemV2:
    id: str
    cursor: str | None = None

    def as_dict(self) -> dict:
        out: dict = {"id": self.id}
        if self.cursor:
            out["cursor"] = self.cursor
        return out


@dataclass(frozen=True)
class ReadContentItemV2:
    id: str
    title: str
    type: Literal["table", "text"]
    content: str
    chars: int
    cursor_used: str | None = None
    next_cursor: str | None = None
    complete: bool = True

    def as_dict(self) -> dict:
        out: dict = {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "content": self.content,
            "chars": self.chars,
            "complete": self.complete,
        }
        if self.cursor_used:
            out["cursor_used"] = self.cursor_used
        if self.next_cursor:
            out["next_cursor"] = self.next_cursor
        return out


@dataclass(frozen=True)
class ReadTraceItemV2:
    id: str
    status: Literal["full", "partial", "artifact", "error", "deferred"]
    chars: int | None = None
    next_cursor: str | None = None
    artifact_id: str | None = None
    prompt_view: dict | None = None

    def as_dict(self) -> dict:
        out: dict = {"id": self.id, "status": self.status}
        if self.chars is not None:
            out["chars"] = self.chars
        if self.next_cursor:
            out["next_cursor"] = self.next_cursor
        if self.artifact_id:
            out["artifact_id"] = self.artifact_id
        if self.prompt_view:
            out["prompt_view"] = dict(self.prompt_view)
        return out


@dataclass(frozen=True)
class DeferredReadItemV2:
    id: str
    reason: str
    chars: int | None = None
    suggested_max_chars: int | None = None
    hint: str | None = None

    def as_dict(self) -> dict:
        out: dict = {"id": self.id, "reason": self.reason}
        if self.chars is not None:
            out["chars"] = self.chars
        if self.suggested_max_chars is not None:
            out["suggested_max_chars"] = self.suggested_max_chars
        if self.hint:
            out["hint"] = self.hint
        return out


@dataclass(frozen=True)
class ReadErrorItemV2:
    id: str
    error_code: str
    hint: str | None = None

    def as_dict(self) -> dict:
        out: dict = {"id": self.id, "error_code": self.error_code}
        if self.hint:
            out["hint"] = self.hint
        return out


@dataclass(frozen=True)
class ReadResponseV2:
    status: Literal["ok", "partial", "constraint_error", "throttled", "error"]
    contents: tuple[ReadContentItemV2, ...] = field(default_factory=tuple)
    read: tuple[ReadTraceItemV2, ...] = field(default_factory=tuple)
    deferred: tuple[DeferredReadItemV2, ...] = field(default_factory=tuple)
    errors: tuple[ReadErrorItemV2, ...] = field(default_factory=tuple)
    max_chars: int | None = None
    max_chars_allowed: int | None = None
    hint: str | None = None
    error_code: str | None = None

    def as_dict(self) -> dict:
        out: dict = {
            "status": self.status,
            "contents": [item.as_dict() for item in self.contents],
            "read": [item.as_dict() for item in self.read],
        }
        if self.deferred:
            out["deferred"] = [item.as_dict() for item in self.deferred]
        if self.errors:
            out["errors"] = [item.as_dict() for item in self.errors]
        if self.max_chars is not None:
            out["max_chars"] = self.max_chars
        if self.max_chars_allowed is not None:
            out["max_chars_allowed"] = self.max_chars_allowed
        if self.hint:
            out["hint"] = self.hint
        if self.error_code:
            out["error_code"] = self.error_code
        return out


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def build_search_response_v2(
    *,
    results: Sequence[SearchResultItemV2],
    total_found: int | None = None,
    hint: str | None = None,
) -> SearchResponseV2:
    items = tuple(results)
    return SearchResponseV2(
        status="ok" if items else "empty",
        results=items,
        total_found=total_found if total_found is not None else len(items),
        hint=hint if not items else None,
    )


def build_read_response_v2(
    *,
    status: Literal["ok", "partial"] = "ok",
    contents: Sequence[ReadContentItemV2] = (),
    read: Sequence[ReadTraceItemV2] = (),
    deferred: Sequence[DeferredReadItemV2] = (),
    errors: Sequence[ReadErrorItemV2] = (),
    max_chars: int | None = None,
    max_chars_allowed: int | None = None,
    hint: str | None = None,
) -> ReadResponseV2:
    return ReadResponseV2(
        status=status,
        contents=tuple(contents),
        read=tuple(read),
        deferred=tuple(deferred),
        errors=tuple(errors),
        max_chars=max_chars,
        max_chars_allowed=max_chars_allowed,
        hint=hint,
    )

