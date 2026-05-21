from __future__ import annotations

import logging
import re
from datetime import datetime, time as dt_time, timedelta
from typing import Any

from django.utils import timezone

from apps.llm.ai_prompt_builder import PromptBundle
from apps.llm.llm_provider import load_default_provider
from apps.voice.models import CallEvent, CallSession


logger = logging.getLogger(__name__)

CALL_INSIGHTS_SCHEMA_VERSION = 1


def generate_call_insights(session: CallSession) -> dict[str, Any]:
    """
    Produce a versioned, structured insights payload for a CallSession.

    This is intentionally "best effort":
    - Outcome + callback time are primarily driven by runtime events for reliability.
    - Topics/key facts/follow-ups are extracted from the transcript via LLM when configured.
    """

    transcript_events = _iter_transcript_events(session)
    signal_events = _iter_signal_events(session)

    topics, follow_ups = _extract_topics_and_follow_ups(session, transcript_events)
    callback_entry = _build_callback_follow_up(session, transcript_events, signal_events)
    if callback_entry:
        follow_ups = [fu for fu in follow_ups if not (isinstance(fu, dict) and fu.get("type") == "callback")]
        follow_ups.append(callback_entry)

    outcome_label, outcome_source_ids = _determine_outcome(session, signal_events, follow_ups)

    generated_at = timezone.now()
    payload: dict[str, Any] = {
        "schema_version": CALL_INSIGHTS_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "language": str(session.language or "").strip() or "en",
        "country": str(session.country or "").strip(),
        "outcome": {"label": outcome_label, "source_event_ids": outcome_source_ids},
        "topics": topics,
        "follow_ups": follow_ups,
    }
    return payload


def format_call_insights_message(insights: dict[str, Any]) -> str:
    outcome = str(((insights or {}).get("outcome") or {}).get("label") or "").strip()
    if not outcome:
        outcome = "unknown"

    lines: list[str] = []
    lines.append("Call Insights")
    lines.append(f"Outcome: {outcome}")

    topics = insights.get("topics") if isinstance(insights, dict) else None
    if isinstance(topics, list) and topics:
        lines.append("")
        lines.append("Topics:")
        for topic in topics[:8]:
            if not isinstance(topic, dict):
                continue
            name = str(topic.get("topic") or "").strip()
            if not name:
                continue
            key_facts = topic.get("key_facts")
            if not isinstance(key_facts, list) or not key_facts:
                continue
            facts_text = []
            for fact in key_facts[:5]:
                if not isinstance(fact, dict):
                    continue
                fact_text = str(fact.get("fact") or "").strip()
                if fact_text:
                    facts_text.append(fact_text)
            if facts_text:
                lines.append(f"- {name}: " + "; ".join(facts_text))

    follow_ups = insights.get("follow_ups") if isinstance(insights, dict) else None
    if isinstance(follow_ups, list) and follow_ups:
        lines.append("")
        lines.append("Follow-ups:")
        for fu in follow_ups[:8]:
            if not isinstance(fu, dict):
                continue
            fu_type = str(fu.get("type") or "").strip()
            if fu_type == "callback":
                txt = str(fu.get("callback_time_text") or "").strip()
                parsed = str(fu.get("callback_time_iso") or "").strip()
                if txt and parsed:
                    lines.append(f"- Callback requested: {txt} ({parsed})")
                elif txt:
                    lines.append(f"- Callback requested: {txt}")
                continue
            summary = str(fu.get("summary") or fu.get("description") or "").strip()
            if summary:
                lines.append(f"- {summary}")

    return "\n".join(lines).strip()


def _iter_transcript_events(session: CallSession) -> list[CallEvent]:
    return list(
        session.events.filter(event_type__in=["stt.final", "llm.response.final"]).order_by("created_at", "id")
    )


def _iter_signal_events(session: CallSession) -> list[CallEvent]:
    signal_types = [
        "call.wrong_person",
        "call.no_engagement.goodbye",
        "call.busy",
        "call.callback_time.captured",
        "goal.delivered",
        "call.hangup.requested",
        "call.hangup.ok",
    ]
    return list(session.events.filter(event_type__in=signal_types).order_by("created_at", "id"))


