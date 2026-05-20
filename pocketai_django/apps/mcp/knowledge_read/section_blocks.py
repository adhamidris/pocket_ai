from __future__ import annotations

import re
from typing import Mapping


_heading_month_tokens = {
    "jan",
    "january",
    "feb",
    "february",
    "mar",
    "march",
    "apr",
    "april",
    "may",
    "jun",
    "june",
    "jul",
    "july",
    "aug",
    "august",
    "sep",
    "sept",
    "september",
    "oct",
    "october",
    "nov",
    "november",
    "dec",
    "december",
    "ongoing",
    "present",
}

def _upload_title(upload) -> str:
    if not upload:
        return "Untitled"
    title = (
        getattr(upload, "display_name", None)
        or getattr(upload, "filename", None)
        or getattr(upload, "source_name", None)
        or getattr(upload, "external_reference", None)
        or getattr(upload, "slug", None)
        or str(getattr(upload, "id", "") or "")
    )
    title_text = str(title or "").strip() or "Untitled"
    return title_text

def _collapse_ws(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()

def _parse_block_anchor(value: object) -> tuple[int, int] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    match = re.search(r"p(?P<page>\d+)-b(?P<block>\d+)", raw)
    if not match:
        return None
    try:
        return int(match.group("page")), int(match.group("block"))
    except (TypeError, ValueError):
        return None

def _load_ordered_page_blocks(upload_id: str, *, upload_block_cache: dict[str, list[dict[str, object]]]) -> list[dict[str, object]]:
    cached = upload_block_cache.get(upload_id)
    if cached is not None:
        return cached
    try:
        from apps.knowledge.models import KnowledgeUploadPageBlock

        rows = list(
            KnowledgeUploadPageBlock.objects.filter(upload_id=upload_id)
            .exclude(text="")
            .order_by("page__page_number", "order_index")
            .values(
                "page__page_number",
                "order_index",
                "text",
                "section_heading",
                "heading_path",
            )
        )
    except Exception:
        rows = []
    normalized: list[dict[str, object]] = []
    for row in rows:
        try:
            page_number = int(row.get("page__page_number") or 0)
            order_index = int(row.get("order_index") or 0)
        except (TypeError, ValueError):
            continue
        normalized.append(
            {
                "page_number": page_number,
                "order_index": order_index,
                "text": str(row.get("text") or ""),
                "section_heading": str(row.get("section_heading") or ""),
                "heading_path": list(row.get("heading_path") or []),
            }
        )
    upload_block_cache[upload_id] = normalized
    return normalized

def _classify_heading_block(block: Mapping[str, object]) -> dict[str, object] | None:
    raw_text = str(block.get("text") or "")
    text = _collapse_ws(raw_text)
    if not text:
        return None

    normalized_path = [_collapse_ws(item) for item in (block.get("heading_path") or []) if _collapse_ws(item)]
    explicit_heading = _collapse_ws(block.get("section_heading") or "")
    if normalized_path or explicit_heading:
        label = normalized_path[-1] if normalized_path else explicit_heading
        return {
            "page_number": int(block.get("page_number") or 0),
            "order_index": int(block.get("order_index") or 0),
            "label": label,
            "level": max(1, len(normalized_path) or 1),
            "signature": "|".join(item.lower() for item in (normalized_path or [label]) if item),
            "heuristic": False,
        }

    if len(text) > 180:
        return None
    stripped = text.lstrip()
    if not stripped:
        return None
    if stripped.startswith(("●", "•", "-", "–", "—", "➔", "*")):
        return None
    if stripped[0].islower():
        return None
    if text[-1:] in {".", ";", "?", "!"}:
        return None

    tokens = re.findall(r"[A-Za-z0-9&/+'().-]+", text)
    if not tokens or len(tokens) > 22:
        return None

    lower_tokens = [token.lower() for token in tokens]
    has_digits = any(ch.isdigit() for ch in text)
    has_month = any(token in _heading_month_tokens for token in lower_tokens)
    pipe_count = text.count("|")
    comma_count = text.count(",")
    colon_count = text.count(":")

    level = 0
    if len(tokens) <= 6 and not has_digits and not has_month and pipe_count == 0 and comma_count <= 1 and colon_count == 0:
        level = 1
    elif len(tokens) <= 12 and not has_digits and not has_month and comma_count <= 1 and colon_count == 0:
        level = 1
    elif len(tokens) <= 20 and (has_digits or has_month or pipe_count > 0 or comma_count > 0) and colon_count <= 1:
        level = 2

    if level <= 0:
        return None

    return {
        "page_number": int(block.get("page_number") or 0),
        "order_index": int(block.get("order_index") or 0),
        "label": text,
        "level": level,
        "signature": text.lower(),
        "heuristic": True,
    }

def _resolve_text_section_span(
    *,
    upload_id: str,
    upload_block_cache: dict[str, list[dict[str, object]]],
    chunk_record,
    chunk_meta: Mapping[str, object],
    fallback_page_number: int,
) -> dict[str, int] | None:
    blocks = _load_ordered_page_blocks(upload_id, upload_block_cache=upload_block_cache)
    if not blocks:
        return None

    block_index_by_key = {
        (int(block["page_number"]), int(block["order_index"])): idx
        for idx, block in enumerate(blocks)
    }

    raw_block_anchors = chunk_meta.get("block_anchors")
    parsed_anchors: list[tuple[int, int]] = []
    if isinstance(raw_block_anchors, list):
        for entry in raw_block_anchors:
            parsed = _parse_block_anchor(entry)
            if parsed is not None:
                parsed_anchors.append(parsed)
    parsed_anchors = [anchor for anchor in parsed_anchors if anchor in block_index_by_key]
    parsed_anchors.sort()

    if parsed_anchors:
        anchor_start_key = parsed_anchors[0]
        anchor_end_key = parsed_anchors[-1]
    else:
        canonical_anchor = _parse_block_anchor(chunk_meta.get("canonical_anchor_id"))
        if canonical_anchor and canonical_anchor in block_index_by_key:
            anchor_start_key = canonical_anchor
            anchor_end_key = canonical_anchor
        else:
            anchor_start_key = (int(fallback_page_number), 0)
            anchor_end_key = (int(fallback_page_number), 0)
            if anchor_start_key not in block_index_by_key:
                return None

    anchor_start_idx = block_index_by_key.get(anchor_start_key)
    anchor_end_idx = block_index_by_key.get(anchor_end_key)
    if anchor_start_idx is None or anchor_end_idx is None:
        return None
    if anchor_end_idx < anchor_start_idx:
        anchor_start_idx, anchor_end_idx = anchor_end_idx, anchor_start_idx

    headings: list[dict[str, object]] = []
    heading_index_by_pos: dict[tuple[int, int], int] = {}
    for idx, block in enumerate(blocks):
        candidate = _classify_heading_block(block)
        if candidate is None:
            continue
        candidate["idx"] = idx
        headings.append(candidate)
        heading_index_by_pos[(int(candidate["page_number"]), int(candidate["order_index"]))] = idx

    if not headings:
        return None

    anchor_major: list[dict[str, object]] = [
        heading
        for heading in headings
        if anchor_start_idx <= int(heading["idx"]) <= anchor_end_idx and int(heading["level"]) == 1
    ]
    if anchor_major:
        start_heading = anchor_major[-1]
    else:
        prior_major = [
            heading
            for heading in headings
            if int(heading["idx"]) <= anchor_start_idx and int(heading["level"]) == 1
        ]
        if prior_major:
            start_heading = prior_major[-1]
        else:
            anchor_any = [
                heading
                for heading in headings
                if anchor_start_idx <= int(heading["idx"]) <= anchor_end_idx
            ]
            if anchor_any:
                start_heading = anchor_any[-1]
            else:
                prior_any = [heading for heading in headings if int(heading["idx"]) <= anchor_start_idx]
                if not prior_any:
                    return None
                start_heading = prior_any[-1]

    start_idx = int(start_heading["idx"])
    start_level = int(start_heading["level"])
    end_idx = len(blocks) - 1
    start_signature = str(start_heading.get("signature") or "")
    for heading in headings:
        idx = int(heading["idx"])
        if idx <= start_idx:
            continue
        level = int(heading["level"])
        signature = str(heading.get("signature") or "")
        if level <= start_level and signature != start_signature:
            end_idx = idx - 1
            break

    if end_idx < start_idx:
        return None

    start_block = blocks[start_idx]
    end_block = blocks[end_idx]
    return {
        "start_page_number": int(start_block["page_number"]),
        "start_block_order": int(start_block["order_index"]),
        "end_page_number": int(end_block["page_number"]),
        "end_block_order": int(end_block["order_index"]),
    }
