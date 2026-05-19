from __future__ import annotations

CONTEXT_STATUS_CODES = {
    "searching_knowledge",
    "searching_start",
    "searching_complete",
    "reading_document",
    "reading_start",
    "reading_complete",
    "planning_actions",
    "responding",
    "answer_started",
    "answer_finalized",
    "clarifying",
}

TOOL_EVENT_PHASES = {"started", "finished", "approval_requested", "approval_resolved"}


def _queue_put(queue, item):
    put = getattr(queue, "put", None)
    if callable(put):
        put(item)
    else:
        queue.append(item)


def _enqueue_status_events(queue, *, code: str, label: str | None = None, meta: dict | None = None) -> None:
    code_value = (code or "").strip()
    if not code_value:
        return
    label_value = label or code_value.replace("_", " ").title()
    payload: dict[str, object] = {"type": "status", "state": code_value, "label": label_value}
    if meta:
        payload["meta"] = meta
    if code_value in CONTEXT_STATUS_CODES:
        ctx_payload = {"type": "context_progress", "state": code_value, "label": label_value}
        if meta:
            ctx_payload["meta"] = meta
        _queue_put(queue, ctx_payload)
    _queue_put(queue, payload)
