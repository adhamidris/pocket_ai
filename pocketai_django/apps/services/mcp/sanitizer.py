from __future__ import annotations

"""
Utility helpers for filtering investigative filler sentences from MCP responses.

These routines are shared by the orchestrator, prompts, and chat portal to keep
placeholder removal consistent across streaming and persisted messages.
"""

import re
import logging
from typing import Iterable, Tuple

from opentelemetry import trace as otel_trace

from apps.services.rag_logging import structured_log

logger = logging.getLogger(__name__)
TRACER = otel_trace.get_tracer(__name__)

PLACEHOLDER_TOOL_PATTERN = re.compile(
    r"\b(search_knowledge|read_knowledge|read_document|table_aggregate|dataset_query|list_tables|create_case|update_case_details)\b",
    re.IGNORECASE,
)

INLINE_RESPONSE_BLOCK_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:[-*+]\s*)?[\"'`]?response(?:_|\s)?blocks[\"'`]?\s*:?",
    re.IGNORECASE,
)


def strip_inline_response_blocks(text: str) -> str:
    """
    Remove any inline `response_blocks` JSON appended to visible text.

    Models sometimes leak the structured blocks array into `response_text`
    despite prompt/schema guidance. We treat anything after the last marker
    as non-visitor-facing.
    """
    if not text:
        return ""
    match = None
    for candidate in INLINE_RESPONSE_BLOCK_PATTERN.finditer(text):
        match = candidate
    if not match:
        return text
    return text[: match.start()].rstrip()


def sanitize_with_diagnostics(
    text: str,
    *,
    conversation=None,
    stage: str = "final",
    filter_level: str = "friendly",
) -> tuple[str, list[str]]:
    """
    Sanitize text and log any dropped investigative filler sentences.
    """

    with TRACER.start_as_current_span("sanitizer.filter") as span:
        cleaned, dropped = _sanitize(text, filter_level=filter_level)
        if span.is_recording():
            span.set_attribute("sanitizer.stage", stage)
            span.set_attribute("sanitizer.original_length", len(text or ""))
            span.set_attribute("sanitizer.dropped_count", len(dropped))
    # If everything was dropped as filler, fall back to the original text to avoid empty replies.
    if not cleaned and text and text.strip():
        cleaned = text.strip()
    if dropped:
        business_id = getattr(getattr(conversation, "business_profile", None), "id", None) if conversation else None
        conversation_id = getattr(conversation, "id", None) if conversation else None
        for sentence in dropped:
            structured_log(
                "mcp",
                "sanitizer.dropped_sentence",
                {
                    "stage": stage,
                    "text": sentence[:200],
                },
                context={"conversation": conversation_id, "business": business_id},
                logger_obj=logger,
            )
    return cleaned, dropped


def sanitize_placeholder_thinking(
    text: str | None,
    *,
    fallback: str | None = "Gathering docs...",
    limit: int = 48,
) -> str | None:
    """
    Produce a short, visitor-safe spinner label from provider placeholders.
    """

    raw = (text or "").strip()
    if not raw and fallback:
        return fallback
    if not raw:
        return None
    normalized = PLACEHOLDER_TOOL_PATTERN.sub("", raw)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .,:-")
    if not normalized:
        normalized = (fallback or "").strip()
    if not normalized:
        return None
    if limit and len(normalized) > limit:
        normalized = normalized[: limit - 3].rstrip()
        normalized = f"{normalized}..."
    return normalized

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
    # Maintains compatibility for existing callers expecting the default filter level.
    return is_investigative_filler_with_level(sentence, filter_level="friendly")


def is_investigative_filler_with_level(sentence: str, *, filter_level: str = "friendly") -> bool:
    text = (sentence or "").strip().lower()
    if not text:
        return False
    text = strip_inline_response_blocks(text)
    if not text:
        return True
    # Only treat phrases that risk leaking internal mechanics as filler; allow human-style chatter.
    hard_patterns = (
        r"\bsearch_knowledge\b",
        r"\bread_document\b",
        r"\bupdate_case\b",
        r"\bcreate_case\b",
        r"\btool[_\s-]?call\b",
        r"\bfunction call\b",
        r"\bexecuting\b.*\btool\b",
        r"\binvoking\b.*\btool\b",
        r"\bcalling\b.*\btool\b",
        r"\bchain[-\s]?of[-\s]?thought\b",
        r"\bstep[-\s]?by[-\s]?step\b",
        r"\breasoning\b[:\-]",
        r"\banalysis\b[:\-]",
        r"i['’]ll check (the )?(docs|document|documents)",
        r"let me check (the )?(docs|document|documents)",
        r"let me (search|check|look|find|review)",
        r"i['’]ll (search|check|look|find|review)",
        r"i will (search|check|look|find|review)",
    )
    for pattern in hard_patterns:
        if re.search(pattern, text):
            return True
    if filter_level not in {"professional"}:
        return False
    # Professional tone: also treat investigative narration as filler.
    text = re.sub(r"^(sure|ok|okay|alright|great|thanks|thank you)[,!\s]+", "", text)
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
    return _sanitize(text, filter_level="friendly")[0]


def _sanitize(text: str, filter_level: str = "friendly") -> tuple[str, list[str]]:
    text = strip_inline_response_blocks(text)
    sentences, remainder = extract_sentences(text)
    keep_parts: list[str] = []
    dropped: list[str] = []
    for sentence, sep in sentences:
        stripped = sentence.strip()
        if not stripped:
            keep_parts.append(sep)
            continue
        if is_investigative_filler_with_level(stripped, filter_level=filter_level):
            dropped.append(stripped)
            # Preserve line breaks in the separator to avoid run-ons
            if "\n" in sep:
                keep_parts.append(sep)
            continue
        keep_parts.append(f"{sentence}{sep}")

    rem = remainder
    rem_stripped = rem.strip()
    if rem_stripped:
        if is_investigative_filler_with_level(rem_stripped, filter_level=filter_level):
            dropped.append(rem_stripped)
            if "\n" in rem:
                keep_parts.append("\n")
        else:
            keep_parts.append(rem)

    cleaned = "".join(keep_parts).strip()
    return cleaned, dropped
