from __future__ import annotations

from typing import Mapping, Sequence


def _clip_text(value: object, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)].rstrip() + "..."


def _compact_refs(refs: object, *, limit: int = 5) -> list[dict[str, object]]:
    if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes, bytearray)):
        return []
    compacted: list[dict[str, object]] = []
    seen: set[str] = set()
    for ref in refs:
        if not isinstance(ref, Mapping):
            continue
        ref_id = str(ref.get("id") or ref.get("ref") or "").strip()
        if not ref_id or ref_id in seen:
            continue
        seen.add(ref_id)
        entry: dict[str, object] = {"id": ref_id}
        label = str(ref.get("label") or ref.get("title") or "").strip()
        if label:
            entry["label"] = _clip_text(label, limit=140)
        kind = str(ref.get("kind") or "").strip().lower()
        if kind:
            entry["kind"] = kind
        ref_type = str(ref.get("type") or "").strip().lower()
        if ref_type:
            entry["type"] = ref_type
        document_id = str(ref.get("document_id") or ref.get("upload_id") or "").strip()
        if document_id:
            entry["document_id"] = document_id
        compacted.append(entry)
        if len(compacted) >= max(1, int(limit)):
            break
    return compacted


def _available_refs(context: object | None, *, limit: int = 5) -> list[dict[str, object]]:
    if context is None:
        return []
    for attr in ("recent_search_refs", "model_visible_refs", "retrieval_candidates"):
        refs = _compact_refs(getattr(context, attr, None), limit=limit)
        if refs:
            return refs
    return []


def build_search_budget_guidance(
    context: object | None,
    *,
    reason: str = "search_budget_exceeded",
) -> dict[str, object]:
    refs = _available_refs(context, limit=5)
    read_evidence = getattr(context, "read_evidence", None) if context is not None else None
    read_evidence_count = len(read_evidence) if isinstance(read_evidence, list) else 0

    next_actions: list[dict[str, object]] = []
    if refs:
        next_actions.append(
            {
                "action": "read_existing_refs",
                "tool": "read_knowledge",
                "when": "Use the relevant refs already returned by search_knowledge instead of searching again.",
                "refs": refs,
            }
        )
    next_actions.append(
        {
            "action": "answer_from_available_evidence",
            "when": "Use this when the existing refs, previews, prefetched evidence, or reads answer the user.",
        }
    )
    next_actions.append(
        {
            "action": "ask_clarification",
            "when": "Use this when the available evidence is unrelated, missing, or not enough to answer safely.",
        }
    )

    hint = (
        "Search budget is exhausted for this turn. Do not call search_knowledge again. "
        "Use read_knowledge on existing refs if they are relevant; otherwise answer from available evidence "
        "or ask one concise clarification question."
    )
    return {
        "reason": reason,
        "message": hint,
        "available_refs_count": len(refs),
        "available_read_evidence_count": read_evidence_count,
        "next_actions": next_actions,
    }


def search_budget_exceeded_payload(
    context: object | None,
    *,
    reason: str = "per_turn_limit",
) -> dict[str, object]:
    guidance = build_search_budget_guidance(context, reason=reason)
    return {
        "tool": "search_knowledge",
        "status": "blocked",
        "error": "search_unavailable",
        "error_code": "search_budget_exceeded",
        "snippets": [],
        "refs": [],
        "hint": str(guidance.get("message") or ""),
        "next_action": "read_existing_refs_or_answer_or_ask_clarification",
        "budget_guidance": guidance,
    }


def build_repeat_search_guidance(
    context: object | None,
    *,
    similarity: float | None = None,
) -> dict[str, object]:
    refs = _available_refs(context, limit=5)
    guidance: dict[str, object] = {
        "reason": "repeated_equivalent_search",
        "message": (
            "This search is very similar to a previous search this turn. Before searching again, "
            "read relevant existing refs or answer from the evidence already collected."
        ),
        "available_refs_count": len(refs),
        "next_actions": [
            {
                "action": "read_existing_refs",
                "tool": "read_knowledge",
                "when": "Use if the existing refs are relevant to the user question.",
            },
            {
                "action": "answer_from_available_evidence",
                "when": "Use if previews or prefetched evidence already answer the question.",
            },
        ],
    }
    if similarity is not None:
        guidance["similarity"] = round(float(similarity), 4)
    if refs:
        guidance["refs"] = refs
    return guidance
