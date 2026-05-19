"""
Search result fusion helpers for MCP knowledge search.
"""

from __future__ import annotations

import json
from typing import Mapping, Sequence

from apps.knowledge.privacy_tools.hashing import sha256_hex


def _fuse_batched_search_runs(
    runs: Sequence[Mapping[str, object]],
    *,
    clip_limit: int | None,
) -> tuple[list[dict[str, object]], dict[str, object] | None]:
    def _snippet_dedupe_key(snippet: Mapping[str, object]) -> str:
        evidence_group_id = str(snippet.get("evidence_group_id") or "").strip()
        if evidence_group_id:
            return f"evidence:{evidence_group_id}"
        chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "").strip()
        if chunk_id:
            return f"chunk:{chunk_id}"
        upload_id = str(snippet.get("upload_id") or "").strip()
        if upload_id:
            return f"upload:{upload_id}"
        content = snippet.get("content")
        if isinstance(content, str) and content.strip():
            return f"content:{sha256_hex(content)}"
        summary = snippet.get("summary")
        if isinstance(summary, str) and summary.strip():
            return f"summary:{sha256_hex(summary)}"
        return json.dumps(snippet, sort_keys=True, default=str)

    if not runs:
        return [], None
    if len(runs) == 1:
        snippets = [
            dict(snippet)
            for snippet in runs[0].get("snippets", [])
            if isinstance(snippet, Mapping)
        ]
        if clip_limit:
            snippets = snippets[:clip_limit]
        return snippets, None

    fusion = {"method": "rrf_dedupe", "runs": len(runs)}
    fused: dict[str, dict[str, object]] = {}
    # Use rank fusion across query variants so later variants can still surface
    # the best shared evidence before we clip to the outward limit.
    rrf_k = 60.0
    for run_index, run in enumerate(runs):
        snippets = run.get("snippets", [])
        if not isinstance(snippets, Sequence) or isinstance(snippets, (str, bytes, bytearray)):
            continue
        for rank, snippet in enumerate(snippets):
            if not isinstance(snippet, Mapping):
                continue
            dedup_key = _snippet_dedupe_key(snippet)
            raw_confidence = snippet.get("confidence_score")
            try:
                confidence = float(raw_confidence) if raw_confidence is not None else 0.0
            except (TypeError, ValueError):
                confidence = 0.0
            entry = fused.get(dedup_key)
            rrf_increment = 1.0 / (rrf_k + float(rank) + 1.0)
            if entry is None:
                fused[dedup_key] = {
                    "snippet": dict(snippet),
                    "rrf_score": rrf_increment,
                    "best_confidence": confidence,
                    "best_rank": int(rank),
                    "best_run_index": int(run_index),
                }
                continue
            entry["rrf_score"] = float(entry.get("rrf_score") or 0.0) + rrf_increment
            if confidence > float(entry.get("best_confidence") or 0.0):
                entry["best_confidence"] = confidence
                entry["snippet"] = dict(snippet)
                entry["best_rank"] = int(rank)
                entry["best_run_index"] = int(run_index)
            elif confidence == float(entry.get("best_confidence") or 0.0):
                prior_run = int(entry.get("best_run_index") or 0)
                prior_rank = int(entry.get("best_rank") or 0)
                if (run_index, rank) < (prior_run, prior_rank):
                    entry["snippet"] = dict(snippet)
                    entry["best_rank"] = int(rank)
                    entry["best_run_index"] = int(run_index)

    ordered_entries = sorted(
        fused.values(),
        key=lambda entry: (
            -float(entry.get("rrf_score") or 0.0),
            -float(entry.get("best_confidence") or 0.0),
            int(entry.get("best_run_index") or 0),
            int(entry.get("best_rank") or 0),
        ),
    )
    if clip_limit is not None:
        ordered_entries = ordered_entries[:clip_limit]
    deduped_snippets = [dict(entry["snippet"]) for entry in ordered_entries]
    return deduped_snippets, fusion
