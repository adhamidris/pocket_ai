from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import AsyncIterator, Callable

from asgiref.sync import sync_to_async

from apps.llm.ai_prompt_builder import PromptBundle
from apps.llm.llm_provider import DeepSeekChatProvider, OpenAIChatProvider, _ResponseTextExtractor, load_default_provider
from apps.voice.audio_frames import iter_audio_frames
from apps.voice.models import CallSession
from apps.voice.voice_spike.deepgram_stt import DeepgramConfig, deepgram_transcripts
from apps.voice.voice_spike.elevenlabs_tts import ElevenLabsConfig, stream_tts_audio


logger = logging.getLogger(__name__)


@dataclass
class VoiceCallRuntimeConfig:
    max_history_turns: int = 8
    llm_timeout_s: float = 45.0


class VoiceCallRuntime:
    """
    Production-shaped Twilio Media Streams runtime.

    Phase 1 keeps this close to the Phase 0 spike while standardizing event
    types and emitting final assistant responses for post-call processing.
    """

    def __init__(self, *, session_id: str, runtime_config: VoiceCallRuntimeConfig | None = None) -> None:
        self.session_id = session_id
        self.runtime_config = runtime_config or VoiceCallRuntimeConfig()
        self._stream_sid: str | None = None
        self._history: list[tuple[str, str]] = []  # ("customer"|"agent", text)
        self._current_speak_task: asyncio.Task | None = None
        self._greeted: bool = False
        self._goal_delivered: bool = False
        self._goal_acknowledged: bool = False
        self._interim_task: asyncio.Task | None = None
        self._interim_version: int = 0
        self._last_handled_text: str = ""
        self._last_handled_at: float = 0.0

    async def run_twilio_stream(self, twilio_ws) -> None:
        session = await self._get_session()
        stop_event = asyncio.Event()

        utterance_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        stt_audio_queues: list[asyncio.Queue[bytes]] = []
        stt_tasks: list[asyncio.Task] = []

        def _enable_dual_stream_for_arabic() -> bool:
            raw = (os.getenv("VOICE_STT_DUAL_STREAM_AR_EN") or "").strip().lower()
            if raw:
                return raw in {"1", "true", "yes"}
            return True

        stt_languages = _stt_language_tags_for_session(
            language=(session.language or "en").strip().lower(),
            country=(session.country or "").strip().upper(),
            dual_stream_for_arabic=_enable_dual_stream_for_arabic(),
        )

        for lang_tag in stt_languages:
            audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
            stt_audio_queues.append(audio_queue)

            async def audio_source(*, _queue: asyncio.Queue[bytes] = audio_queue) -> AsyncIterator[bytes]:
                while not stop_event.is_set():
                    chunk = await _queue.get()
                    if chunk is None:  # type: ignore[comparison-overlap]
                        break
                    yield chunk

            async def stt_loop(*, _lang: str = lang_tag, _source: Callable[[], AsyncIterator[bytes]] = audio_source) -> None:
                try:
                    dg_config = DeepgramConfig.from_env(language=_lang)
                    async for payload in deepgram_transcripts(config=dg_config, audio_source=_source()):
                        text, confidence, is_final = _extract_transcript_with_confidence(payload)
                        if text:
                            await utterance_queue.put(
                                {
                                    "text": text,
                                    "confidence": confidence,
                                    "stt_language": _lang,
                                    "is_final": is_final,
                                }
                            )
                except Exception as exc:
                    logger.exception("Deepgram STT loop crashed lang=%s: %s", _lang, exc)

            stt_tasks.append(asyncio.create_task(stt_loop()))

        try:
            async for raw_message in twilio_ws:
                if not raw_message:
                    continue
                try:
                    message = json.loads(raw_message)
                except Exception:
                    continue

                event = message.get("event")
                if event == "start":
                    start = message.get("start") or {}
                    self._stream_sid = str(start.get("streamSid") or "")
                    call_sid = str(start.get("callSid") or "")
                    await self._update_stream_ids(stream_sid=self._stream_sid or "", call_sid=call_sid)
                    await self._log_event("twilio.stream.start", {"call_sid": call_sid, "stream_sid": self._stream_sid})
                    await self._maybe_greet(twilio_ws)
                    continue
                if event == "stop":
                    await self._log_event("twilio.stream.stop", {})
                    stop_event.set()
                    break
                if event == "media":
                    media = message.get("media") or {}
                    payload_b64 = media.get("payload")
                    if not isinstance(payload_b64, str):
                        continue
                    try:
                        audio = base64.b64decode(payload_b64)
                    except Exception:
                        continue

                    for queue in stt_audio_queues:
                        try:
                            queue.put_nowait(audio)
                        except asyncio.QueueFull:
                            pass

                    await self._drain_utterances(utterance_queue, twilio_ws)
                    continue
        finally:
            stop_event.set()
            for queue in stt_audio_queues:
                try:
                    queue.put_nowait(b"")
                except Exception:
                    pass
            if self._interim_task:
                self._interim_task.cancel()
                self._interim_task = None
            if self._current_speak_task:
                self._current_speak_task.cancel()
            for task in stt_tasks:
                task.cancel()

    async def _drain_utterances(self, utterance_queue: asyncio.Queue[dict[str, object]], twilio_ws) -> None:
        drained: list[dict[str, object]] = []
        while True:
            try:
                drained.append(utterance_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not drained:
            return

        final_items = [item for item in drained if bool(item.get("is_final"))]
        interim_items = [item for item in drained if not bool(item.get("is_final"))]

        if final_items:
            if self._interim_task:
                self._interim_task.cancel()
                self._interim_task = None
            best = _pick_best_utterance(final_items)
            await self._handle_transcript(best, twilio_ws, source="final")
            return

        if interim_items:
            best = _pick_best_utterance(interim_items)
            text = str(best.get("text") or "").strip()
            if not text:
                return
            min_chars = _interim_min_chars()
            if len(text) < min_chars:
                return
            self._interim_version += 1
            version = self._interim_version
            if self._interim_task:
                self._interim_task.cancel()
            self._interim_task = asyncio.create_task(self._debounced_interim(best, twilio_ws, version))

    async def _interrupt_speech(self, twilio_ws) -> None:
        if self._current_speak_task and not self._current_speak_task.done():
            self._current_speak_task.cancel()
        if self._stream_sid:
            await _twilio_send(twilio_ws, {"event": "clear", "streamSid": self._stream_sid})

    async def _maybe_greet(self, twilio_ws) -> None:
        if self._greeted:
            return
        session = await self._get_session()
        if not session.consent_obtained:
            return
        self._greeted = True
        await self._interrupt_speech(twilio_ws)
        lang_hint = _normalize_lang_for_prompt((session.language or "").strip().lower())
        self._current_speak_task = asyncio.create_task(
            self._respond_and_speak(twilio_ws, customer_text="", customer_language=lang_hint, is_greeting=True)
        )

    async def _respond_and_speak(
        self,
        twilio_ws,
        *,
        customer_text: str,
        customer_language: str | None = None,
        is_greeting: bool = False,
    ) -> None:
        session = await self._get_session()
        if not session.consent_obtained:
            await self._log_event("guard.no_consent", {})
            return

        provider = load_default_provider()
        if not provider:
            await self._log_event("llm.disabled", {})
            return

        system_prompt = (
            "You are a phone-call agent for a business. Be natural, concise, and helpful.\n"
            "Never hallucinate. If you don't know something, say you'll check and follow up.\n"
            "Language policy: respond in the customer's language (Arabic or English). If the customer code-switches, you may code-switch.\n"
            "If speaking Arabic, prefer clear Modern Standard Arabic unless the customer uses a dialect.\n"
            "Ask short clarifying questions when needed.\n"
            "Return JSON with keys: response_text (string), actions (empty array), extractions (empty array).\n"
        )
        history_lines = "\n".join(f"{role}: {text}" for role, text in self._history[-12:])
        customer_language_hint = (customer_language or session.language or "").strip().lower()
        deliver_goal = is_greeting or not self._goal_delivered
        if is_greeting:
            user_prompt = (
                f"Call objective: {session.objective}\n"
                f"Workspace default language: {session.language}\n"
                f"Customer language hint: {customer_language_hint}\n"
                f"Country: {session.country}\n\n"
                "Conversation so far:\n"
                f"{history_lines}\n\n"
                f"Goal status: delivered={self._goal_delivered}, acknowledged={self._goal_acknowledged}\n\n"
                "No customer speech yet. Start the call with a brief greeting and explicitly deliver the objective. "
                "Ask for confirmation/acknowledgement, then a short opening question.\n"
            )
        else:
            user_prompt = (
                f"Call objective: {session.objective}\n"
                f"Workspace default language: {session.language}\n"
                f"Customer language hint: {customer_language_hint}\n"
                f"Country: {session.country}\n\n"
                "Conversation so far:\n"
                f"{history_lines}\n\n"
                f"Customer just said: {customer_text}\n\n"
                f"Goal status: delivered={self._goal_delivered}, acknowledged={self._goal_acknowledged}\n"
                "If the objective has not been delivered yet, deliver it now in one concise sentence. "
                "If delivered but not acknowledged, ask for acknowledgement before moving on.\n"
            )
        bundle = PromptBundle(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            transcript=[],
            knowledge_snippets=[],
            actions_catalog=[],
            agent_traits={},
        )

        loop = asyncio.get_running_loop()
        delta_queue: asyncio.Queue[str] = asyncio.Queue()
        response_text_parts: list[str] = []
        llm_result: dict | None = None

        def _emit_text(delta: str) -> None:
            if not delta:
                return
            response_text_parts.append(delta)
            loop.call_soon_threadsafe(delta_queue.put_nowait, delta)

        def _build_stream_callback() -> Callable[[str], None]:
            if isinstance(provider, DeepSeekChatProvider):
                return _emit_text
            if isinstance(provider, OpenAIChatProvider):
                extractor = _ResponseTextExtractor(_emit_text)

                def _on_delta(json_delta: str) -> None:
                    extractor.feed(json_delta)

                return _on_delta
            return _emit_text

        stream_cb = _build_stream_callback()

        async def _llm_thread() -> None:
            nonlocal llm_result
            try:
                llm_result = await asyncio.to_thread(provider.generate, bundle, on_stream_delta=stream_cb)
            except Exception as exc:
                await self._log_event("llm.error", {"error": str(exc)})
            finally:
                loop.call_soon_threadsafe(delta_queue.put_nowait, "")

        llm_task = asyncio.create_task(_llm_thread())

        try:
            tts_config_en = ElevenLabsConfig.from_env(language="en")
            tts_config_ar = ElevenLabsConfig.from_env(language="ar")
        except Exception as exc:
            await self._log_event("tts.disabled", {"error": str(exc)})
            await llm_task
            return

        buffer = ""
        spoke_any = False
        try:
            while True:
                delta = await delta_queue.get()
                if delta == "":
                    break
                buffer += delta
                chunk, buffer = _maybe_extract_speakable_chunk(buffer)
                if chunk:
                    await self._log_event("tts.chunk", {"text": chunk})
                    tts_lang = _detect_text_language(chunk, fallback=customer_language_hint)
                    tts_cfg = tts_config_ar if tts_lang == "ar" else tts_config_en
                    await self._stream_tts_to_twilio(twilio_ws, chunk, config=tts_cfg)
                    spoke_any = True

            final = buffer.strip()
            if final:
                await self._log_event("tts.chunk.final", {"text": final})
                tts_lang = _detect_text_language(final, fallback=customer_language_hint)
                tts_cfg = tts_config_ar if tts_lang == "ar" else tts_config_en
                await self._stream_tts_to_twilio(twilio_ws, final, config=tts_cfg)
                spoke_any = True
            if deliver_goal and spoke_any:
                if not self._goal_delivered:
                    self._goal_delivered = True
                    await self._log_event("goal.delivered", {"objective": session.objective})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._log_event("tts.error", {"error": str(exc)})
        finally:
            await llm_task
            full_text = ""
            if isinstance(llm_result, dict):
                full_text = str(llm_result.get("response_text") or "").strip()
                usage = llm_result.get("llm_usage")
                if full_text:
                    await self._log_event("llm.response.final", {"text": full_text, "llm_usage": usage or {}})
                if not full_text:
                    full_text = "".join(response_text_parts).strip()
                if full_text:
                    self._history.append(("agent", full_text))
                    self._history = self._history[-(self.runtime_config.max_history_turns * 2) :]

    async def _stream_tts_to_twilio(self, twilio_ws, text: str, *, config: ElevenLabsConfig) -> None:
        if not self._stream_sid:
            return
        frame_ms = int((os.getenv("VOICE_TTS_FRAME_MS") or "20").strip() or 20)
        pace_raw = (os.getenv("VOICE_TTS_PACE") or "").strip().lower()
        pace = False if pace_raw in {"0", "false", "no"} else True
        async for frame in iter_audio_frames(
            stream_tts_audio(text, config=config),
            output_format=config.output_format,
            frame_ms=frame_ms,
        ):
            payload = base64.b64encode(frame).decode("ascii")
            await _twilio_send(twilio_ws, {"event": "media", "streamSid": self._stream_sid, "media": {"payload": payload}})
            if pace:
                await asyncio.sleep(frame_ms / 1000.0)

    async def _get_session(self) -> CallSession:
        return await sync_to_async(CallSession.objects.get)(id=self.session_id)

    async def _update_stream_ids(self, *, stream_sid: str, call_sid: str) -> None:
        def _update() -> None:
            CallSession.objects.filter(id=self.session_id).update(twilio_stream_sid=stream_sid, twilio_call_sid=call_sid)

        await sync_to_async(_update)()

    async def _log_event(self, event_type: str, payload: dict) -> None:
        def _create() -> None:
            session = CallSession.objects.get(id=self.session_id)
            session.events.create(event_type=event_type, payload=payload)

        await sync_to_async(_create)()

    async def _handle_transcript(self, payload: dict[str, object], twilio_ws, *, source: str) -> None:
        text = str(payload.get("text") or "").strip()
        if not text:
            return
        if _should_skip_duplicate(text, last_text=self._last_handled_text, last_at=self._last_handled_at):
            return
        confidence = float(payload.get("confidence") or 0.0)
        stt_language = str(payload.get("stt_language") or "").strip()
        event_type = "stt.final" if source == "final" else "stt.interim"
        await self._log_event(event_type, {"text": text, "confidence": confidence, "stt_language": stt_language})
        if self._goal_delivered and not self._goal_acknowledged:
            if _is_acknowledgement(text, stt_language or "en"):
                self._goal_acknowledged = True
                await self._log_event("goal.acknowledged", {"text": text})
        self._history.append(("customer", text))
        self._history = self._history[-(self.runtime_config.max_history_turns * 2) :]
        self._last_handled_text = text
        self._last_handled_at = time.monotonic()

        await self._interrupt_speech(twilio_ws)
        self._current_speak_task = asyncio.create_task(
            self._respond_and_speak(twilio_ws, customer_text=text, customer_language=_normalize_lang_for_prompt(stt_language))
        )

    async def _debounced_interim(self, payload: dict[str, object], twilio_ws, version: int) -> None:
        delay_ms = _interim_stable_ms()
        await asyncio.sleep(delay_ms / 1000.0)
        if version != self._interim_version:
            return
        await self._handle_transcript(payload, twilio_ws, source="interim")


async def _twilio_send(ws, payload: dict) -> None:
    await ws.send(json.dumps(payload))


def _extract_final_transcript(payload: dict) -> str:
    if payload.get("type") == "UtteranceEnd":
        return ""
    is_final = bool(payload.get("is_final") or payload.get("speech_final"))
    if not is_final:
        return ""
    channel = payload.get("channel") or {}
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
    channel = payload.get("channel") or {}
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
    channel = payload.get("channel") or {}
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


def _is_acknowledgement(text: str, language_hint: str | None = None) -> bool:
    normalized = (text or "").strip().lower()
    if not normalized:
        return False
    if language_hint and language_hint.lower().startswith("ar"):
        ar_tokens = {
            "نعم",
            "تمام",
            "حاضر",
            "اوكي",
            "أوكي",
            "ماشي",
            "موافق",
            "فهمت",
            "تمام فهمت",
            "شكرا",
            "شكرًا",
        }
        return any(token in text for token in ar_tokens)
    en_tokens = {
        "yes",
        "yeah",
        "yep",
        "ok",
        "okay",
        "sure",
        "alright",
        "sounds good",
        "got it",
        "understood",
        "i understand",
        "fine",
        "thanks",
        "thank you",
    }
    return any(token in normalized for token in en_tokens)


def _interim_min_chars() -> int:
    raw = (os.getenv("VOICE_STT_INTERIM_MIN_CHARS") or "12").strip()
    try:
        value = int(raw)
    except Exception:
        value = 12
    return max(4, min(60, value))


def _interim_stable_ms() -> int:
    raw = (os.getenv("VOICE_STT_INTERIM_STABLE_MS") or "450").strip()
    try:
        value = int(raw)
    except Exception:
        value = 450
    return max(120, min(2000, value))


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
