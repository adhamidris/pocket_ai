from __future__ import annotations

import os
import re
import json
import time
from typing import Mapping

from apps.voice.deepgram_tts import DeepgramTTSWSSession


async def _twilio_send(ws, payload: dict) -> None:
    await ws.send(json.dumps(payload))


def _stream_target_fields(stream_id: str | None) -> dict[str, str]:
    sid = str(stream_id or "").strip()
    if not sid:
        return {}
    # Twilio expects `streamSid`; Telnyx TeXML media streams expect `stream_id`.
    return {"streamSid": sid, "stream_id": sid}


def _extract_channel(payload: Mapping[str, object]) -> Mapping[str, object]:
    channel = payload.get("channel")
    if isinstance(channel, Mapping):
        return channel
    if isinstance(channel, list):
        for item in channel:
            if isinstance(item, Mapping):
                return item
        return {}
    channels = payload.get("channels")
    if isinstance(channels, list):
        for item in channels:
            if isinstance(item, Mapping):
                return item
    return {}


def _extract_final_transcript(payload: dict) -> str:
    if payload.get("type") == "UtteranceEnd":
        return ""
    is_final = bool(payload.get("is_final") or payload.get("speech_final"))
    if not is_final:
        return ""
    channel = _extract_channel(payload)
    alternatives = channel.get("alternatives") or []
    if not alternatives or not isinstance(alternatives, list):
        return ""
    first = alternatives[0] if isinstance(alternatives[0], dict) else {}
    transcript = str(first.get("transcript") or "").strip()
    return transcript


