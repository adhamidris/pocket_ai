from __future__ import annotations

import re

from django.conf import settings

from apps.accounts.models import BusinessProfile
from apps.rag.contracts import StreamingTurnContext


LOW_INTENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(hi|hello|hey|hola|hallo|مرحبا|السلام عليكم|as-salamu alaykum)\b", re.IGNORECASE),
    re.compile(r"^(good\s+(morning|evening|afternoon|day|night))\b", re.IGNORECASE),
    re.compile(r"^(thanks|thank you|gracias|gracias|shukran|شكرا)\b", re.IGNORECASE),
    re.compile(r"^(test|testing)\b", re.IGNORECASE),
)
LOW_INTENT_SIMPLE = {
    "hi",
    "hello",
    "hey",
    "hola",
    "مرحبا",
    "salam",
    "salaam",
    "as-salamu alaykum",
    "thanks",
    "thank you",
    "gracias",
    "شكرا",
    "test",
    "testing",
}
STRUCTURED_KEYWORDS = {
    "account",
    "action",
    "appointment",
    "apply",
    "balance",
    "book",
    "case",
    "cancel",
    "card",
    "complaint",
    "contact",
    "contract",
    "escalate",
    "fee",
    "help",
    "issue",
    "lead",
    "meeting",
    "order",
    "payment",
    "phone",
    "price",
    "problem",
    "refund",
    "schedule",
    "status",
    "support",
    "ticket",
    "update",
}


def _is_low_intent_message(text: str) -> bool:
    """Detect short greetings/acks that do not require planner metadata."""

    normalized = (text or "").strip()
    if not normalized:
        return False
    if len(normalized) > 80:
        return False
    lowered = normalized.lower()
    if any(keyword in lowered for keyword in STRUCTURED_KEYWORDS):
        return False
    if any(ch.isdigit() for ch in lowered):
        return False
    letters = sum(ch.isalpha() for ch in lowered)
    if letters == 0:
        return True
    for pattern in LOW_INTENT_PATTERNS:
        if pattern.match(lowered):
            return True
    if lowered in LOW_INTENT_SIMPLE:
        return True
    return False


def _tool_activity_present(stream_context: StreamingTurnContext | None) -> bool:
    if not stream_context:
        return False
    tool_context = getattr(stream_context, "tool_context", None)
    if not tool_context:
        return False
    signal_attrs = (
        "tool_trace",
        "knowledge_results",
        "knowledge_reads",
        "coverage_ledger",
    )
    for attr in signal_attrs:
        values = getattr(tool_context, attr, None)
        if values:
            return True
    if getattr(stream_context, "knowledge_payload", None):
        return True
    return False


# Tools that purely retrieve knowledge (no CRM side effects)
_KNOWLEDGE_ONLY_TOOLS = frozenset({
    "search_knowledge",
    "read_knowledge",
})

# CRM tools that need planner for action extraction
_CRM_TOOLS = frozenset()


def _has_crm_signals(stream_context: StreamingTurnContext | None, user_message: str) -> bool:
    """
    Detect if this turn has CRM-related signals that warrant running the planner.

    Returns True if:
    - A future CRM/action tool was called
    - Non-knowledge tools were called
    - User message contains identifiers (email, phone, digits)
    - User message contains complaint/escalation signals
    """
    # Check tool trace for CRM or non-knowledge tools
    if stream_context:
        tool_context = getattr(stream_context, "tool_context", None)
        if tool_context:
            tool_trace = getattr(tool_context, "tool_trace", [])
            if isinstance(tool_trace, list):
                for entry in tool_trace:
                    if not isinstance(entry, dict):
                        continue
                    tool_name = entry.get("tool", "")
                    # If any CRM tool was called, definitely need planner
                    if tool_name in _CRM_TOOLS:
                        return True
                    # If tool is not knowledge-only, might have side effects
                    if tool_name and tool_name not in _KNOWLEDGE_ONLY_TOOLS:
                        return True
    # Check message for CRM signals
    lowered = (user_message or "").lower()
    # Identifiers (email, phone, digits) suggest action requests
    if any(ch.isdigit() for ch in lowered):
        return True
    if "@" in lowered:  # Email pattern
        return True
    # Complaint/escalation keywords
    crm_keywords = {
        "complaint", "complain", "angry", "frustrated", "escalate",
        "manager", "supervisor", "refund", "cancel", "urgent",
        "problem", "issue", "broken", "not working", "help me",
    }
    if any(kw in lowered for kw in crm_keywords):
        return True

    return False


def _business_planner_override(business: BusinessProfile | None) -> bool | None:
    if not business:
        return None
    metadata = business.metadata if isinstance(business.metadata, dict) else {}
    if not metadata:
        return None
    enabled = metadata.get("portal_planner_enabled")
    if isinstance(enabled, bool):
        return enabled
    disabled = metadata.get("portal_disable_planner")
    if isinstance(disabled, bool):
        return not disabled
    return None


def _planner_decision(
    *,
    conversation,
    user_message: str,
    stream_context: StreamingTurnContext | None,
) -> tuple[bool, str | None]:
    if getattr(settings, "PORTAL_FORCE_PLANNER", False):
        return True, None
    if getattr(settings, "PORTAL_DISABLE_PLANNER", False):
        return False, "env_disabled"
    override = _business_planner_override(getattr(conversation, "business_profile", None))
    if override is not None:
        return override, "business_override" if not override else None
    # Skip planner for low intent messages (greetings, etc.)
    if _is_low_intent_message(user_message):
        return False, "low_intent"
    # Only run planner if CRM signals are present (not just any tool activity)
    # This saves ~9s latency for pure knowledge Q&A turns
    if _has_crm_signals(stream_context, user_message):
        return True, None
    # Knowledge-only turns don't need planner
    if _tool_activity_present(stream_context):
        return False, "knowledge_only"
    # No tool activity and no CRM signals - default to running planner
    # (might be a complex intent that didn't trigger tools)
    return True, None
