from __future__ import annotations

from typing import Mapping


class McpFileToolCompactionMixin:
    def _compact_file_meta(self, file_meta: object) -> dict[str, object]:
        file_out: dict[str, object] = {}
        if not isinstance(file_meta, Mapping):
            return file_out
        for key in ("id", "filename", "page_count"):
            value = file_meta.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            file_out[key] = value
        return file_out

    def _compact_search_conversation_files_payload(
        self,
        compact: dict[str, object],
        payload: Mapping[str, object],
        *,
        max_snippets: int,
        snippet_content_chars: int,
    ) -> dict[str, object]:
        raw_snippets = payload.get("snippets")
        snippets_out: list[dict[str, object]] = []
        preview_chars = max(200, min(900, int(snippet_content_chars)))
        if isinstance(raw_snippets, list):
            for entry in raw_snippets[: max(1, max_snippets)]:
                if not isinstance(entry, Mapping):
                    continue
                out: dict[str, object] = {}
                snippet_id = entry.get("id")
                if isinstance(snippet_id, str) and snippet_id.strip():
                    out["id"] = snippet_id.strip()
                file_out = self._compact_file_meta(entry.get("file"))
                if file_out:
                    out["file"] = file_out
                preview = entry.get("preview")
                if isinstance(preview, str) and preview.strip():
                    out["preview"] = self._clip_text(preview.strip(), preview_chars)
                read_hint = entry.get("read_hint") or entry.get("readHint")
                if isinstance(read_hint, Mapping):
                    ids = read_hint.get("ids")
                    if isinstance(ids, list):
                        out["read_hint"] = {"ids": [str(v) for v in ids if str(v).strip()][:12]}
                if out:
                    snippets_out.append(out)
        compact["snippets"] = snippets_out
        compact["prompt_compact"] = True
        return compact

    def _compact_read_conversation_file_payload(
        self,
        compact: dict[str, object],
        payload: Mapping[str, object],
        *,
        max_snippets: int,
        snippet_content_chars: int,
    ) -> dict[str, object]:
        raw_chunks = payload.get("chunks")
        chunks_out: list[dict[str, object]] = []
        content_chars = max(400, min(2400, int(snippet_content_chars)))
        if isinstance(raw_chunks, list):
            for entry in raw_chunks[: max(1, max_snippets)]:
                if not isinstance(entry, Mapping):
                    continue
                out: dict[str, object] = {}
                chunk_id = entry.get("id")
                if isinstance(chunk_id, str) and chunk_id.strip():
                    out["id"] = chunk_id.strip()
                file_out = self._compact_file_meta(entry.get("file"))
                if file_out:
                    out["file"] = file_out
                content = entry.get("content")
                if isinstance(content, str) and content.strip():
                    out["content"] = self._clip_text(content.strip(), content_chars)
                if out:
                    chunks_out.append(out)
        compact["chunks"] = chunks_out
        compact["prompt_compact"] = True
        return compact

    def _compact_pdf_artifact_payload(
        self,
        compact: dict[str, object],
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        artifact = payload.get("artifact")
        if isinstance(artifact, Mapping):
            artifact_out: dict[str, object] = {}
            file_id = artifact.get("file_id") or artifact.get("fileId") or artifact.get("id")
            filename = artifact.get("filename")
            if file_id is not None:
                artifact_out["file_id"] = str(file_id)
            if isinstance(filename, str) and filename.strip():
                artifact_out["filename"] = self._clip_text(filename.strip(), 180)
            if artifact_out:
                compact["artifact"] = artifact_out
        compact["prompt_compact"] = True
        return compact

    def _compact_pdf_extract_text_payload(
        self,
        compact: dict[str, object],
        payload: Mapping[str, object],
        *,
        snippet_content_chars: int,
    ) -> dict[str, object]:
        file_out = self._compact_file_meta(payload.get("file"))
        if file_out:
            compact["file"] = file_out
        text_value = payload.get("text")
        if isinstance(text_value, str) and text_value.strip():
            max_text = max(2000, min(15000, int(snippet_content_chars) * 10))
            compact["text"] = self._clip_text(text_value.strip(), max_text)
        compact["prompt_compact"] = True
        return compact
