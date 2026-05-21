from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import AsyncIterator, Callable

from apps.voice.providers.deepgram_stt import deepgram_transcripts
from apps.voice.runtime.helpers import _extract_transcript_with_confidence, _stt_language_tags_for_session

logger = logging.getLogger(__name__)


class VoiceRuntimeStreamMixin:

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
                    self._stream_sid = str(start.get("streamSid") or start.get("stream_id") or "")
                    call_sid = str(
                        start.get("callSid")
                        or start.get("call_session_id")
                        or start.get("call_control_id")
                        or ""
                    )
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
