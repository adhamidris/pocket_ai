from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Mapping

from asgiref.sync import sync_to_async

from apps.conversations.models import ConversationMessage
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
        self._identity_confirmed: bool = False  # Identity confirmation flow
        self._goal_delivered: bool = False
        self._goal_acknowledged: bool = False
        self._interim_task: asyncio.Task | None = None
        self._interim_version: int = 0
        self._last_handled_text: str = ""
        self._last_handled_at: float = 0.0
        self._context_loaded: bool = False
        self._context_block: str = ""
        self._session_cache: CallSession | None = None  # Cache session to avoid repeated DB queries

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

        # If no recipient name in context, skip identity confirmation phase
        recipient_name = _extract_recipient_name(session.context_items)
        if not recipient_name:
            self._identity_confirmed = True

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
            "Never hallucinate. Never invent prices, fees, policies, dates, or promises.\n"
            "Only state facts that are explicitly present in the provided context or said by the customer.\n"
            "If you don't have confirmed information, say you don't have it and offer a follow-up call.\n"
            "Language policy: respond in the customer's language (Arabic or English). If the customer code-switches, you may code-switch.\n"
            "If speaking Arabic, prefer clear Modern Standard Arabic unless the customer uses a dialect.\n"
            "Ask short clarifying questions when needed.\n"
            "Return JSON with keys: response_text (string), actions (empty array), extractions (empty array).\n"
        )
        history_lines = "\n".join(f"{role}: {text}" for role, text in self._history[-12:])
        customer_language_hint = (customer_language or session.language or "").strip().lower()
        context_block = await self._build_context_block()

        # Extract recipient name for identity confirmation flow
        recipient_name = _extract_recipient_name(session.context_items)
        identity_status = f"identity_confirmed={self._identity_confirmed}"
        goal_status = f"goal_delivered={self._goal_delivered}, goal_acknowledged={self._goal_acknowledged}"

        if is_greeting:
            # Phase 1: Ask for identity confirmation first
            if recipient_name:
                greeting_instruction = (
                    f"No customer speech yet. Greet briefly and ask to confirm identity: "
                    f"'Hello, am I speaking with {recipient_name}?' or equivalent in the appropriate language. "
                    "Do NOT deliver the objective yet - wait for identity confirmation first."
                )
            else:
                # No recipient name, skip identity confirmation
                greeting_instruction = (
                    "No customer speech yet. Start the call with a brief greeting and deliver the objective. "
                    "Ask for confirmation/acknowledgement."
                )
            user_prompt = (
                f"Call objective: {session.objective}\n"
                f"Workspace default language: {session.language}\n"
                f"Customer language hint: {customer_language_hint}\n"
                f"Country: {session.country}\n\n"
                f"{context_block}\n\n"
                "Conversation so far:\n"
                f"{history_lines}\n\n"
                f"Status: {identity_status}, {goal_status}\n\n"
                f"{greeting_instruction}\n"
            )
        else:
            # Phase 2+: Handle based on identity and goal status
            if not self._identity_confirmed and recipient_name:
                # Awaiting identity confirmation
                user_prompt = (
                    f"Call objective: {session.objective}\n"
                    f"Workspace default language: {session.language}\n"
                    f"Customer language hint: {customer_language_hint}\n"
                    f"Country: {session.country}\n\n"
                    f"{context_block}\n\n"
                    "Conversation so far:\n"
                    f"{history_lines}\n\n"
                    f"Customer just said: {customer_text}\n\n"
                    f"Status: {identity_status}, {goal_status}\n\n"
                    "If the customer confirmed their identity (e.g., 'yes', 'speaking'), now deliver the objective. "
                    "If the customer denied or said 'wrong number', apologize politely and indicate the call will end. "
                    "If unclear, politely ask again to confirm if this is the right person.\n"
                )
            elif not self._goal_delivered:
                # Identity confirmed (or no name to check), deliver objective
                user_prompt = (
                    f"Call objective: {session.objective}\n"
                    f"Workspace default language: {session.language}\n"
                    f"Customer language hint: {customer_language_hint}\n"
                    f"Country: {session.country}\n\n"
                    f"{context_block}\n\n"
                    "Conversation so far:\n"
                    f"{history_lines}\n\n"
                    f"Customer just said: {customer_text}\n\n"
                    f"Status: {identity_status}, {goal_status}\n\n"
                    "Now deliver the objective in one concise sentence and ask for acknowledgement.\n"
                )
            else:
                # Objective delivered, continue conversation
                user_prompt = (
                    f"Call objective: {session.objective}\n"
                    f"Workspace default language: {session.language}\n"
                    f"Customer language hint: {customer_language_hint}\n"
                    f"Country: {session.country}\n\n"
                    f"{context_block}\n\n"
                    "Conversation so far:\n"
                    f"{history_lines}\n\n"
                    f"Customer just said: {customer_text}\n\n"
                    f"Status: {identity_status}, {goal_status}\n\n"
                    "If the goal has not been acknowledged, ask for acknowledgement. "
                    "Otherwise, continue the conversation naturally and helpfully.\n"
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

    async def _get_session(self, *, force_refresh: bool = False) -> CallSession:
        """Get session with caching to reduce DB queries in critical path."""
        if self._session_cache is not None and not force_refresh:
            return self._session_cache
        session = await sync_to_async(CallSession.objects.get)(id=self.session_id)
        self._session_cache = session
        return session

    async def _update_stream_ids(self, *, stream_sid: str, call_sid: str) -> None:
        def _update() -> None:
            CallSession.objects.filter(id=self.session_id).update(twilio_stream_sid=stream_sid, twilio_call_sid=call_sid)

        await sync_to_async(_update)()

    async def _log_event(self, event_type: str, payload: dict, *, blocking: bool = False) -> None:
        """
        Log call event to database.

        By default, runs in fire-and-forget mode to avoid blocking the critical path.
        Set blocking=True for events that must complete before proceeding.
        """
        session_id = self.session_id
        conversation_id = None
        if self._session_cache:
            conversation_id = getattr(self._session_cache, "initiating_conversation_id", None)

        def _create() -> CallSession | None:
            try:
                session = CallSession.objects.get(id=session_id)
                session.events.create(event_type=event_type, payload=payload)
                return session
            except Exception:
                logger.exception("Failed to log event %s", event_type)
                return None

        if blocking:
            session = await sync_to_async(_create)()
            if session and event_type in ("stt.final", "llm.response.final"):
                await self._broadcast_transcript_sse(session, event_type, payload)
        else:
            # Fire-and-forget: don't block the critical path
            async def _log_async() -> None:
                session = await sync_to_async(_create)()
                if session and event_type in ("stt.final", "llm.response.final"):
                    # Broadcast to SSE (also fire-and-forget)
                    asyncio.create_task(self._broadcast_transcript_sse(session, event_type, payload))

            asyncio.create_task(_log_async())

    async def _broadcast_transcript_sse(self, session: CallSession, event_type: str, payload: dict) -> None:
        """Push transcript events to cache for SSE polling."""
        from django.core.cache import cache

        conversation_id = getattr(session, "initiating_conversation_id", None)
        if not conversation_id:
            return

        role = "customer" if event_type == "stt.final" else "agent"
        text = str(payload.get("text") or "").strip()
        if not text:
            return

        cache_key = f"voice_transcript:{conversation_id}"
        event_data = {
            "session_id": str(session.id),
            "event_type": event_type,
            "role": role,
            "text": text,
            "timestamp": time.time(),
        }

        def _push_to_cache() -> None:
            existing = cache.get(cache_key) or []
            if not isinstance(existing, list):
                existing = []
            existing.append(event_data)
            # Keep only recent events (last 100)
            if len(existing) > 100:
                existing = existing[-100:]
            cache.set(cache_key, existing, timeout=300)  # 5 minute TTL

        await sync_to_async(_push_to_cache)()

    async def _build_context_block(self) -> str:
        if self._context_loaded:
            return self._context_block
        self._context_loaded = True

        def _load() -> str:
            call_session = (
                CallSession.objects.select_related("agent_profile", "initiating_conversation")
                .filter(id=self.session_id)
                .first()
            )
            if not call_session:
                return ""
            parts: list[str] = []

            agent = call_session.agent_profile
            if agent:
                persona_lines = []
                if agent.name:
                    persona_lines.append(f"Name: {agent.name}")
                if agent.role:
                    persona_lines.append(f"Role: {agent.role}")
                if agent.tone:
                    persona_lines.append(f"Tone: {agent.tone}")
                if agent.traits:
                    traits = ", ".join(str(t).strip() for t in agent.traits if str(t).strip())
                    if traits:
                        persona_lines.append(f"Traits: {traits}")
                if persona_lines:
                    parts.append("Agent persona:\n" + "\n".join(persona_lines))

            context_items = _format_context_items(call_session.context_items)
            if context_items:
                parts.append("Call context items:\n" + context_items)

            convo = call_session.initiating_conversation
            summary = ""
            if convo and convo.summary:
                summary = _clip_text(str(convo.summary), _context_summary_max_chars())
                if summary:
                    parts.append("Conversation summary:\n" + summary)

            if convo:
                max_messages = _context_recent_messages()
                if max_messages > 0:
                    messages = (
                        ConversationMessage.objects.filter(conversation_id=convo.id)
                        .order_by("-sent_at", "-created_at")
                        .values_list("sender", "body")[: max_messages]
                    )
                    if messages:
                        lines = []
                        for sender, body in reversed(list(messages)):
                            sender_label = str(sender or "unknown")
                            body_text = _clip_text(str(body or ""), _context_message_max_chars())
                            if body_text:
                                lines.append(f"{sender_label}: {body_text}")
                        if lines:
                            parts.append("Recent conversation messages:\n" + "\n".join(lines))

            block = "\n\n".join(parts).strip()
            if not block:
                return ""
            max_chars = _context_block_max_chars()
            return "Context:\n" + _clip_text(block, max_chars)

        self._context_block = await sync_to_async(_load)()
        return self._context_block

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

        # Identity confirmation flow: check before goal acknowledgement
        if not self._identity_confirmed:
            if _is_identity_confirmation(text, stt_language or "en"):
                self._identity_confirmed = True
                await self._log_event("identity.confirmed", {"text": text})
            elif _is_identity_denial(text, stt_language or "en"):
                await self._log_event("identity.denied", {"text": text})
                # The LLM prompt will handle the apology and call ending

        # Goal acknowledgement (only relevant after identity is confirmed or no name check needed)
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


def _is_identity_confirmation(text: str, language_hint: str | None = None) -> bool:
    """Detect if customer confirms their identity (e.g., 'yes', 'speaking', 'this is X')."""
    normalized = (text or "").strip().lower()
    if not normalized:
        return False

    # Arabic identity confirmations
    if language_hint and language_hint.lower().startswith("ar"):
        ar_tokens = {
            "نعم",
            "أيوا",
            "ايوا",
            "أيوه",
            "ايوه",
            "معاك",
            "معك",
            "أنا",
            "انا",
            "نفسي",
            "بنفسي",
            "موجود",
            "حاضر",
            "تمام",
            "صحيح",
        }
        return any(token in text for token in ar_tokens)

    # English identity confirmations
    en_tokens = {
        "yes",
        "yeah",
        "yep",
        "speaking",
        "this is",
        "that's me",
        "thats me",
        "it's me",
        "its me",
        "i am",
        "correct",
        "right",
        "here",
        "present",
    }
    return any(token in normalized for token in en_tokens)


def _is_identity_denial(text: str, language_hint: str | None = None) -> bool:
    """Detect if customer denies their identity (e.g., 'wrong number', 'not me')."""
    normalized = (text or "").strip().lower()
    if not normalized:
        return False

    # Arabic identity denials
    if language_hint and language_hint.lower().startswith("ar"):
        ar_tokens = {
            "رقم غلط",
            "غلط",
            "مش انا",
            "مش أنا",
            "لأ",
            "لا",
            "رقم خاطئ",
            "خطأ",
            "مفيش",
            "مش هنا",
            "مش موجود",
        }
        return any(token in text for token in ar_tokens)

    # English identity denials
    en_tokens = {
        "wrong number",
        "not me",
        "no",
        "nope",
        "wrong person",
        "who",
        "who is this",
        "not here",
        "doesn't live here",
        "doesn't live",
        "not available",
        "you have the wrong",
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
