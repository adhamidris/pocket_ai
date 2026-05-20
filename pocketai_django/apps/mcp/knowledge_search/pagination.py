from __future__ import annotations

from typing import Mapping, Sequence

from ..types import ToolExecutionContext


def _excluded_chunk_ids(*, context: ToolExecutionContext, exclude_seen: bool) -> set[str]:
    if not exclude_seen:
        return set()
    try:
        shown = context.get_all_shown_this_conversation()
        chunk_ids = shown.get("chunk_ids", set())
        if isinstance(chunk_ids, set):
            return {str(cid) for cid in chunk_ids if cid}
    except Exception:
        pass
    # Best-effort fallback.
    return {
        str(cid)
        for cid in (getattr(context, "seen_chunk_ids", set()) | getattr(context, "newly_shown_chunk_ids", set()))
        if cid
    }

def _page_snippets(
    snippets: Sequence[Mapping[str, object]],
    *,
    offset: int,
    page_size: int,
    excluded_chunk_ids: set[str],
) -> tuple[list[dict[str, object]], int, bool, int]:
    out: list[dict[str, object]] = []
    excluded = 0
    idx = max(0, int(offset))
    size = max(1, int(page_size))

    def _chunk_id(entry: Mapping[str, object]) -> str:
        return str(entry.get("chunk_id") or entry.get("id") or "").strip()

    while idx < len(snippets) and len(out) < size:
        entry = snippets[idx]
        idx += 1
        if not isinstance(entry, Mapping):
            continue
        cid = _chunk_id(entry)
        if cid and cid in excluded_chunk_ids:
            excluded += 1
            continue
        out.append(dict(entry))

    has_more = False
    if idx < len(snippets):
        if not excluded_chunk_ids:
            has_more = True
        else:
            for j in range(idx, len(snippets)):
                entry = snippets[j]
                if not isinstance(entry, Mapping):
                    continue
                cid = _chunk_id(entry)
                if cid and cid not in excluded_chunk_ids:
                    has_more = True
                    break

    return out, idx, has_more, excluded

def _read_budget_for_refs(
    refs: Sequence[Mapping[str, object]],
    *,
    max_chars_allowed: int,
) -> dict[str, int] | None:
    if not refs:
        return None
    total_suggested = 0
    for ref in refs:
        if not isinstance(ref, Mapping):
            continue
        try:
            total_suggested += int(ref.get("read_chars") or 0)
        except (TypeError, ValueError):
            continue
    return {
        "suggested_chars": min(int(total_suggested), int(max_chars_allowed)),
        "max_chars": int(max_chars_allowed),
    }
