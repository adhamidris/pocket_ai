from __future__ import annotations

"""
Utility helpers for filtering investigative filler sentences from MCP responses.

These routines are shared by the orchestrator, prompts, and chat portal to keep
placeholder removal consistent across streaming and persisted messages.
"""

import re
import logging
from typing import Iterable, Tuple

logger = logging.getLogger(__name__)


def sanitize_with_diagnostics(
    text: str,
    *,
    conversation=None,
    stage: str = "final",
) -> tuple[str, list[str]]:
    """
    Sanitize text and log any dropped investigative filler sentences.
    """

    cleaned, dropped = _sanitize(text)
    if dropped:
        business_id = getattr(getattr(conversation, "business_profile", None), "id", None) if conversation else None
        conversation_id = getattr(conversation, "id", None) if conversation else None
        for sentence in dropped:
            logger.info(
                "mcp.sanitizer.dropped_sentence stage=%s conversation=%s business=%s text=%s",
                stage,
                conversation_id,
                business_id,
                sentence[:200],
            )
    return cleaned, dropped

def extract_sentences(buffer: str) -> Tuple[list[Tuple[str, str]], str]:
    """
    Split a buffer into completed sentences with trailing whitespace separators.
    """

    sentences: list[Tuple[str, str]] = []
    last_end = 0
    pattern = re.compile(r"([^.!?]*[.!?])(\s*)", re.DOTALL)
    for match in pattern.finditer(buffer):
        segment = match.group(1)
        sep = match.group(2) or ""
        if segment:
            sentences.append((segment, sep))
        last_end = match.end()
    remainder = buffer[last_end:] if last_end < len(buffer) else ""
    return sentences, remainder


def is_investigative_filler(sentence: str) -> bool:
    text = (sentence or "").strip().lower()
    if not text:
        return False
    prefixes = (
        "i'll ",
        "i will ",
        "i’m ",
        "i am ",
        "im ",
        "let me ",
        "i'm going to ",
        "i am going to ",
        "reviewing ",
        "searching ",
        "checking ",
        "looking into ",
    )
    if text in {
        "reviewing knowledge",
        "reviewing the document",
        "reviewing docs",
        "loading details",
    }:
        return True
    if any(text.startswith(prefix) for prefix in prefixes):
        return True
    if text.startswith("thanks for the update, i'm reviewing"):
        return True
    if text.startswith("thanks for the update, i’m reviewing"):
        return True
    if re.match(r"^i['’]m\s+(reviewing|checking|searching|looking)", text):
        return True
    return False


def sanitize_text(text: str) -> str:
    return _sanitize(text)[0]


def _sanitize(text: str) -> tuple[str, list[str]]:
    sentences, remainder = extract_sentences(text)
    keep_parts: list[str] = []
    dropped: list[str] = []
    for sentence, sep in sentences:
        stripped = sentence.strip()
        if not stripped:
            keep_parts.append(sep)
            continue
        if is_investigative_filler(stripped):
            dropped.append(stripped)
            # Preserve line breaks in the separator to avoid run-ons
            if "\n" in sep:
                keep_parts.append(sep)
            continue
        keep_parts.append(f"{sentence}{sep}")

    rem = remainder
    rem_stripped = rem.strip()
    if rem_stripped:
        if is_investigative_filler(rem_stripped):
            dropped.append(rem_stripped)
            if "\n" in rem:
                keep_parts.append("\n")
        else:
            keep_parts.append(rem)

    cleaned = "".join(keep_parts).strip()
    return cleaned, dropped
