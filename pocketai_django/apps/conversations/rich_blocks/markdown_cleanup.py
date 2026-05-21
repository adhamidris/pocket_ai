from __future__ import annotations

import re


def _fix_embedded_list_in_item(line: str) -> list[str]:
    """Split a list-prefixed line that contains embedded inline numbered items.

    For example:
        ``- Description includes Facebook event 5. Gas reading 6. Another``
    becomes:
        ``- Description includes Facebook event``
        ``5. Gas reading``
        ``6. Another``

    Returns ``[line]`` unchanged when no embedded items are found.
    """
    stripped = line.lstrip()
    leading_ws = line[: len(line) - len(stripped)]

    # Determine the existing list prefix and the remaining text.
    bullet_m = re.match(r"^([-*+])\s+", stripped)
    ordered_m = re.match(r"^(\d+)\.\s+", stripped)
    if bullet_m:
        prefix = stripped[: bullet_m.end()]
        text_body = stripped[bullet_m.end():]
    elif ordered_m:
        prefix = stripped[: ordered_m.end()]
        text_body = stripped[ordered_m.end():]
    else:
        return [line]

    if not text_body:
        return [line]

    # Look for embedded numbered items inside the remaining text.
    # Require at least some non-trivial text before the first embedded number
    # to avoid false positives like "- See item 3. It works great".
    embedded = list(re.finditer(r"(\d+)\.\s+", text_body))
    if not embedded:
        return [line]

    # We need either 2+ embedded numbered items, or 1 embedded number that is
    # preceded by substantial text (heuristic: >=10 chars before it).
    first_emb = None
    if len(embedded) >= 2:
        # Pick the first embedded number that has non-trivial preceding text.
        for m in embedded:
            before = text_body[: m.start()].rstrip()
            if len(before) >= 6:
                first_emb = m
                break
        if first_emb is None:
            return [line]
    elif len(embedded) == 1:
        m = embedded[0]
        before = text_body[: m.start()].rstrip()
        if len(before) < 10:
            return [line]
        first_emb = m
    else:
        return [line]

    # Split: keep original bullet with text up to the embedded number,
    # then each embedded numbered item on its own line.
    trimmed_text = text_body[: first_emb.start()].rstrip()
    result = [f"{leading_ws}{prefix}{trimmed_text}"]

    rest = text_body[first_emb.start():]
    parts = re.split(r"(\d+)\.\s+", rest)
    i = 1
    while i < len(parts):
        num = parts[i]
        content = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if content:
            result.append(f"{num}. {content}")
        i += 2

    return result if len(result) > 1 else [line]


def _fix_malformed_markdown(text: str) -> str:
    """
    Fix common LLM markdown formatting issues before parsing.

    Handles:
    - Inline numbered lists: "text 1. item 2. item" → "text:\n\n1. item\n2. item"
    - Inline bullet lists: "text - item - item" → "text:\n\n- item\n- item"
    - Missing blank lines before lists
    """
    if not text:
        return text

    lines = text.split("\n")
    fixed_lines: list[str] = []

    for line in lines:
        # Lines that are already list items: still check for embedded inline lists.
        stripped = line.lstrip()
        is_existing_list_item = re.match(r"^(\d+)\.\s+", stripped) or re.match(r"^[-*+]\s+", stripped)
        if is_existing_list_item:
            fixed = _fix_embedded_list_in_item(line)
            fixed_lines.extend(fixed)
            continue

        # Skip code blocks (don't modify content inside code fences)
        if stripped.startswith("```"):
            fixed_lines.append(line)
            continue

        # Fix inline numbered lists: "text 1. item 2. item 3. item"
        # Pattern: word/punctuation followed by " 1. " mid-line (not at start)
        inline_numbered_pattern = r"(\S)(\s+)(\d+)\.\s+(\S)"
        if re.search(inline_numbered_pattern, line):
            # Check if this looks like an inline list (multiple numbered items on same line)
            numbered_items = list(re.finditer(r"(\d+)\.\s+", line))
            if len(numbered_items) >= 2:
                # Multiple numbered items on one line - likely malformed list
                # Find where the list starts (first number that follows text)
                first_match = None
                for match in numbered_items:
                    # Check if there's text before this number (not just whitespace)
                    before = line[:match.start()].rstrip()
                    if before and not re.match(r"^\s*$", before):
                        first_match = match
                        break

                if first_match:
                    # Split into intro text and list items
                    intro = line[:first_match.start()].rstrip()
                    rest = line[first_match.start():]

                    # Add colon to intro if it doesn't end with punctuation
                    if intro and intro[-1] not in ":;.,!?":
                        intro = intro + ":"

                    # Split the rest into individual list items
                    items = re.split(r"(\d+)\.\s+", rest)
                    formatted_items: list[str] = []
                    i = 1
                    while i < len(items):
                        if i + 1 < len(items):
                            num = items[i]
                            content = items[i + 1].strip()
                            if content:
                                formatted_items.append(f"{num}. {content}")
                        i += 2

                    if formatted_items:
                        fixed_lines.append(intro)
                        fixed_lines.append("")  # Blank line before list
                        fixed_lines.extend(formatted_items)
                        continue

        # Fix inline bullet lists: "text - item - another item"
        # Only if there are multiple " - " patterns suggesting a list
        bullet_matches = list(re.finditer(r"\s[-*+]\s+\S", line))
        if len(bullet_matches) >= 2:
            # Check if first bullet is mid-sentence (has text before it)
            first_match = bullet_matches[0]
            before = line[:first_match.start()].strip()
            if before and not re.match(r"^[-*+]\s", before):
                # Split into intro and items
                intro = before
                rest = line[first_match.start():]

                # Add colon to intro if needed
                if intro and intro[-1] not in ":;.,!?":
                    intro = intro + ":"

                # Split by bullet markers
                items = re.split(r"\s+([-*+])\s+", rest)
                formatted_items: list[str] = []
                i = 1
                while i < len(items):
                    if i + 1 < len(items):
                        marker = items[i]
                        content = items[i + 1].strip()
                        if content:
                            formatted_items.append(f"{marker} {content}")
                    i += 2

                if formatted_items:
                    fixed_lines.append(intro)
                    fixed_lines.append("")  # Blank line before list
                    fixed_lines.extend(formatted_items)
                    continue

        # No fixes needed for this line
        fixed_lines.append(line)

    return "\n".join(fixed_lines)
