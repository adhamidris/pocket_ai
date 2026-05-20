from __future__ import annotations

from typing import Mapping, Sequence

from apps.knowledge.models import KnowledgeUpload

from core.tenancy import tenant_context


def _snippet_count(payload: Mapping[str, object] | None) -> int:
    if not isinstance(payload, Mapping):
        return 0
    snippets = payload.get("snippets")
    if isinstance(snippets, Sequence) and not isinstance(snippets, (str, bytes, bytearray)):
        return len(snippets)
    refs = payload.get("refs")
    if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes, bytearray)):
        return len(refs)
    results = payload.get("results")
    if isinstance(results, Sequence) and not isinstance(results, (str, bytes, bytearray)):
        return len(results)
    contents = payload.get("contents")
    if isinstance(contents, Sequence) and not isinstance(contents, (str, bytes, bytearray)):
        return len(contents)
    evidence = payload.get("evidence")
    if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes, bytearray, Mapping)):
        return len(evidence)
    if isinstance(evidence, Mapping):
        snippets = evidence.get("snippets")
        if isinstance(snippets, Sequence) and not isinstance(snippets, (str, bytes, bytearray)):
            return len(snippets)
    return 0

def _resolve_read_label(
    arguments: Mapping[str, object],
    *,
    business_id: object,
    action_verb: str,
) -> str:
    """
    Best-effort label for read operations that prefers a human document name
    over opaque UUIDs (especially when read operations are called with ids/refs).
    """
    resolved_title = ""
    ids = arguments.get("ids")
    if business_id and isinstance(ids, Sequence) and not isinstance(ids, (str, bytes, bytearray)) and ids:
        try:
            from apps.knowledge.models import KnowledgeUploadChunk
        except Exception:
            KnowledgeUploadChunk = None  # type: ignore[assignment]
        if KnowledgeUploadChunk is not None:
            first_id = str(ids[0]).strip()
            if first_id:
                try:
                    with tenant_context(business_id):
                        resolved_title = (
                            KnowledgeUploadChunk.objects.filter(
                                id=first_id,
                                business_profile_id=business_id,
                            )
                            .values_list("upload__display_name", flat=True)
                            .first()
                            or ""
                        )
                except Exception:
                    resolved_title = ""

    raw_doc_id = arguments.get("document_id")
    doc_id = str(raw_doc_id).strip() if raw_doc_id is not None else ""
    if business_id and doc_id and not resolved_title:
        try:
            with tenant_context(business_id):
                resolved_title = (
                    KnowledgeUpload.objects.filter(id=doc_id, business_profile_id=business_id)
                    .values_list("display_name", flat=True)
                    .first()
                    or ""
                )
        except Exception:
            resolved_title = ""
    if business_id and doc_id and not resolved_title:
        # Some call paths pass a chunk id as document_id; resolve back to the upload name.
        try:
            from apps.knowledge.models import KnowledgeUploadChunk
        except Exception:
            KnowledgeUploadChunk = None  # type: ignore[assignment]
        if KnowledgeUploadChunk is not None:
            try:
                with tenant_context(business_id):
                    resolved_title = (
                        KnowledgeUploadChunk.objects.filter(
                            id=doc_id,
                            business_profile_id=business_id,
                        )
                        .values_list("upload__display_name", flat=True)
                        .first()
                        or ""
                    )
            except Exception:
                resolved_title = ""

    resolved_title = str(resolved_title or "").strip()
    if resolved_title:
        return f"{action_verb} {resolved_title[:80]}"
    return ""
