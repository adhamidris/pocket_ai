from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Mapping

import requests
from asgiref.sync import sync_to_async

from apps.conversations.models import ConversationMessage
from apps.llm.ai_prompt_builder import PromptBundle
from apps.llm.llm_provider import DeepSeekChatProvider, OpenAIChatProvider, _ResponseTextExtractor, load_default_provider
from apps.voice.audio_frames import iter_audio_frames
from apps.voice.models import CallSession
from apps.voice.deepgram_stt import DeepgramConfig, deepgram_transcripts
from apps.voice.elevenlabs_tts import ElevenLabsConfig, stream_tts_audio
from apps.voice.provider_credentials import (
    resolve_deepgram_config,
    resolve_elevenlabs_config,
    resolve_twilio_config,
)


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
        self._agent_response_seq: int = 0
        self._stream_sid: str | None = None
        self._history: list[tuple[str, str]] = []  # ("customer"|"agent", text)
        self._current_speak_task: asyncio.Task | None = None
        self._speaking_response_id: int = 0
        self._greeted: bool = False
        self._goal_delivered: bool = False
        self._last_handled_text: str = ""
        self._last_handled_at: float = 0.0
        self._context_loaded: bool = False
        self._context_block: str = ""
        self._session_cache: CallSession | None = None  # Cache session to avoid repeated DB queries
        self._pending_final_text: str = ""
        self._pending_final_confidence: float = 0.0
        self._pending_final_language: str = ""
        self._pending_final_updated_at: float = 0.0  # monotonic
        self._pending_final_task: asyncio.Task | None = None
        self._last_customer_activity_at: float = 0.0  # monotonic
        self._last_customer_final_at: float = 0.0  # monotonic
        self._last_agent_speech_end_at: float = 0.0  # monotonic
        self._customer_speaking: bool = False
        self._last_vad_speech_started_at: float = 0.0  # monotonic
        self._last_vad_utterance_end_at: float = 0.0  # monotonic
        self._awaiting_first_customer: bool = False
        self._no_engagement_task: asyncio.Task | None = None
        self._silence_close_task: asyncio.Task | None = None
        self._closing_waiting_for_customer: bool = False
        self._closing_question_asked_at: float = 0.0  # monotonic
        self._waiting_for_callback_time: bool = False
        self._callback_time_text: str = ""
        self._auto_resume_task: asyncio.Task | None = None
        self._auto_resume_text: str = ""
        self._auto_resume_response_id: int = 0
        self._auto_resume_marks_goal_delivered: bool = False
        self._auto_resume_expected_response_id: int = 0
        self._auto_resume_expected_barge_in_at: float = 0.0  # monotonic
        self._interrupted_agent_remaining: str = ""
        self._interrupted_agent_remaining_at: float = 0.0  # monotonic
        self._interrupted_agent_response_id: int = 0
        self._interrupted_agent_marks_goal_delivered: bool = False
        self._last_barge_in_at: float = 0.0  # monotonic
        self._last_barge_in_text: str = ""
        self._terminated: bool = False
        self._stt_config_cache: dict[str, DeepgramConfig] = {}
        self._tts_config_cache: dict[str, ElevenLabsConfig] = {}

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
                    dg_config = await self._get_deepgram_config(language=_lang)
                    async for payload in deepgram_transcripts(config=dg_config, audio_source=_source()):
                        if payload.get("type") == "SpeechStarted":
                            await utterance_queue.put({"event": "vad.speech_started", "stt_language": _lang})
                        if payload.get("type") == "UtteranceEnd":
                            await utterance_queue.put({"event": "vad.utterance_end", "stt_language": _lang})
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
            if self._pending_final_task:
                self._pending_final_task.cancel()
                self._pending_final_task = None
            if self._no_engagement_task:
                self._no_engagement_task.cancel()
                self._no_engagement_task = None
            if self._silence_close_task:
                self._silence_close_task.cancel()
                self._silence_close_task = None
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

        now = time.monotonic()
        vad_items = [item for item in drained if str(item.get("event") or "").strip()]
        transcript_items = [item for item in drained if not str(item.get("event") or "").strip()]

        for item in vad_items:
            ev = str(item.get("event") or "").strip()
            if ev == "vad.speech_started":
                self._customer_speaking = True
                self._last_vad_speech_started_at = now
                self._last_customer_activity_at = now
            elif ev == "vad.utterance_end":
                self._customer_speaking = False
                self._last_vad_utterance_end_at = now

        final_items = [item for item in transcript_items if bool(item.get("is_final"))]
        interim_items = [item for item in transcript_items if not bool(item.get("is_final"))]

        if interim_items:
            # Phase 1 turn-taking: use interim STT for barge-in only. Do not respond until a
            # debounced final utterance is ready.
            best = _pick_best_utterance(interim_items)
            text = str(best.get("text") or "").strip()
            if text and _should_barge_in(text, min_chars=_barge_in_min_chars()):
                active_speech = bool(self._current_speak_task and not self._current_speak_task.done())
                barge_in_at = time.monotonic()
                if active_speech:
                    self._last_barge_in_at = barge_in_at
                    self._last_barge_in_text = _clip_text(text, 120)
                    await self._log_event(
                        "call.barge_in",
                        {
                            "text": _clip_text(text, 80),
                            "stt_language": str(best.get("stt_language") or ""),
                            "has_pending_final": bool(self._pending_final_text),
                        },
                    )
                self._last_customer_activity_at = time.monotonic()
                if self._awaiting_first_customer:
                    self._awaiting_first_customer = False
                await self._interrupt_speech(twilio_ws)
                if active_speech:
                    await self._schedule_auto_resume_after_barge_in(
                        twilio_ws,
                        barge_in_at=barge_in_at,
                        response_id=self._speaking_response_id,
                    )
                # If we already have a pending final transcript, keep pushing the debounce
                # window forward while the customer is still speaking (prevents cutoffs when
                # endpointing is aggressive).
                if self._pending_final_text:
                    self._pending_final_updated_at = time.monotonic()

        if final_items:
            self._last_customer_activity_at = time.monotonic()
            best = _pick_best_utterance(final_items)
            self._enqueue_final(best, twilio_ws)

    def _enqueue_final(self, payload: dict[str, object], twilio_ws) -> None:
        text = str(payload.get("text") or "").strip()
        if not text:
            return
        self._pending_final_text = _merge_transcript_segments(self._pending_final_text, text)
        try:
            confidence = float(payload.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        self._pending_final_confidence = max(self._pending_final_confidence, confidence)
        self._pending_final_language = str(payload.get("stt_language") or self._pending_final_language or "").strip()
        self._pending_final_updated_at = time.monotonic()

        if self._pending_final_task and not self._pending_final_task.done():
            return
        self._pending_final_task = asyncio.create_task(self._flush_pending_final(twilio_ws))

    async def _flush_pending_final(self, twilio_ws) -> None:
        """
        Debounce final transcripts to avoid responding mid-sentence when the STT
        engine emits short finals (aggressive endpointing) and continues emitting
        interims/finals as the customer keeps speaking.
        """
        stable_ms = _final_stable_ms()
        stable_s = stable_ms / 1000.0
        try:
            while True:
                await asyncio.sleep(stable_s)
                now = time.monotonic()
                if not self._pending_final_text:
                    return
                if (now - self._pending_final_updated_at) < stable_s:
                    continue
                text = self._pending_final_text.strip()
                confidence = float(self._pending_final_confidence or 0.0)
                stt_language = str(self._pending_final_language or "").strip()

                self._pending_final_text = ""
                self._pending_final_confidence = 0.0
                self._pending_final_language = ""
                self._pending_final_updated_at = 0.0

                await self._handle_transcript(
                    {"text": text, "confidence": confidence, "stt_language": stt_language, "is_final": True},
                    twilio_ws,
                    source="final",
                )
                return
        finally:
            self._pending_final_task = None

    async def _interrupt_speech(self, twilio_ws) -> None:
        if self._current_speak_task and not self._current_speak_task.done():
            self._current_speak_task.cancel()
            self._last_agent_speech_end_at = time.monotonic()
        if self._stream_sid:
            await _twilio_send(twilio_ws, {"event": "clear", "streamSid": self._stream_sid})

    async def _schedule_auto_resume_after_barge_in(self, twilio_ws, *, barge_in_at: float, response_id: int) -> None:
        if response_id <= 0:
            return
        self._auto_resume_expected_response_id = response_id
        self._auto_resume_expected_barge_in_at = float(barge_in_at or 0.0)
        if self._auto_resume_task and not self._auto_resume_task.done():
            self._auto_resume_task.cancel()
        self._auto_resume_task = asyncio.create_task(
            self._auto_resume_after_barge_in(twilio_ws, barge_in_at=barge_in_at, response_id=response_id)
        )

    async def _auto_resume_after_barge_in(self, twilio_ws, *, barge_in_at: float, response_id: int) -> None:
        """
        If barge-in was triggered by noise/echo (no real customer final follows),
        continue speaking the remaining text from the interrupted agent response.
        """
        delay = _resume_after_barge_in_seconds()
        max_wait_for_text_s = 2.0
        try:
            await asyncio.sleep(delay)
            if self._terminated:
                return
            if self._waiting_for_callback_time:
                return
            if self._last_customer_final_at > barge_in_at:
                return
            if self._customer_speaking:
                return
            if self._pending_final_text or (self._pending_final_task and not self._pending_final_task.done()):
                return

            deadline = time.monotonic() + max_wait_for_text_s
            while time.monotonic() < deadline:
                if self._terminated:
                    return
                if self._last_customer_final_at > barge_in_at:
                    return
                if self._customer_speaking:
                    return
                if self._auto_resume_response_id == response_id and self._auto_resume_text:
                    break
                await asyncio.sleep(0.1)

            if not (self._auto_resume_response_id == response_id and self._auto_resume_text):
                return
            if self._current_speak_task and not self._current_speak_task.done():
                return

            session = await self._get_session()
            lang_hint = _normalize_lang_for_prompt(session.language or "") or "en"
            await self._log_event(
                "call.resume.start",
                {
                    "response_id": response_id,
                    "remaining_chars": len(self._auto_resume_text),
                },
            )
            self._current_speak_task = asyncio.create_task(
                self._speak_auto_resume(
                    twilio_ws,
                    response_id=response_id,
                    text=self._auto_resume_text,
                    language_hint=lang_hint,
                )
            )
        except asyncio.CancelledError:
            return
        finally:
            current = asyncio.current_task()
            if self._auto_resume_task is current:
                self._auto_resume_task = None

    async def _speak_auto_resume(self, twilio_ws, *, response_id: int, text: str, language_hint: str | None) -> None:
        self._speaking_response_id = response_id
        chunks = _chunk_text_for_tts(text)
        completed = 0
        try:
            for idx, chunk in enumerate(chunks):
                await self._log_event(
                    "tts.resume.chunk",
                    {"response_id": response_id, "chunk_index": idx, "text": _clip_text(chunk, 160)},
                )
                tts_lang = _detect_text_language(chunk, fallback=language_hint)
                tts_cfg = await self._get_tts_config(language=tts_lang)
                await self._stream_tts_to_twilio(twilio_ws, chunk, config=tts_cfg)
                completed = idx + 1
            if chunks:
                spoken_text = " ".join(chunks[:completed]).strip()
                if spoken_text:
                    self._history.append(("agent", spoken_text))
                    self._history = self._history[-(self.runtime_config.max_history_turns * 2) :]
            self._last_agent_speech_end_at = time.monotonic()
            if self._auto_resume_marks_goal_delivered and not self._goal_delivered:
                self._goal_delivered = True
                session = await self._get_session()
                await self._log_event("goal.delivered", {"objective": session.objective})
            await self._log_event("call.resume.completed", {"response_id": response_id})
            self._auto_resume_text = ""
            self._auto_resume_response_id = 0
            self._auto_resume_marks_goal_delivered = False
            self._auto_resume_expected_response_id = 0
            self._auto_resume_expected_barge_in_at = 0.0
            self._interrupted_agent_remaining = ""
            self._interrupted_agent_remaining_at = 0.0
            self._interrupted_agent_response_id = 0
            self._interrupted_agent_marks_goal_delivered = False
        except asyncio.CancelledError:
            self._last_agent_speech_end_at = time.monotonic()
            spoken_text = " ".join(chunks[:completed]).strip()
            if spoken_text:
                self._history.append(("agent", spoken_text))
                self._history = self._history[-(self.runtime_config.max_history_turns * 2) :]
            remaining = " ".join(chunks[completed:]).strip()
            if remaining:
                self._auto_resume_text = remaining
                self._auto_resume_response_id = response_id
                self._interrupted_agent_remaining = remaining
                self._interrupted_agent_remaining_at = time.monotonic()
                self._interrupted_agent_response_id = response_id
            await self._log_event(
                "call.resume.interrupted",
                {"response_id": response_id, "remaining_chars": len(remaining)},
            )
            raise
        finally:
            if self._speaking_response_id == response_id:
                self._speaking_response_id = 0

    async def _finalize_interrupted_llm_response(
        self,
        *,
        response_id: int,
        llm_task: asyncio.Task,
        response_text_parts: list[str],
        spoken_chunk_count: int,
        deliver_goal: bool,
    ) -> None:
        try:
            await llm_task
        except Exception:
            return

        full_text = "".join(response_text_parts).strip()
        if not full_text:
            return

        await self._log_event("llm.response.final", {"text": full_text, "interrupted": True, "response_id": response_id})

        chunks = _chunk_text_for_tts(full_text)
        remaining = " ".join(chunks[int(spoken_chunk_count) :]).strip()
        if not remaining:
            return

        self._interrupted_agent_remaining = remaining
        self._interrupted_agent_remaining_at = time.monotonic()
        self._interrupted_agent_response_id = response_id
        self._interrupted_agent_marks_goal_delivered = deliver_goal

        if self._auto_resume_expected_response_id == response_id and self._auto_resume_expected_barge_in_at > 0:
            self._auto_resume_text = remaining
            self._auto_resume_response_id = response_id
            self._auto_resume_marks_goal_delivered = deliver_goal

        await self._log_event(
            "call.agent_speech.interrupted",
            {
                "response_id": response_id,
                "spoken_chunks": int(spoken_chunk_count),
                "total_chunks": len(chunks),
                "remaining_chars": len(remaining),
                "barge_in_text": self._last_barge_in_text,
            },
        )

    async def _maybe_greet(self, twilio_ws) -> None:
        if self._greeted or self._terminated:
            return
        session = await self._get_session()
        if not session.consent_obtained:
            return
        self._greeted = True
        self._awaiting_first_customer = True
        self._closing_waiting_for_customer = False
        self._waiting_for_callback_time = False
        self._callback_time_text = ""

        if not self._no_engagement_task or self._no_engagement_task.done():
            self._no_engagement_task = asyncio.create_task(self._watch_no_engagement(twilio_ws))
        if not self._silence_close_task or self._silence_close_task.done():
            self._silence_close_task = asyncio.create_task(self._watch_silence_close(twilio_ws))
        await self._log_event(
            "call.timer.no_engagement.started",
            {"hello_seconds": _no_engagement_hello_seconds(), "goodbye_seconds": _no_engagement_goodbye_seconds()},
        )
        await self._log_event(
            "call.timer.silence_close.started",
            {
                "close_seconds": _silence_close_seconds(),
                "hangup_after_close_seconds": _silence_hangup_after_close_seconds(),
            },
        )

        await self._interrupt_speech(twilio_ws)
        lang_hint = _normalize_lang_for_prompt((session.language or "").strip().lower())
        self._current_speak_task = asyncio.create_task(
            self._respond_and_speak(
                twilio_ws,
                customer_text="",
                customer_language=lang_hint,
                is_greeting=True,
            )
        )

    async def _watch_no_engagement(self, twilio_ws) -> None:
        """
        Human-style "no answer" behavior after greeting:
        - wait ~4s, say "Hello?"
        - wait ~4s, say goodbye + hang up
        """
        try:
            # Don't start timers until the greeting finishes speaking.
            while not self._terminated and self._awaiting_first_customer:
                speak_task = self._current_speak_task
                if speak_task and not speak_task.done():
                    await asyncio.sleep(0.1)
                    continue
                break

            if self._terminated:
                await self._log_event("call.timer.no_engagement.cancelled", {"reason": "terminated"})
                return
            if not self._awaiting_first_customer:
                await self._log_event("call.timer.no_engagement.cancelled", {"reason": "customer_engaged"})
                return

            hello_delay = _no_engagement_hello_seconds()
            goodbye_delay = _no_engagement_goodbye_seconds()
            session = await self._get_session()
            lang_hint = _normalize_lang_for_prompt(session.language or "") or "en"
            await self._log_event(
                "call.timer.no_engagement.armed",
                {"hello_seconds": hello_delay, "goodbye_seconds": goodbye_delay},
            )

            start = time.monotonic()
            while not self._terminated and self._awaiting_first_customer and (time.monotonic() - start) < hello_delay:
                await asyncio.sleep(0.1)
            if self._terminated:
                await self._log_event("call.timer.no_engagement.cancelled", {"reason": "terminated"})
                return
            if not self._awaiting_first_customer:
                await self._log_event("call.timer.no_engagement.cancelled", {"reason": "customer_engaged"})
                return

            await self._log_event("call.no_engagement.hello", {})
            await self._interrupt_speech(twilio_ws)
            hello_text = "Hello?" if lang_hint == "en" else "ألو؟"
            self._current_speak_task = asyncio.create_task(
                self._speak_text(twilio_ws, text=hello_text, language_hint=lang_hint, event_type="tts.static.hello")
            )
            try:
                await self._current_speak_task
            except asyncio.CancelledError:
                return

            if self._terminated:
                await self._log_event("call.timer.no_engagement.cancelled", {"reason": "terminated"})
                return
            if not self._awaiting_first_customer:
                await self._log_event("call.timer.no_engagement.cancelled", {"reason": "customer_engaged"})
                return
            await self._log_event("call.timer.no_engagement.goodbye_armed", {"goodbye_seconds": goodbye_delay})

            start = time.monotonic()
            while not self._terminated and self._awaiting_first_customer and (time.monotonic() - start) < goodbye_delay:
                await asyncio.sleep(0.1)
            if self._terminated:
                await self._log_event("call.timer.no_engagement.cancelled", {"reason": "terminated"})
                return
            if not self._awaiting_first_customer:
                await self._log_event("call.timer.no_engagement.cancelled", {"reason": "customer_engaged"})
                return

            await self._log_event("call.no_engagement.goodbye", {})
            await self._interrupt_speech(twilio_ws)
            goodbye_text = "Okay, I'll let you go. Goodbye." if lang_hint == "en" else "حسنًا، سأغلق الآن. مع السلامة."
            self._current_speak_task = asyncio.create_task(
                self._speak_then_hangup(
                    twilio_ws,
                    text=goodbye_text,
                    language_hint=lang_hint,
                    reason="no_engagement",
                )
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            await self._log_event("call.no_engagement.error", {"error": str(exc)})

    async def _watch_silence_close(self, twilio_ws) -> None:
        """
        Silence-triggered closing:
        - after ~6–8s of silence (both parties), ask: "Anything else before I let you go?"
        - if still no response for another window, say goodbye + hang up
        """
        poll_s = 0.25
        close_after_s = _silence_close_seconds()
        hangup_after_close_s = _silence_hangup_after_close_seconds()
        try:
            await self._log_event(
                "call.timer.silence_close.running",
                {"close_seconds": close_after_s, "hangup_after_close_seconds": hangup_after_close_s},
            )
            while not self._terminated:
                await asyncio.sleep(poll_s)
                if self._terminated:
                    await self._log_event("call.timer.silence_close.stopped", {"reason": "terminated"})
                    return
                if self._awaiting_first_customer:
                    continue
                if self._waiting_for_callback_time:
                    continue
                if self._auto_resume_task and not self._auto_resume_task.done():
                    continue
                speak_task = self._current_speak_task
                if speak_task and not speak_task.done():
                    continue

                now = time.monotonic()
                last_activity = max(self._last_customer_activity_at, self._last_agent_speech_end_at, 0.0)
                if last_activity <= 0.0:
                    continue

                if self._closing_waiting_for_customer:
                    if hangup_after_close_s > 0 and (now - max(last_activity, self._closing_question_asked_at)) >= hangup_after_close_s:
                        session = await self._get_session()
                        lang_hint = _normalize_lang_for_prompt(session.language or "") or "en"
                        await self._log_event(
                            "call.closing.silence_hangup",
                            {
                                "hangup_after_close_seconds": hangup_after_close_s,
                                "silence_seconds": now - max(last_activity, self._closing_question_asked_at),
                            },
                        )
                        await self._interrupt_speech(twilio_ws)
                        goodbye_text = "Okay, I'll let you go. Goodbye." if lang_hint == "en" else "حسنًا، سأغلق الآن. مع السلامة."
                        self._current_speak_task = asyncio.create_task(
                            self._speak_then_hangup(
                                twilio_ws,
                                text=goodbye_text,
                                language_hint=lang_hint,
                                reason="silence_after_closing",
                            )
                        )
                        return
                    continue

                if (now - last_activity) >= close_after_s:
                    session = await self._get_session()
                    lang_hint = _normalize_lang_for_prompt(session.language or "") or "en"
                    self._closing_waiting_for_customer = True
                    self._closing_question_asked_at = now
                    await self._log_event(
                        "call.closing.prompt",
                        {
                            "after_seconds": close_after_s,
                            "silence_seconds": now - last_activity,
                            "hangup_after_close_seconds": hangup_after_close_s,
                        },
                    )
                    await self._interrupt_speech(twilio_ws)
                    self._current_speak_task = asyncio.create_task(
                        self._respond_and_speak(
                            twilio_ws,
                            customer_text="",
                            customer_language=lang_hint,
                            is_closing_prompt=True,
                        )
                    )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            await self._log_event("call.silence_close.error", {"error": str(exc)})

    async def _speak_text(self, twilio_ws, *, text: str, language_hint: str | None, event_type: str) -> None:
        try:
            await self._log_event(event_type, {"text": text})
            tts_lang = _detect_text_language(text, fallback=language_hint)
            tts_cfg = await self._get_tts_config(language=tts_lang)
            await self._stream_tts_to_twilio(twilio_ws, text, config=tts_cfg)
            self._last_agent_speech_end_at = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._log_event("tts.static.error", {"error": str(exc), "event_type": event_type})

    async def _speak_then_hangup(self, twilio_ws, *, text: str, language_hint: str | None, reason: str) -> None:
        try:
            if text:
                await self._speak_text(twilio_ws, text=text, language_hint=language_hint, event_type="tts.static.hangup")
        finally:
            await self._hangup_call(reason=reason)

    async def _hangup_call(self, *, reason: str) -> None:
        if self._terminated:
            return
        self._terminated = True
        await self._log_event("call.hangup.requested", {"reason": reason})
        session = await self._get_session()
        call_sid = str(session.twilio_call_sid or "").strip()
        if not call_sid:
            await self._log_event("call.hangup.missing_call_sid", {"reason": reason})
            return
        try:
            cfg = await sync_to_async(resolve_twilio_config)(
                business_id=session.business_profile_id,
                require_from_number=False,
            )
        except Exception as exc:
            await self._log_event("call.hangup.missing_twilio_config", {"reason": reason, "error": str(exc)})
            return

        url = f"https://api.twilio.com/2010-04-01/Accounts/{cfg.account_sid}/Calls/{call_sid}.json"

        def _post() -> requests.Response:
            return requests.post(
                url,
                auth=(cfg.account_sid, cfg.auth_token),
                data={"Status": "completed"},
                timeout=20,
            )

        try:
            resp = await asyncio.to_thread(_post)
        except Exception as exc:
            await self._log_event("call.hangup.request_failed", {"reason": reason, "error": str(exc)})
            return

        if resp.status_code >= 400:
            await self._log_event(
                "call.hangup.failed",
                {"reason": reason, "status": int(resp.status_code), "body": str(resp.text or "")[:300]},
            )
            return
        await self._log_event("call.hangup.ok", {"reason": reason})

    async def _store_callback_time(self, text: str) -> None:
        value = str(text or "").strip()
        if not value:
            return

        def _update() -> None:
            session = CallSession.objects.filter(id=self.session_id).first()
            if not session:
                return
            insights = session.insights if isinstance(getattr(session, "insights", None), dict) else {}
            insights = dict(insights)
            follow_ups = insights.get("follow_ups")
            if not isinstance(follow_ups, list):
                follow_ups = []
            follow_ups = [item for item in follow_ups if not (isinstance(item, dict) and item.get("type") == "callback")]
            follow_ups.append({"type": "callback", "callback_time_text": value, "captured_during_call": True})
            insights.setdefault("schema_version", 1)
            insights["follow_ups"] = follow_ups
            session.insights = insights
            session.save(update_fields=["insights", "updated_at"])

        await sync_to_async(_update)()

    async def _respond_and_speak(
        self,
        twilio_ws,
        *,
        customer_text: str,
        customer_language: str | None = None,
        is_greeting: bool = False,
        is_closing_prompt: bool = False,
        closing_check: bool = False,
    ) -> None:
        session = await self._get_session()
        if not session.consent_obtained:
            await self._log_event("guard.no_consent", {})
            return

        provider = load_default_provider()
        if not provider:
            await self._log_event("llm.disabled", {})
            return
        self._agent_response_seq += 1
        response_id = self._agent_response_seq
        self._speaking_response_id = response_id

        system_prompt = (
            "You are a phone-call agent for a business. Be natural, concise, and helpful.\n"
            "Never hallucinate. Never invent prices, fees, policies, dates, or promises.\n"
            "Only state facts that are explicitly present in the provided context or said by the customer.\n"
            "If you don't have confirmed information, say you don't have it and offer a follow-up call.\n"
            "Language policy: respond in the customer's language (Arabic or English). If the customer code-switches, you may code-switch.\n"
            "If speaking Arabic, prefer clear Modern Standard Arabic unless the customer uses a dialect.\n"
            "Ask short clarifying questions when needed.\n"
            "Return JSON with keys: response_text (string), actions (array), extractions (empty array).\n"
            "Allowed actions: hangup (payload may be empty). Use hangup only if you are ending the call.\n"
        )
        history_lines = "\n".join(f"{role}: {text}" for role, text in self._history[-12:])
        customer_language_hint = (customer_language or session.language or "").strip().lower()
        context_block = await self._build_context_block()

        recipient_name = _extract_recipient_name(session.context_items)
        # Humanized flow: greet first, then deliver the objective on the customer's first reply.
        deliver_goal = bool(not is_closing_prompt and not is_greeting and (not self._goal_delivered and not closing_check))

        if is_closing_prompt:
            user_prompt = (
                f"Call objective: {session.objective}\n"
                f"Workspace default language: {session.language}\n"
                f"Customer language hint: {customer_language_hint}\n"
                f"Country: {session.country}\n\n"
                f"{context_block}\n\n"
                "Conversation so far:\n"
                f"{history_lines}\n\n"
                "The customer has been silent for a while.\n"
                "Ask a short closing question: 'Anything else before I let you go?'\n"
                "Do NOT restate the objective and do NOT hang up yet.\n"
            )
        elif is_greeting:
            name_note = f"Recipient name (optional to mention): {recipient_name}\n" if recipient_name else ""
            user_prompt = (
                f"Call objective: {session.objective}\n"
                f"Workspace default language: {session.language}\n"
                f"Customer language hint: {customer_language_hint}\n"
                f"Country: {session.country}\n"
                f"{name_note}\n"
                f"{context_block}\n\n"
                "No customer speech yet.\n"
                "Greet briefly and naturally. You may mention the recipient name if available, but do not ask for confirmation.\n"
                "Do NOT deliver the objective yet — wait for the customer's first reply before explaining the reason for the call.\n"
                "Ask a short, natural opener to elicit a first response (avoid sounding like an IVR).\n"
            )
        else:
            closing_instructions = ""
            if closing_check:
                closing_instructions = (
                    "You previously asked: 'Anything else before I let you go?'\n"
                    "If the customer indicates 'no' or that they are done, say goodbye and include a hangup action.\n"
                    "If the customer has another request/question, continue naturally and do NOT hang up.\n\n"
                )
            interrupted_note = ""
            if not closing_check and self._interrupted_agent_remaining and self._interrupted_agent_remaining_at:
                if (time.monotonic() - self._interrupted_agent_remaining_at) <= 120.0:
                    clip = _clip_text(self._interrupted_agent_remaining, 600)
                    if clip:
                        interrupted_note = (
                            "You were interrupted earlier and may not have finished saying this (not yet delivered):\n"
                            f"{clip}\n\n"
                            "First respond to the customer's latest message. Then, if it feels natural to continue the interrupted "
                            "information, continue briefly. If it does NOT feel appropriate, do not continue it.\n\n"
                        )
            goal_instruction = ""
            if deliver_goal:
                goal_instruction = (
                    "The objective has not been delivered yet. Deliver it once, briefly, as part of your response.\n\n"
                )

            user_prompt = (
                f"Call objective: {session.objective}\n"
                f"Workspace default language: {session.language}\n"
                f"Customer language hint: {customer_language_hint}\n"
                f"Country: {session.country}\n\n"
                f"{context_block}\n\n"
                "Conversation so far:\n"
                f"{history_lines}\n\n"
                f"Customer just said: {customer_text}\n\n"
                f"{closing_instructions}"
                f"{interrupted_note}"
                f"{goal_instruction}"
                "Rules:\n"
                "- Be natural, concise, and helpful. Avoid sounding like an IVR.\n"
                "- Do NOT ask the customer to confirm they've received information unless they asked you to repeat/clarify.\n"
                "- If the customer asks something not supported by the provided context, say you will follow up.\n"
            )
        bundle = PromptBundle(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            transcript=[],
            knowledge_snippets=[],
            actions_catalog=[],
            agent_traits={},
        )

        await self._log_event(
            "call.agent_speech.started",
            {
                "response_id": response_id,
                "is_greeting": bool(is_greeting),
                "is_closing_prompt": bool(is_closing_prompt),
                "closing_check": bool(closing_check),
                "deliver_goal": bool(deliver_goal),
            },
        )

        loop = asyncio.get_running_loop()
        delta_queue: asyncio.Queue[str] = asyncio.Queue()
        response_text_parts: list[str] = []
        llm_result: dict | None = None
        streaming_to_tts = True

        def _emit_text(delta: str) -> None:
            nonlocal streaming_to_tts
            if not delta:
                return
            response_text_parts.append(delta)
            if streaming_to_tts:
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
            tts_config_en = await self._get_tts_config(language="en")
            tts_config_ar = await self._get_tts_config(language="ar")
        except Exception as exc:
            await self._log_event("tts.disabled", {"error": str(exc)})
            await llm_task
            if self._speaking_response_id == response_id:
                self._speaking_response_id = 0
            return

        buffer = ""
        spoken_chunks: list[str] = []
        spoke_any = False
        try:
            while True:
                delta = await delta_queue.get()
                if delta == "":
                    break
                buffer += delta
                chunk, buffer = _maybe_extract_speakable_chunk(buffer)
                if chunk:
                    await self._log_event("tts.chunk", {"response_id": response_id, "text": chunk})
                    tts_lang = _detect_text_language(chunk, fallback=customer_language_hint)
                    tts_cfg = tts_config_ar if tts_lang == "ar" else tts_config_en
                    await self._stream_tts_to_twilio(twilio_ws, chunk, config=tts_cfg)
                    spoken_chunks.append(chunk)
                    await self._log_event("tts.chunk.done", {"response_id": response_id, "text": _clip_text(chunk, 160)})
                    spoke_any = True

            final = buffer.strip()
            if final:
                await self._log_event("tts.chunk.final", {"response_id": response_id, "text": final})
                tts_lang = _detect_text_language(final, fallback=customer_language_hint)
                tts_cfg = tts_config_ar if tts_lang == "ar" else tts_config_en
                await self._stream_tts_to_twilio(twilio_ws, final, config=tts_cfg)
                spoken_chunks.append(final)
                spoke_any = True
            if spoke_any:
                self._last_agent_speech_end_at = time.monotonic()
            if deliver_goal and spoke_any and not self._goal_delivered:
                self._goal_delivered = True
                await self._log_event("goal.delivered", {"objective": session.objective})
        except asyncio.CancelledError:
            streaming_to_tts = False
            self._last_agent_speech_end_at = time.monotonic()
            spoken_text = " ".join(spoken_chunks).strip()
            if spoken_text:
                self._history.append(("agent", spoken_text))
                self._history = self._history[-(self.runtime_config.max_history_turns * 2) :]
            await self._log_event(
                "call.agent_speech.cancelled",
                {
                    "response_id": response_id,
                    "spoken_chunks": len(spoken_chunks),
                    "barge_in_text": self._last_barge_in_text,
                },
            )
            asyncio.create_task(
                self._finalize_interrupted_llm_response(
                    response_id=response_id,
                    llm_task=llm_task,
                    response_text_parts=response_text_parts,
                    spoken_chunk_count=len(spoken_chunks),
                    deliver_goal=deliver_goal,
                )
            )
            if self._speaking_response_id == response_id:
                self._speaking_response_id = 0
            return
        except Exception as exc:
            await self._log_event("tts.error", {"error": str(exc)})
        finally:
            if self._speaking_response_id == response_id:
                self._speaking_response_id = 0

        await llm_task
        full_text = ""
        if isinstance(llm_result, dict):
            full_text = str(llm_result.get("response_text") or "").strip()
            usage = llm_result.get("llm_usage")
            if full_text:
                await self._log_event("llm.response.final", {"text": full_text, "llm_usage": usage or {}, "response_id": response_id})
            if not full_text:
                full_text = "".join(response_text_parts).strip()
            spoken_text = " ".join(spoken_chunks).strip() if spoken_chunks else ""
            if spoken_text:
                self._history.append(("agent", spoken_text))
                self._history = self._history[-(self.runtime_config.max_history_turns * 2) :]
            elif full_text:
                self._history.append(("agent", full_text))
                self._history = self._history[-(self.runtime_config.max_history_turns * 2) :]
            actions = llm_result.get("actions")
            if _has_hangup_action(actions):
                await self._log_event("call.hangup.action", {"actions": actions})
                await self._hangup_call(reason="llm_action")

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

    async def _get_deepgram_config(self, *, language: str) -> DeepgramConfig:
        lang = (language or "en").strip().lower() or "en"
        cached = self._stt_config_cache.get(lang)
        if cached is not None:
            return cached
        session = await self._get_session()
        config = await sync_to_async(resolve_deepgram_config)(
            business_id=session.business_profile_id,
            language=lang,
        )
        self._stt_config_cache[lang] = config
        return config

    async def _get_tts_config(self, *, language: str | None) -> ElevenLabsConfig:
        lang = (language or "en").strip().lower() or "en"
        cached = self._tts_config_cache.get(lang)
        if cached is not None:
            return cached
        session = await self._get_session()
        config = await sync_to_async(resolve_elevenlabs_config)(
            business_id=session.business_profile_id,
            language=lang,
        )
        self._tts_config_cache[lang] = config
        return config

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

        # Phase 4: Prefer publishing transcript updates to the portal session Redis event bus.
        # Keep cache fallback for the legacy DB-polling session stream.
        try:
            from apps.conversations.portal_session_event_bus import publish_portal_conversation_event

            publish_portal_conversation_event(
                conversation_id=conversation_id,
                event_name="voiceCallTranscript",
                payload=event_data,
            )
        except Exception:
            pass

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
        if self._terminated:
            return
        text = str(payload.get("text") or "").strip()
        if not text:
            return
        if _should_skip_duplicate(text, last_text=self._last_handled_text, last_at=self._last_handled_at):
            return
        confidence = float(payload.get("confidence") or 0.0)
        stt_language = str(payload.get("stt_language") or "").strip()
        event_type = "stt.final" if source == "final" else "stt.interim"
        await self._log_event(event_type, {"text": text, "confidence": confidence, "stt_language": stt_language})

        now = time.monotonic()
        self._last_customer_activity_at = now
        self._last_customer_final_at = now
        self._customer_speaking = False
        if self._auto_resume_task and not self._auto_resume_task.done():
            self._auto_resume_task.cancel()
        self._auto_resume_text = ""
        self._auto_resume_response_id = 0
        self._auto_resume_marks_goal_delivered = False
        self._auto_resume_expected_response_id = 0
        self._auto_resume_expected_barge_in_at = 0.0

        session = await self._get_session()
        language_hint = _normalize_lang_for_prompt(stt_language or session.language or "") or "en"
        recipient_name = _extract_recipient_name(session.context_items)

        # Any customer turn cancels a pending close-wait state; we'll re-enter later if silence persists.
        closing_check = False
        if self._closing_waiting_for_customer:
            closing_check = True
            self._closing_waiting_for_customer = False
            self._closing_question_asked_at = 0.0

        if self._awaiting_first_customer:
            self._awaiting_first_customer = False
            await self._log_event("call.customer.engaged", {"text": text})

        if self._waiting_for_callback_time:
            self._waiting_for_callback_time = False
            self._callback_time_text = text
            await self._store_callback_time(text)
            await self._log_event("call.callback_time.captured", {"text": text})
            await self._interrupt_speech(twilio_ws)
            confirm_text = "Thanks — when it’s convenient, I’ll call you back. Goodbye." if language_hint == "en" else "شكرًا، سأتصل بك في الوقت المناسب. مع السلامة."
            self._current_speak_task = asyncio.create_task(
                self._speak_then_hangup(
                    twilio_ws,
                    text=confirm_text,
                    language_hint=language_hint,
                    reason="callback_time_captured",
                )
            )
            return

        if _is_wrong_person_strong(text, language_hint=stt_language or session.language or "", recipient_name=recipient_name):
            await self._log_event("call.wrong_person", {"text": text, "recipient_name": recipient_name})
            await self._interrupt_speech(twilio_ws)
            apology = "Sorry about that — I’ll update our records. Goodbye." if language_hint == "en" else "أعتذر عن الإزعاج—سأقوم بتحديث بياناتنا. مع السلامة."
            self._current_speak_task = asyncio.create_task(
                self._speak_then_hangup(
                    twilio_ws,
                    text=apology,
                    language_hint=language_hint,
                    reason="wrong_person",
                )
            )
            return

        if _is_busy_signal(text, stt_language or session.language or ""):
            await self._log_event("call.busy", {"text": text})
            self._waiting_for_callback_time = True
            await self._interrupt_speech(twilio_ws)
            prompt_text = "No problem — when should I call you back?" if language_hint == "en" else "تمام، متى تحب أن أتصل بك مرة أخرى؟"
            self._current_speak_task = asyncio.create_task(
                self._speak_text(
                    twilio_ws,
                    text=prompt_text,
                    language_hint=language_hint,
                    event_type="call.busy.ask_callback",
                )
            )
            return

        self._history.append(("customer", text))
        self._history = self._history[-(self.runtime_config.max_history_turns * 2) :]
        self._last_handled_text = text
        self._last_handled_at = time.monotonic()

        await self._interrupt_speech(twilio_ws)
        self._current_speak_task = asyncio.create_task(
            self._respond_and_speak(
                twilio_ws,
                customer_text=text,
                customer_language=_normalize_lang_for_prompt(stt_language),
                closing_check=closing_check,
            )
        )

    # Note: Phase 1 disables responding to interim transcripts. We keep the
    # previous interim debounce implementation around for future experimentation.


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


def _final_stable_ms() -> int:
    raw = (os.getenv("VOICE_STT_FINAL_STABLE_MS") or "700").strip()
    try:
        value = int(raw)
    except Exception:
        value = 700
    return max(200, min(2000, value))


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
