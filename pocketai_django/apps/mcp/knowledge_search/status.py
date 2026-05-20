from __future__ import annotations

from typing import Mapping, Sequence


def _normalized_run_status(run: Mapping[str, object] | None) -> str:
    if not isinstance(run, Mapping):
        return ""
    return str(run.get("status") or "").strip().lower()


def _select_final_status(
    *,
    runs: Sequence[Mapping[str, object]],
    primary_run: Mapping[str, object],
    page_snippets: Sequence[object],
) -> tuple[str, Mapping[str, object]]:
    # Agentic RAG should not block on "needs_clarification". Always return best-effort evidence
    # and let the assistant handle ambiguity/conflicts in the response.
    status_source_run: Mapping[str, object] = next(
        (run for run in runs if _normalized_run_status(run) == "ok"),
        primary_run,
    )
    if page_snippets:
        return "ok", status_source_run

    non_default_status_run = next(
        (
            run
            for run in runs
            if _normalized_run_status(run) not in {"", "not_found", "needs_clarification"}
        ),
        None,
    )
    if non_default_status_run is not None:
        return _normalized_run_status(non_default_status_run), non_default_status_run

    status_source_run = runs[-1]
    final_status = _normalized_run_status(status_source_run) or "not_found"
    if final_status == "needs_clarification":
        final_status = "not_found"
    return final_status, status_source_run
