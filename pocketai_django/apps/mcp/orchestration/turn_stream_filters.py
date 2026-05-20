from __future__ import annotations

DSML_MARKERS = ("<｜DSML｜", "</｜DSML｜", "<｜｜DSML｜｜", "</｜｜DSML｜｜", "<|DSML|", "</|DSML|")


def _filter_dsml_stream(chunk: str, *, skip_line: bool) -> tuple[str, bool]:
    """
    Some models (notably DeepSeek) can emit DSML tool-call markup in the visible
    content stream. This is never visitor-facing; strip it line-by-line in a
    stream-safe way so partial tags never leak.
    """
    if not chunk:
        return "", skip_line

    remaining = chunk
    out_parts: list[str] = []

    while remaining:
        if skip_line:
            newline_idx = remaining.find("\n")
            if newline_idx == -1:
                # Still inside a DSML line; drop until we see the terminating newline.
                return "".join(out_parts), skip_line
            # Drop DSML line content; preserve a single newline to keep spacing stable.
            out_parts.append("\n")
            remaining = remaining[newline_idx + 1 :]
            skip_line = False
            continue

        next_idx = -1
        for marker in DSML_MARKERS:
            idx = remaining.find(marker)
            if idx != -1 and (next_idx == -1 or idx < next_idx):
                next_idx = idx
        if next_idx == -1:
            out_parts.append(remaining)
            break

        out_parts.append(remaining[:next_idx])
        remaining = remaining[next_idx:]
        skip_line = True

    return "".join(out_parts), skip_line
