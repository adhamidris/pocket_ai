from __future__ import annotations

from typing import Mapping, Sequence

from ..types import ToolExecutionContext


def _extract_agentic_manifests(
    refs: Sequence[Mapping[str, object]],
    *,
    context: ToolExecutionContext,
) -> dict[str, dict[str, object]]:
    """
    Persist only the manifests needed to resolve table_chunk refs.

    These manifests live on ToolExecutionContext and are populated by
    _convert_to_agentic_search_response. Cursor paging needs them to be
    present so read_knowledge can hydrate table anchors without requiring a re-search.
    """
    table_manifests: dict[str, dict[str, object]] = {}
    table_cache = getattr(context, "table_row_anchor_manifests", None)
    if not isinstance(table_cache, dict):
        return table_manifests

    for ref in refs:
        if not isinstance(ref, Mapping):
            continue
        kind = str(ref.get("kind") or "").strip().lower()
        ref_id = str(ref.get("id") or "").strip()
        if not ref_id:
            continue
        if kind == "table_chunk" and isinstance(table_cache, dict):
            manifest = table_cache.get(ref_id)
            if isinstance(manifest, Mapping):
                table_manifests[ref_id] = dict(manifest)
    return table_manifests