def _extract_topics_and_follow_ups(
    session: CallSession, transcript_events: list[CallEvent]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    provider = load_default_provider()
    if not provider:
        return [], []

    lines: list[str] = []
    allowed_event_ids: set[int] = set()
    for ev in transcript_events[-200:]:
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        text = str(payload.get("text") or "").strip()
        if not text:
            continue
        speaker = "Customer" if ev.event_type == "stt.final" else "Agent"
        allowed_event_ids.add(int(ev.id))
        lines.append(f"#{int(ev.id)} {speaker}: {text}")

    transcript = "\n".join(lines).strip()
    if not transcript:
        return [], []

    system_prompt = (
        "You extract structured call insights for a business workspace.\n"
        "Be strictly grounded in the provided transcript lines.\n"
        "Do not hallucinate. Do not invent policies, prices, or commitments.\n"
        "Return JSON with keys: response_text (string), actions (array), extractions (array).\n"
        "In extractions include exactly ONE object of type 'call_insights' with payload:\n"
        "{ topics: [ { topic: string, key_facts: [ { fact: string, source_event_ids: [int] } ] } ],\n"
        "  follow_ups: [ { type: string, summary: string, source_event_ids: [int] } ] }\n"
        "Constraints:\n"
        "- Use at most 6 topics.\n"
        "- Each topic must have 1-5 key_facts.\n"
        "- Each key_fact must cite 1-5 source_event_ids.\n"
        "- follow_ups must exclude callback scheduling (handled elsewhere).\n"
        "- source_event_ids must be a subset of the provided event IDs.\n"
    )
    user_prompt = (
        f"Call objective: {session.objective}\n"
        f"Country: {session.country}\n"
        f"Language: {session.language}\n\n"
        "Transcript (each line starts with an event id):\n"
        f"{transcript}\n"
    )
    bundle = PromptBundle(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        transcript=[],
        knowledge_snippets=[],
        actions_catalog=[],
    )
    try:
        result = provider.generate(bundle)
    except Exception as exc:
        logger.exception("voice.call_insights.llm_failed session=%s error=%s", session.id, exc)
        return [], []

    extraction_payload: dict[str, Any] | None = None
    if isinstance(result, dict):
        extractions = result.get("extractions")
        if isinstance(extractions, list):
            for item in extractions:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or "").strip() == "call_insights":
                    payload = item.get("payload")
                    if isinstance(payload, dict):
                        extraction_payload = payload
                        break

    if not extraction_payload:
        return [], []

    topics_raw = extraction_payload.get("topics")
    followups_raw = extraction_payload.get("follow_ups")

    topics = _validate_topics(topics_raw, allowed_event_ids)
    follow_ups = _validate_follow_ups(followups_raw, allowed_event_ids)
    return topics, follow_ups


