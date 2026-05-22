from __future__ import annotations

from datetime import datetime, timezone as dt_timezone


class ToolContextDocumentsMixin:

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(tz=dt_timezone.utc).isoformat()

    def has_strong_primary_document(self) -> bool:
        """
        Document continuity is disabled; there is no implicit primary document.
        """
        return False

    def track_document_reference(
        self,
        upload_id: str,
        title: str | None = None,
        stage: str | None = None,
        confidence: float | None = None,
        *,
        update_primary: bool = True,
    ) -> None:
        """
        Track a document that was referenced in search results.

        Called after each search for debug/reference bookkeeping only.
        """
        if not upload_id:
            return

        upload_id = str(upload_id)
        self.referenced_upload_ids.add(upload_id)

        # Initialize or update document metadata
        if upload_id not in self.document_context:
            self.document_context[upload_id] = {
                "title": title or "",
                "search_count": 0,
                "read_count": 0,
                "stages": [],
                "confidences": [],
                "last_referenced_at": None,
                "last_read_at": None,
            }

        meta = self.document_context[upload_id]
        meta["search_count"] = meta.get("search_count", 0) + 1
        meta["last_referenced_at"] = self._utc_now_iso()
        if title and not meta.get("title"):
            meta["title"] = title
        if stage:
            stages = meta.get("stages", [])
            if stage not in stages:
                stages.append(stage)
            meta["stages"] = stages
        if confidence is not None:
            confidences = meta.get("confidences", [])
            confidences.append(float(confidence))
            # Keep only last 10 confidence scores
            meta["confidences"] = confidences[-10:]

    def track_document_read(self, upload_id: str, *, title: str | None = None) -> None:
        """
        Track an explicit document read.
        """
        if not upload_id:
            return
        upload_id = str(upload_id)
        self.referenced_upload_ids.add(upload_id)

        if upload_id not in self.document_context:
            self.document_context[upload_id] = {
                "title": title or "",
                "search_count": 0,
                "read_count": 0,
                "stages": [],
                "confidences": [],
                "last_referenced_at": None,
                "last_read_at": None,
            }

        meta = self.document_context[upload_id]
        if title and not meta.get("title"):
            meta["title"] = title
        meta["read_count"] = meta.get("read_count", 0) + 1
        meta["last_read_at"] = self._utc_now_iso()
        meta["last_referenced_at"] = meta.get("last_referenced_at") or meta["last_read_at"]

    def _update_primary_document(self, candidate_upload_id: str) -> None:
        """Primary document selection is disabled."""
        return

    def get_primary_document_title(self) -> str | None:
        """Document continuity is disabled; no primary document is exposed."""
        return None

    def get_document_context_for_query(self) -> dict:
        """
        Get document context dictionary for query rewriting and routing.

        Returns a dict suitable for passing to ContextAwareQueryRewriter.
        """
        return {
            "primary_upload_id": None,
            "primary_document_title": None,
            "referenced_upload_ids": list(self.referenced_upload_ids),
            "document_metadata": dict(self.document_context),
        }

    def get_document_context_for_persistence(self) -> dict:
        """
        Get document context for persistence to conversation.metadata.

        Returns a serializable dict that can be stored and rehydrated.
        """
        def _as_int(value: object) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        sorted_docs = sorted(
            self.document_context.items(),
            key=lambda item: (
                _as_int(item[1].get("read_count")),
                _as_int(item[1].get("search_count")),
                str(item[0]),
            ),
            reverse=True,
        )
        selected_ids: list[str] = []
        for upload_id, _ in sorted_docs:
            upload_id = str(upload_id)
            if upload_id in selected_ids:
                continue
            selected_ids.append(upload_id)
            if len(selected_ids) >= 10:
                break

        referenced_sorted = sorted(
            self.referenced_upload_ids,
            key=lambda upload_id: (
                _as_int(self.document_context.get(upload_id, {}).get("read_count")),
                _as_int(self.document_context.get(upload_id, {}).get("search_count")),
                str(upload_id),
            ),
            reverse=True,
        )[:20]

        return {
            "primary_upload_id": None,
            "referenced_uploads": referenced_sorted,  # Keep top 20 by engagement
            "document_metadata": {
                k: {
                    "title": v.get("title", ""),
                    "search_count": v.get("search_count", 0),
                    "read_count": v.get("read_count", 0),
                    "stages": v.get("stages", [])[-5:],  # Keep last 5 stages
                    "last_referenced_at": v.get("last_referenced_at"),
                    "last_read_at": v.get("last_read_at"),
                    # Don't persist confidences (transient)
                }
                for k in selected_ids
                for v in [self.document_context.get(k, {})]
            },
        }

    def hydrate_document_context(self, persisted: dict) -> None:
        """
        Restore document context from conversation.metadata.

        Called at the start of each turn to restore conversation state.
        """
        if not persisted:
            return

        self.primary_upload_id = None
        self.referenced_upload_ids = set(persisted.get("referenced_uploads", []))

        doc_metadata = persisted.get("document_metadata", {})
        for upload_id, meta in doc_metadata.items():
            self.document_context[upload_id] = {
                "title": meta.get("title", ""),
                "search_count": meta.get("search_count", 0),
                "read_count": meta.get("read_count", 0),
                "stages": meta.get("stages", []),
                "confidences": [],  # Not persisted
                "last_referenced_at": meta.get("last_referenced_at"),
                "last_read_at": meta.get("last_read_at"),
            }