def _extract_final_transcript_with_confidence(payload: dict) -> tuple[str, float]:
    transcript = _extract_final_transcript(payload)
    if not transcript:
        return "", 0.0
    channel = _extract_channel(payload)
    alternatives = channel.get("alternatives") or []
    if not alternatives or not isinstance(alternatives, list):
        return transcript, 0.0
    first = alternatives[0] if isinstance(alternatives[0], dict) else {}
    try:
        confidence = float(first.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    return transcript, confidence


def _extract_transcript_with_confidence(payload: dict) -> tuple[str, float, bool]:
    if payload.get("type") == "UtteranceEnd":
        return "", 0.0, False
    channel = _extract_channel(payload)
    alternatives = channel.get("alternatives") or []
    if not alternatives or not isinstance(alternatives, list):
        return "", 0.0, False
    first = alternatives[0] if isinstance(alternatives[0], dict) else {}
    transcript = str(first.get("transcript") or "").strip()
    if not transcript:
        return "", 0.0, False
    try:
        confidence = float(first.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    is_final = bool(payload.get("is_final") or payload.get("speech_final"))
    return transcript, confidence, is_final


async def _deepgram_ws_send(
    ws_session: DeepgramTTSWSSession,
    text: str,
    *,
    pending_chars: int,
    last_flush_at: float,
    force_flush: bool,
) -> tuple[int, float]:
    """
    Send text to Deepgram WS TTS and flush only on stronger boundaries.

    Flushing each tiny chunk can introduce audible micro-pauses/prosody resets.
    """
    clean = str(text or "").strip()
    if not clean:
        return pending_chars, last_flush_at
    await ws_session.send_text(text)
    pending_chars = max(0, int(pending_chars)) + len(clean)
    now = time.monotonic()
    should_flush = bool(
        force_flush
        or _text_ends_sentence(clean)
        or pending_chars >= _deepgram_ws_flush_max_chars()
        or (now - float(last_flush_at or 0.0)) >= _deepgram_ws_flush_interval_seconds()
    )
    if should_flush and pending_chars > 0:
        await ws_session.flush()
        return 0, now
    return pending_chars, last_flush_at


def _maybe_extract_speakable_chunk_first(buffer: str, *, min_chars: int = 8) -> tuple[str, str]:
    """First-chunk fast path: use a lower min_chars threshold so audio starts sooner."""
    text = buffer
    max_chars = _tts_chunk_max_chars()
    min_space = _tts_chunk_min_space()
    for punct in (". ", "? ", "! ", "؟ ", "؟", "\n"):
        idx = text.find(punct)
        if idx != -1 and idx >= min_chars:
            cut = idx + len(punct)
            chunk = text[:cut].strip()
            rest = text[cut:].lstrip()
            return chunk, rest

    if len(text) >= max_chars:
        last_space = text.rfind(" ", 0, max_chars + 40)
        if last_space > min_space:
            chunk = text[: last_space + 1].strip()
            rest = text[last_space + 1 :].lstrip()
            return chunk, rest
    return "", buffer


def _maybe_extract_speakable_chunk(buffer: str) -> tuple[str, str]:
    text = buffer
    min_punct = _tts_chunk_min_chars()
    max_chars = _tts_chunk_max_chars()
    min_space = _tts_chunk_min_space()
    for punct in (". ", "? ", "! ", "؟ ", "؟", "\n"):
        idx = text.find(punct)
        if idx != -1 and idx >= min_punct:
            cut = idx + len(punct)
            chunk = text[:cut].strip()
            rest = text[cut:].lstrip()
            return chunk, rest

    if len(text) >= max_chars:
        last_space = text.rfind(" ", 0, max_chars + 40)
        if last_space > min_space:
            chunk = text[: last_space + 1].strip()
            rest = text[last_space + 1 :].lstrip()
            return chunk, rest
    return "", buffer


def _chunk_text_for_tts(text: str) -> list[str]:
    buffer = str(text or "")
    chunks: list[str] = []
    while True:
        chunk, buffer = _maybe_extract_speakable_chunk(buffer)
        if not chunk:
            break
        chunks.append(chunk)
    final = buffer.strip()
    if final:
        chunks.append(final)
    return chunks


def _normalize_lang_for_prompt(stt_language: str) -> str | None:
    lang = (stt_language or "").strip().lower()
    if not lang:
        return None
    if lang.startswith("ar"):
        return "ar"
    if lang.startswith("en"):
        return "en"
    return None


def _stt_language_tags_for_session(*, language: str, country: str, dual_stream_for_arabic: bool) -> list[str]:
    language_norm = (language or "en").strip().lower()
    if language_norm == "ar":
        ar_tag = _arabic_bcp47_for_country(country)
        if dual_stream_for_arabic:
            return [ar_tag, "en"]
        return [ar_tag]
    return ["en"]


def _arabic_bcp47_for_country(country: str) -> str:
    country_norm = (country or "").strip().upper()
    mapping = {
        "EG": "ar-EG",
        "AE": "ar-AE",
        "SA": "ar-SA",
        "QA": "ar-QA",
        "KW": "ar-KW",
        "JO": "ar-JO",
        "OM": "ar-OM",
    }
    return mapping.get(country_norm, "ar")


def _pick_best_utterance(drained: list[dict[str, object]]) -> dict[str, object]:
    def _score(item: dict[str, object]) -> float:
        text = str(item.get("text") or "")
        try:
            confidence = float(item.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        if not text.strip():
            return 0.0
        return confidence * max(1.0, float(len(text.strip())))

    best = drained[0]
    best_score = _score(best)
    for item in drained[1:]:
        score = _score(item)
        if score > best_score:
            best = item
            best_score = score
    return best


def _detect_text_language(text: str, *, fallback: str | None = None) -> str:
    if any("\u0600" <= ch <= "\u06FF" for ch in text):
        return "ar"
    if any("A" <= ch <= "Z" or "a" <= ch <= "z" for ch in text):
        return "en"
    if fallback in {"ar", "en"}:
        return fallback
    return "en"


def _text_ends_sentence(text: str) -> bool:
    stripped = str(text or "").rstrip()
    if not stripped:
        return False
    return bool(re.search(r"""[.!?؟]["')\]]*$""", stripped))


def _has_hangup_action(actions: object) -> bool:
    """
    Accept either:
    - ["hangup"]
    - [{"type": "hangup"}]
    """
    if not actions:
        return False
    if isinstance(actions, str):
        return actions.strip().lower() == "hangup"
    if not isinstance(actions, list):
        return False
    for item in actions:
        if isinstance(item, str) and item.strip().lower() == "hangup":
            return True
        if isinstance(item, dict):
            action_type = str(item.get("type") or item.get("action") or "").strip().lower()
            if action_type == "hangup":
                return True
    return False


def _is_busy_signal(text: str, language_hint: str | None = None) -> bool:
    normalized = (text or "").strip().lower()
    if not normalized:
        return False

    if language_hint and str(language_hint).lower().startswith("ar"):
        ar_tokens = (
            "مشغول",
            "في اجتماع",
            "فى اجتماع",
            "في مكالمة",
            "فى مكالمة",
            "اتصل بعدين",
            "اتصل لاحق",
            "اتصل لاحقًا",
            "كلمك بعدين",
            "مش وقته",
            "مش وقت مناسب",
        )
        return any(token in text for token in ar_tokens)

    # Avoid false positives like "I'm not busy"
    if "not busy" in normalized or "i am not busy" in normalized or "i'm not busy" in normalized or "im not busy" in normalized:
        return False

    patterns = (
        r"\bi[' ]?m busy\b",
        r"\bim busy\b",
        r"\bbusy right now\b",
        r"\bin a meeting\b",
        r"\bin meeting\b",
        r"\bon a call\b",
        r"\bcan't talk\b",
        r"\bcant talk\b",
        r"\bnot a good time\b",
        r"\bcall (me )?back\b",
        r"\bcall back later\b",
        r"\bcall later\b",
        r"\banother time\b",
    )
    return any(re.search(pat, normalized) for pat in patterns)


def _is_wrong_person_strong(text: str, *, language_hint: str | None = None, recipient_name: str = "") -> bool:
    normalized = (text or "").strip().lower()
    if not normalized:
        return False

    if normalized in {"no", "no.", "nope", "nah"}:
        return False

    if language_hint and str(language_hint).lower().startswith("ar"):
        ar_phrases = (
            "رقم غلط",
            "رقم خاطئ",
            "مش انا",
            "مش أنا",
            "مش الشخص",
            "مش هو",
            "مش هي",
            "مش موجود",
            "مش هنا",
        )
        return any(phrase in text for phrase in ar_phrases)

    if re.search(r"\bwrong (number|person)\b", normalized):
        return True
    if re.search(r"\byou (have|got) (the )?wrong\b", normalized):
        return True
    if re.search(r"\bnot (him|her|me)\b", normalized):
        return True
    if re.search(r"\bdoesn'?t (live|work) here\b", normalized):
        return True

    # Name-based strong denial (preferred when we know who we intended to reach).
    name_norm = (recipient_name or "").strip().lower()
    if name_norm:
        name_tokens = [t for t in re.split(r"[^a-z0-9]+", name_norm) if len(t) >= 3]
        if any(tok in normalized for tok in name_tokens) and "not" in normalized:
            if re.search(r"\b(this is|i am|i'?m|im)\s+not\b", normalized):
                return True
            if re.search(r"\bnot\s+(mr|mister|ms|mrs)\b", normalized):
                return True
            return True

    # Generic wrong-person language without name context (avoid matching "I'm not interested/sure").
    if "the person" in normalized and ("not" in normalized or "isn't" in normalized or "isnt" in normalized):
        return True
    if re.search(r"\bnot\s+(mr|mister|ms|mrs)\b", normalized):
        return True

    return False


def _tts_provider() -> str:
    return (os.getenv("VOICE_TTS_PROVIDER") or "elevenlabs").strip().lower()


def _deepgram_ws_flush_interval_seconds() -> float:
    raw = (os.getenv("VOICE_DEEPGRAM_WS_FLUSH_INTERVAL_SECONDS") or "0.30").strip()
    try:
        value = float(raw)
    except Exception:
        value = 0.30
    return max(0.05, min(2.0, value))


def _deepgram_ws_flush_max_chars() -> int:
    raw = (os.getenv("VOICE_DEEPGRAM_WS_FLUSH_MAX_CHARS") or "180").strip()
    try:
        value = int(raw)
    except Exception:
        value = 180
    return max(40, min(600, value))


def _final_stable_ms() -> int:
    raw = (os.getenv("VOICE_STT_FINAL_STABLE_MS") or "700").strip()
    try:
        value = int(raw)
    except Exception:
        value = 700
    return max(50, min(2000, value))


def _barge_in_min_chars() -> int:
    raw = (os.getenv("VOICE_STT_BARGE_IN_MIN_CHARS") or "2").strip()
    try:
        value = int(raw)
    except Exception:
        value = 2
    return max(1, min(40, value))


def _resume_after_barge_in_seconds() -> float:
    raw = (os.getenv("VOICE_CALL_RESUME_AFTER_BARGE_IN_SECONDS") or "1.8").strip()
    try:
        value = float(raw)
    except Exception:
        value = 1.8
    return max(0.5, min(8.0, value))


def _should_barge_in(text: str, *, min_chars: int) -> bool:
    clean = (text or "").strip()
    if not clean:
        return False
    if not any(ch.isalnum() for ch in clean):
        return False
    return len(clean) >= max(1, int(min_chars))


def _no_engagement_hello_seconds() -> float:
    raw = (os.getenv("VOICE_CALL_NO_ENGAGEMENT_HELLO_SECONDS") or "4").strip()
    try:
        value = float(raw)
    except Exception:
        value = 4.0
    return max(0.5, min(20.0, value))


def _no_engagement_goodbye_seconds() -> float:
    raw = (os.getenv("VOICE_CALL_NO_ENGAGEMENT_GOODBYE_SECONDS") or "4").strip()
    try:
        value = float(raw)
    except Exception:
        value = 4.0
    return max(0.5, min(30.0, value))


def _silence_close_seconds() -> float:
    raw = (os.getenv("VOICE_CALL_SILENCE_CLOSE_SECONDS") or "7").strip()
    try:
        value = float(raw)
    except Exception:
        value = 7.0
    return max(2.0, min(60.0, value))


def _silence_hangup_after_close_seconds() -> float:
    raw = (os.getenv("VOICE_CALL_SILENCE_HANGUP_AFTER_CLOSE_SECONDS") or "7").strip()
    try:
        value = float(raw)
    except Exception:
        value = 7.0
    return max(2.0, min(120.0, value))


def _merge_transcript_segments(existing: str, incoming: str) -> str:
    """
    Merge STT segments that may arrive as multiple finals for a single user
    utterance.

    Deepgram sometimes emits a short final (e.g., "I am not") followed by
    additional finals/interims as the customer continues speaking. This helper
    tries to keep a single, coherent utterance without duplicating text.
    """
    left = (existing or "").strip()
    right = (incoming or "").strip()
    if not right:
        return left
    if not left:
        return right

    left_norm = " ".join(left.split())
    right_norm = " ".join(right.split())

    if right_norm == left_norm:
        return left
    if right_norm.startswith(left_norm):
        return incoming.strip()
    if left_norm.startswith(right_norm):
        return left

    joined = f"{left.rstrip()} {right.lstrip()}".strip()
    return joined


def _tts_chunk_min_chars() -> int:
    raw = (os.getenv("VOICE_TTS_CHUNK_MIN_CHARS") or "20").strip()
    try:
        value = int(raw)
    except Exception:
        value = 20
    return max(6, min(80, value))


def _tts_chunk_max_chars() -> int:
    raw = (os.getenv("VOICE_TTS_CHUNK_MAX_CHARS") or "100").strip()
    try:
        value = int(raw)
    except Exception:
        value = 100
    return max(40, min(240, value))


def _tts_chunk_min_space() -> int:
    raw = (os.getenv("VOICE_TTS_CHUNK_MIN_SPACE") or "30").strip()
    try:
        value = int(raw)
    except Exception:
        value = 30
    return max(10, min(120, value))


def _should_skip_duplicate(text: str, *, last_text: str, last_at: float) -> bool:
    if not last_text:
        return False
    if not text:
        return True
    now = time.monotonic()
    if now - last_at > 6.0:
        return False
    if text == last_text:
        return True
    if text.startswith(last_text) and (len(text) - len(last_text)) <= 8:
        return True
    return False


def _clip_text(text: str, max_chars: int) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    if max_chars <= 0:
        return clean
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars].rstrip() + "…"


def _context_block_max_chars() -> int:
    raw = (os.getenv("VOICE_CALL_CONTEXT_MAX_CHARS") or "1200").strip()
    try:
        value = int(raw)
    except Exception:
        value = 1200
    return max(200, min(4000, value))


def _context_summary_max_chars() -> int:
    raw = (os.getenv("VOICE_CALL_CONTEXT_SUMMARY_MAX_CHARS") or "600").strip()
    try:
        value = int(raw)
    except Exception:
        value = 600
    return max(100, min(2000, value))


def _context_recent_messages() -> int:
    raw = (os.getenv("VOICE_CALL_CONTEXT_RECENT_MESSAGES") or "6").strip()
    try:
        value = int(raw)
    except Exception:
        value = 6
    return max(0, min(20, value))


def _context_message_max_chars() -> int:
    raw = (os.getenv("VOICE_CALL_CONTEXT_MESSAGE_MAX_CHARS") or "200").strip()
    try:
        value = int(raw)
    except Exception:
        value = 200
    return max(80, min(600, value))


def _context_items_max_items() -> int:
    raw = (os.getenv("VOICE_CALL_CONTEXT_ITEMS_MAX") or "8").strip()
    try:
        value = int(raw)
    except Exception:
        value = 8
    return max(1, min(20, value))


def _context_item_max_chars() -> int:
    raw = (os.getenv("VOICE_CALL_CONTEXT_ITEM_MAX_CHARS") or "220").strip()
    try:
        value = int(raw)
    except Exception:
        value = 220
    return max(80, min(600, value))


def _format_context_items(items: object) -> str:
    if not isinstance(items, list) or not items:
        return ""
    max_items = _context_items_max_items()
    lines: list[str] = []
    for item in items[:max_items]:
        text = _stringify_context_item(item)
        text = _clip_text(text, _context_item_max_chars())
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines).strip()


def _stringify_context_item(item: object) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        title = str(item.get("title") or item.get("label") or item.get("name") or "").strip()
        value = str(item.get("value") or item.get("content") or item.get("text") or "").strip()
        if title and value:
            return f"{title}: {value}"
        if value:
            return value
        if title:
            return title
        parts = []
        for key, val in list(item.items())[:3]:
            key_text = str(key).strip()
            val_text = str(val).strip()
            if key_text and val_text:
                parts.append(f"{key_text}: {val_text}")
        return "; ".join(parts).strip()
    return str(item or "").strip()


def _extract_recipient_name(context_items: object) -> str:
    """Extract recipient/customer name from context_items if available."""
    if not isinstance(context_items, list):
        return ""
    name_keys = {"name", "customer_name", "recipient_name", "contact_name", "customer", "recipient", "اسم", "العميل"}
    for item in context_items:
        if isinstance(item, dict):
            for key in name_keys:
                val = item.get(key)
                if val and isinstance(val, str) and val.strip():
                    return val.strip()
            # Also check title/label patterns like {"title": "Customer Name", "value": "John"}
            title = str(item.get("title") or item.get("label") or "").strip().lower()
            if any(k in title for k in ("name", "customer", "recipient", "اسم", "عميل")):
                val = str(item.get("value") or item.get("content") or item.get("text") or "").strip()
                if val:
                    return val
    return ""
