from __future__ import annotations

"""
Utility helpers for filtering investigative filler sentences from MCP responses.

These routines are shared by the orchestrator, prompts, and chat portal to keep
placeholder removal consistent across streaming and persisted messages.
"""

import re
import logging
from typing import Iterable, Tuple

from apps.services.rag_logging import structured_log

logger = logging.getLogger(__name__)


def sanitize_with_diagnostics(
    text: str,
    *,
    conversation=None,
    stage: str = "final",
    filter_level: str = "friendly",
) -> tuple[str, list[str]]:
    """
    Sanitize text and log any dropped investigative filler sentences.

    Used in streaming (MCP orchestrator) and persistence (chat_portal) to keep
    filler removal consistent between what was streamed and what is stored.
    
    Why sanitization:
        - LLMs often emit "investigative filler" (e.g., "Let me check that for you...")
        - These phrases leak internal mechanics and don't add value
        - Professional tone needs stricter filtering (drops more narration)
        - Friendly tone only drops hard guardrails (tool names, function calls)
        
    Filter levels:
        - "friendly": Only drops hard patterns (tool names, function calls)
        - "professional": Also drops investigative narration ("I'll check...", "Let me...")
        
    Args:
        text: Raw text to sanitize
        conversation: Optional conversation for logging context
        stage: Processing stage ("streaming_tools", "final_answer", "persisted_message")
        filter_level: Aggressiveness ("friendly" | "professional")
        
    Returns:
        Tuple of (cleaned_text, dropped_sentences_list)
        
    Edge case:
        If all text is dropped as filler, returns original text to avoid empty replies.
    """

    cleaned, dropped = _sanitize(text, filter_level=filter_level)
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
            )
    return cleaned, dropped

def extract_sentences(buffer: str) -> Tuple[list[Tuple[str, str]], str]:
    """
    Split a buffer into completed sentences with trailing whitespace separators.

    Helper for streaming chunk assembly; keeps remainder so partial sentences
    can be continued as more tokens arrive.
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
    """
    Detect investigative/meta filler we want to drop from customer-facing text.

    `filter_level` tunes aggressiveness (professional drops more narration).
    
    Hard patterns (always dropped, regardless of filter_level):
        - Tool names: "search_knowledge", "read_document", "create_case"
        - Function calls: "tool_call", "function call", "executing tool"
        - Reasoning markers: "chain-of-thought", "step-by-step", "reasoning:", "analysis:"
        
    Professional-only patterns (dropped only when filter_level="professional"):
        - Investigative prefixes: "I'll", "I will", "I'm", "Let me", "I'm going to"
        - Status phrases: "reviewing knowledge", "checking", "looking into"
        - Thanks + review: "thanks for the update, i'm reviewing..."
        
    Why two levels:
        - Friendly tone allows some natural conversation ("I'll help you with that")
        - Professional tone needs stricter filtering (no internal narration)
        - Hard patterns always leak mechanics (must be dropped)
        
    Args:
        sentence: Sentence to check (should be trimmed)
        filter_level: "friendly" (lenient) or "professional" (strict)
        
    Returns:
        True if sentence should be dropped, False otherwise
    """
    text = (sentence or "").strip().lower()
    if not text:
        return False
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
