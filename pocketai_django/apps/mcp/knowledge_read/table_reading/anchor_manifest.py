from __future__ import annotations

from typing import Mapping


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _load_table_anchor_manifest(context, *, ref_id: str, table_id: str | None = None) -> dict[str, object] | None:
    cache_value = getattr(context, "table_row_anchor_manifests", None)
    if not isinstance(cache_value, dict):
        return None
    candidate_keys: list[str] = []
    ref_key = str(ref_id or "").strip()
    table_key = str(table_id or "").strip()
    if ref_key:
        candidate_keys.append(ref_key)
    if table_key and table_key not in candidate_keys:
        candidate_keys.append(table_key)
    for key in candidate_keys:
        raw_manifest = cache_value.get(key)
        if not isinstance(raw_manifest, Mapping):
            continue
        anchors: list[int] = []
        raw_anchors = raw_manifest.get("anchors")
        if isinstance(raw_anchors, list):
            for entry in raw_anchors:
                if isinstance(entry, Mapping):
                    parsed = _coerce_int(entry.get("row_index"))
                else:
                    parsed = _coerce_int(entry)
                if parsed is None or parsed < 0:
                    continue
                anchors.append(int(parsed))
        # Deduplicate anchors while preserving order.
        if anchors:
            seen_rows: set[int] = set()
            anchors = [row for row in anchors if (row not in seen_rows and not seen_rows.add(row))]

        matched_row_index = _coerce_int(raw_manifest.get("matched_row_index"))
        if matched_row_index is None:
            matched_row_index = -1
        if matched_row_index < 0:
            if anchors:
                matched_row_index = int(anchors[0])
            else:
                continue

        # Ensure anchors includes matched_row_index, and keep it first.
        if matched_row_index in anchors:
            anchors = [matched_row_index] + [row for row in anchors if row != matched_row_index]
        else:
            anchors = [matched_row_index] + anchors
        anchors = anchors[:5]

        out: dict[str, object] = {
            "ref_id": key,
            "table_id": table_key or str(raw_manifest.get("table_id") or "").strip(),
            "matched_row_index": int(matched_row_index),
        }
        if anchors:
            out["anchors"] = list(anchors)

        try:
            estimated_rows = int(raw_manifest.get("estimated_rows") or 0)
        except (TypeError, ValueError):
            estimated_rows = 0
        if estimated_rows > 0:
            out["estimated_rows"] = int(estimated_rows)

        try:
            estimated_columns = int(raw_manifest.get("estimated_columns") or 0)
        except (TypeError, ValueError):
            estimated_columns = 0
        if estimated_columns > 0:
            out["estimated_columns"] = int(estimated_columns)

        return out
    return None
