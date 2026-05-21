from __future__ import annotations

import re

TABLE_CELL_LIMIT = 12
TABLE_DIVIDER_CELL_RE = re.compile(r"^:?-{3,}:?$")


def _split_markdown_table_cells(line: str) -> list[str]:
    raw = (line or "").strip()
    if not raw:
        return []
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    cells = [part.strip() for part in raw.split("|")]
    return cells[:TABLE_CELL_LIMIT]


def _parse_partial_markdown_table_row(
    line: str,
    *,
    expected_columns: int | None = None,
    pad_to_expected: bool = False,
) -> list[str] | None:
    raw = (line or "").rstrip("\r")
    stripped = raw.strip()
    if not stripped:
        return None
    if stripped.startswith(">") or stripped.startswith("```"):
        return None
    if _parse_markdown_table_divider(stripped) is not None:
        return None
    if not (stripped.startswith("|") or "|" in stripped):
        return None
    cells = _split_markdown_table_cells(stripped)
    if not cells:
        return None
    if expected_columns is not None:
        if pad_to_expected and len(cells) < expected_columns:
            cells = cells + [""] * (expected_columns - len(cells))
        elif len(cells) > expected_columns:
            cells = cells[:expected_columns]
    if not any(cell for cell in cells):
        return None
    return cells


def _looks_like_markdown_table_line(line: str) -> bool:
    raw = (line or "").strip()
    if not raw:
        return False
    if raw.startswith(">") or raw.startswith("```"):
        return False
    pipe_count = raw.count("|")
    if pipe_count < 2 and not raw.startswith("|") and not raw.endswith("|"):
        return False
    cells = _split_markdown_table_cells(raw)
    if len(cells) < 2:
        return False
    return True


def _looks_like_partial_markdown_table_candidate(line: str) -> bool:
    raw = (line or "").strip()
    if not raw:
        return False
    if raw.startswith(">") or raw.startswith("```"):
        return False
    if _looks_like_markdown_table_line(raw):
        return True
    # Streamed table headers often arrive as a leading pipe plus an incomplete first cell,
    # e.g. "| Segment" before the remaining columns arrive in the next chunk.
    if raw.startswith("|"):
        return True
    # Support markdown tables without a leading pipe once at least one separator is visible.
    if "|" in raw and not raw.endswith((".", "!", "?", ":", ";")):
        return True
    return False


def _parse_markdown_table_divider(line: str) -> list[str] | None:
    if not _looks_like_markdown_table_line(line):
        return None
    cells = _split_markdown_table_cells(line)
    if len(cells) < 2:
        return None
    alignments: list[str] = []
    for cell in cells:
        token = cell.replace(" ", "")
        if not TABLE_DIVIDER_CELL_RE.match(token):
            return None
        if token.startswith(":") and token.endswith(":"):
            alignments.append("center")
        elif token.endswith(":"):
            alignments.append("right")
        else:
            alignments.append("left")
    return alignments


def _parse_markdown_table_row(line: str, *, expected_columns: int | None = None) -> list[str] | None:
    if not _looks_like_markdown_table_line(line):
        return None
    if _parse_markdown_table_divider(line) is not None:
        return None
    cells = _split_markdown_table_cells(line)
    if len(cells) < 2:
        return None
    if expected_columns is not None:
        if len(cells) < expected_columns:
            cells = cells + [""] * (expected_columns - len(cells))
        elif len(cells) > expected_columns:
            cells = cells[:expected_columns]
    if not any(cell for cell in cells):
        return None
    return cells