def _validate_topics(topics: object, allowed_event_ids: set[int]) -> list[dict[str, Any]]:
    if not isinstance(topics, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for topic in topics[:12]:
        if not isinstance(topic, dict):
            continue
        topic_name = str(topic.get("topic") or "").strip()
        if not topic_name:
            continue
        key_facts_raw = topic.get("key_facts")
        if not isinstance(key_facts_raw, list):
            continue
        key_facts: list[dict[str, Any]] = []
        for fact in key_facts_raw[:10]:
            if not isinstance(fact, dict):
                continue
            fact_text = _clip(str(fact.get("fact") or "").strip(), 220)
            if not fact_text:
                continue
            ids = _filter_event_ids(fact.get("source_event_ids"), allowed_event_ids)
            if not ids:
                continue
            key_facts.append({"fact": fact_text, "source_event_ids": ids})
        if not key_facts:
            continue
        cleaned.append({"topic": _clip(topic_name, 80), "key_facts": key_facts})
    return cleaned[:6]


def _validate_follow_ups(follow_ups: object, allowed_event_ids: set[int]) -> list[dict[str, Any]]:
    if not isinstance(follow_ups, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for fu in follow_ups[:12]:
        if not isinstance(fu, dict):
            continue
        fu_type = str(fu.get("type") or "").strip()
        if fu_type.lower() == "callback":
            continue
        summary = _clip(str(fu.get("summary") or "").strip(), 220)
        if not summary:
            continue
        ids = _filter_event_ids(fu.get("source_event_ids"), allowed_event_ids)
        if not ids:
            continue
        cleaned.append({"type": fu_type or "follow_up", "summary": summary, "source_event_ids": ids})
    return cleaned[:6]


def _determine_outcome(session: CallSession, signal_events: list[CallEvent], follow_ups: list[dict[str, Any]]) -> tuple[str, list[int]]:
    def _ids_for(event_type: str) -> list[int]:
        return [int(ev.id) for ev in signal_events if ev.event_type == event_type]

    wrong_person_ids = _ids_for("call.wrong_person")
    if wrong_person_ids:
        return "wrong_person", wrong_person_ids

    no_engagement_ids = _ids_for("call.no_engagement.goodbye")
    if no_engagement_ids:
        return "no_engagement", no_engagement_ids

    callback_ids = _ids_for("call.callback_time.captured") or _ids_for("call.busy")
    if callback_ids:
        return "callback_requested", callback_ids

    follow_up_ids: list[int] = []
    for item in follow_ups:
        ids = item.get("source_event_ids")
        if isinstance(ids, list):
            follow_up_ids.extend(int(x) for x in ids if isinstance(x, int))
    if follow_up_ids:
        return "follow_up_needed", sorted(set(follow_up_ids))[:20]

    goal_ids = _ids_for("goal.delivered")
    if goal_ids:
        return "info_delivered", goal_ids

    # Fallback: best effort based on call transcript existing.
    if session.events.filter(event_type__in=["stt.final", "llm.response.final"]).exists():
        return "info_delivered", []
    return "no_engagement", []


def _build_callback_follow_up(
    session: CallSession,
    transcript_events: list[CallEvent],
    signal_events: list[CallEvent],
) -> dict[str, Any] | None:
    callback_time_text, source_ids = _extract_callback_time_text(session, transcript_events, signal_events)
    if not callback_time_text:
        return None

    parsed_iso = _parse_callback_time_best_effort(callback_time_text, reference_dt=session.ended_at or timezone.now())
    payload: dict[str, Any] = {
        "type": "callback",
        "callback_time_text": callback_time_text,
        "callback_time_iso": parsed_iso,
        "source_event_ids": source_ids,
    }
    return payload


def _extract_callback_time_text(
    session: CallSession,
    transcript_events: list[CallEvent],
    signal_events: list[CallEvent],
) -> tuple[str, list[int]]:
    # Prefer explicit runtime signal.
    for ev in reversed(signal_events):
        if ev.event_type != "call.callback_time.captured":
            continue
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        text = str(payload.get("text") or "").strip()
        if text:
            ids = [int(ev.id)]
            match_id = _find_transcript_evidence_id(transcript_events, text)
            if match_id:
                ids.append(match_id)
            return text, sorted(set(ids))

    # Fall back to any runtime-captured draft in session.insights.
    insights = session.insights if isinstance(getattr(session, "insights", None), dict) else {}
    follow_ups = insights.get("follow_ups") if isinstance(insights, dict) else None
    if isinstance(follow_ups, list):
        for item in follow_ups:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").strip().lower() != "callback":
                continue
            text = str(item.get("callback_time_text") or "").strip()
            if text:
                match_id = _find_transcript_evidence_id(transcript_events, text)
                ids = [match_id] if match_id else []
                return text, ids

    # Backward compatibility: Phase 2 stored callback time under metadata.call_insights.callback_time.
    meta = session.metadata if isinstance(getattr(session, "metadata", None), dict) else {}
    call_insights = meta.get("call_insights") if isinstance(meta, dict) else None
    if isinstance(call_insights, dict):
        text = str(call_insights.get("callback_time") or "").strip()
        if text:
            match_id = _find_transcript_evidence_id(transcript_events, text)
            ids = [match_id] if match_id else []
            return text, ids

    return "", []


def _find_transcript_evidence_id(transcript_events: list[CallEvent], needle: str) -> int | None:
    needle_norm = (needle or "").strip().lower()
    if not needle_norm:
        return None
    for ev in reversed(transcript_events[-50:]):
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        text = str(payload.get("text") or "").strip().lower()
        if not text:
            continue
        if needle_norm in text:
            return int(ev.id)
    return None


def _parse_callback_time_best_effort(text: str, *, reference_dt: datetime) -> str | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    # Try external parser if available (best effort; may fail on "tomorrow", etc.).
    try:  # pragma: no cover - optional dependency
        from dateutil import parser as dateutil_parser  # type: ignore[import-not-found]

        parsed = dateutil_parser.parse(raw, fuzzy=True)
        if parsed.tzinfo is None:
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed.isoformat()
    except Exception:
        pass

    local_ref = reference_dt
    if timezone.is_aware(reference_dt):
        local_ref = timezone.localtime(reference_dt)
    base_date = local_ref.date()
    lower = raw.lower()

    delta = _parse_in_delta(lower)
    if delta:
        dt = (local_ref + delta).replace(second=0, microsecond=0)
        return dt.isoformat()

    target_date = None
    if "tomorrow" in lower:
        target_date = base_date + timedelta(days=1)
    elif "today" in lower:
        target_date = base_date
    else:
        day = _parse_weekday_reference(lower, base_date)
        if day:
            target_date = day

    if not target_date:
        return None

    tod = _extract_time_of_day(lower)
    if not tod:
        return None

    naive = datetime.combine(target_date, tod)
    aware = timezone.make_aware(naive, timezone.get_current_timezone())
    return aware.isoformat()


def _parse_in_delta(lower: str) -> timedelta | None:
    match = re.search(r"\\bin\\s+(\\d+)\\s*(minute|minutes|min|hour|hours|hr|day|days)\\b", lower)
    if not match:
        return None
    qty = int(match.group(1))
    unit = match.group(2)
    if unit.startswith("min"):
        return timedelta(minutes=qty)
    if unit.startswith("hour") or unit == "hr":
        return timedelta(hours=qty)
    if unit.startswith("day"):
        return timedelta(days=qty)
    return None


def _parse_weekday_reference(lower: str, base_date) -> Any | None:
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    for name, idx in weekdays.items():
        if name not in lower:
            continue
        days_ahead = (idx - base_date.weekday()) % 7
        if days_ahead == 0:
            # "next monday" should push to the next week.
            if f"next {name}" in lower:
                days_ahead = 7
        return base_date + timedelta(days=days_ahead)
    return None


def _extract_time_of_day(lower: str) -> dt_time | None:
    # 24h time, e.g. 15:30
    match = re.search(r"\\b([01]?\\d|2[0-3]):([0-5]\\d)\\b", lower)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        return dt_time(hour=hour, minute=minute)

    # 12h time, e.g. 3pm / 3:15 pm
    match = re.search(r"\\b(\\d{1,2})(?::(\\d{2}))?\\s*(am|pm)\\b", lower)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = match.group(3)
    hour = max(1, min(12, hour))
    minute = max(0, min(59, minute))
    if meridiem == "pm" and hour != 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return dt_time(hour=hour, minute=minute)


def _filter_event_ids(value: object, allowed_event_ids: set[int]) -> list[int]:
    if not isinstance(value, list):
        return []
    cleaned: list[int] = []
    for item in value:
        try:
            event_id = int(item)
        except Exception:
            continue
        if event_id in allowed_event_ids:
            cleaned.append(event_id)
    out: list[int] = []
    seen: set[int] = set()
    for event_id in cleaned:
        if event_id in seen:
            continue
        seen.add(event_id)
        out.append(event_id)
    return out[:10]


def _clip(text: str, max_chars: int) -> str:
    if not text:
        return ""
    if max_chars <= 0:
        return text
    return text[:max_chars].rstrip()
