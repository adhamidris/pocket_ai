from __future__ import annotations

from typing import Mapping, Sequence


class ToolContextRecentRefsMixin:

    @staticmethod
    def _clip_text(value: object, *, limit: int) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(1, limit - 3)].rstrip() + "..."

    @staticmethod
    def _normalize_recent_ref_entry(ref: Mapping[str, object]) -> dict[str, object] | None:
        ref_id = str(ref.get("id") or "").strip()
        if not ref_id:
            return None
        normalized: dict[str, object] = {"id": ref_id}
        label = str(ref.get("label") or ref.get("title") or "").strip()
        if label:
            normalized["label"] = ToolContextRecentRefsMixin._clip_text(label, limit=180)
        document = str(ref.get("document") or ref.get("document_name") or "").strip()
        if document:
            normalized["document"] = ToolContextRecentRefsMixin._clip_text(document, limit=180)
        kind = str(ref.get("kind") or "").strip().lower()
        if kind:
            normalized["kind"] = kind
        ref_type = str(ref.get("type") or "").strip().lower()
        if ref_type:
            normalized["type"] = ref_type
        document_id = str(ref.get("document_id") or ref.get("upload_id") or "").strip()
        if document_id:
            normalized["document_id"] = document_id
        preview = str(ref.get("preview") or "").strip()
        if preview:
            normalized["preview"] = ToolContextRecentRefsMixin._clip_text(preview, limit=220)
        return normalized

    def set_recent_search_refs(self, refs: Sequence[Mapping[str, object]] | None, *, limit: int = 12) -> None:
        normalized: list[dict[str, object]] = []
        seen_ids: set[str] = set()
        for item in refs or ():
            if not isinstance(item, Mapping):
                continue
            normalized_item = self._normalize_recent_ref_entry(item)
            if not normalized_item:
                continue
            ref_id = str(normalized_item.get("id") or "").strip()
            if not ref_id or ref_id in seen_ids:
                continue
            seen_ids.add(ref_id)
            normalized.append(normalized_item)
            if len(normalized) >= max(1, int(limit)):
                break
        self.recent_search_refs = normalized
        self.recent_search_refs_updated = True

    def get_recent_search_refs_for_persistence(self) -> dict[str, object]:
        refs = [dict(item) for item in (self.recent_search_refs or [])][:12]
        return {"refs": refs}

    def hydrate_recent_search_refs(self, persisted: Mapping[str, object] | Sequence[Mapping[str, object]] | None) -> None:
        refs_payload: Sequence[Mapping[str, object]] | None = None
        if isinstance(persisted, Mapping):
            refs = persisted.get("refs")
            if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes, bytearray)):
                refs_payload = refs  # type: ignore[assignment]
        elif isinstance(persisted, Sequence) and not isinstance(persisted, (str, bytes, bytearray)):
            refs_payload = persisted  # type: ignore[assignment]

        normalized: list[dict[str, object]] = []
        seen_ids: set[str] = set()
        for item in refs_payload or ():
            if not isinstance(item, Mapping):
                continue
            normalized_item = self._normalize_recent_ref_entry(item)
            if not normalized_item:
                continue
            ref_id = str(normalized_item.get("id") or "").strip()
            if not ref_id or ref_id in seen_ids:
                continue
            seen_ids.add(ref_id)
            normalized.append(normalized_item)
            if len(normalized) >= 12:
                break
        self.recent_search_refs = normalized
        self.recent_search_refs_updated = False
