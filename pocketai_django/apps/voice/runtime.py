from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator, Callable

from asgiref.sync import sync_to_async

from apps.llm.ai_prompt_builder import PromptBundle
from apps.llm.llm_provider import DeepSeekChatProvider, OpenAIChatProvider, _ResponseTextExtractor, load_default_provider
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

    async def run_twilio_stream(self, twilio_ws) -> None:
        session = await self._get_session()
        audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        utterance_queue: asyncio.Queue[str] = asyncio.Queue()
        stop_event = asyncio.Event()

        async def audio_source() -> AsyncIterator[bytes]:
            while not stop_event.is_set():
                chunk = await audio_queue.get()
                if chunk is None:  # type: ignore[comparison-overlap]
                    break
                yield chunk

        async def stt_loop() -> None:
            try:
                dg_config = DeepgramConfig.from_env(language=session.language or "en")
                async for payload in deepgram_transcripts(config=dg_config, audio_source=audio_source()):
                    text = _extract_final_transcript(payload)
                    if text:
                        await utterance_queue.put(text)
            except Exception as exc:
                logger.exception("Deepgram STT loop crashed: %s", exc)

        stt_task = asyncio.create_task(stt_loop())
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

                    try:
                        audio_queue.put_nowait(audio)
                    except asyncio.QueueFull:
                        pass

                    await self._drain_utterances(utterance_queue, twilio_ws)
                    continue
        finally:
            stop_event.set()
            try:
                audio_queue.put_nowait(b"")
            except Exception:
                pass
            if self._current_speak_task:
                self._current_speak_task.cancel()
            stt_task.cancel()

    async def _drain_utterances(self, utterance_queue: asyncio.Queue[str], twilio_ws) -> None:
        drained: list[str] = []
        while True:
            try:
                drained.append(utterance_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not drained:
            return
        text = drained[-1].strip()
        if not text:
            return

        await self._log_event("stt.final", {"text": text})
        self._history.append(("customer", text))
        self._history = self._history[-(self.runtime_config.max_history_turns * 2) :]

        await self._interrupt_speech(twilio_ws)
        self._current_speak_task = asyncio.create_task(self._respond_and_speak(twilio_ws, customer_text=text))

    async def _interrupt_speech(self, twilio_ws) -> None:
        if self._current_speak_task and not self._current_speak_task.done():
            self._current_speak_task.cancel()
        if self._stream_sid:
            await _twilio_send(twilio_ws, {"event": "clear", "streamSid": self._stream_sid})

    async def _respond_and_speak(self, twilio_ws, *, customer_text: str) -> None:
        session = await self._get_session()
        if not session.consent_obtained:
            await self._log_event("guard.no_consent", {})
            return

        provider = load_default_provider()
        if not provider:
            await self._log_event("llm.disabled", {})
            return

        system_prompt = (
            "You are a phone-call agent. Be natural, concise, and helpful.\n"
            "Do not hallucinate. If you don't know something, say you'll check and follow up.\n"
            "Ask short clarifying questions when needed.\n"
            "Return JSON with keys: response_text (string), actions (empty array), extractions (empty array).\n"
        )
        history_lines = "\n".join(f"{role}: {text}" for role, text in self._history[-12:])
        user_prompt = (
            f"Call objective: {session.objective}\n"
            f"Language: {session.language}\n\n"
            "Conversation so far:\n"
            f"{history_lines}\n\n"
            f"Customer just said: {customer_text}\n"
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
            tts_config = ElevenLabsConfig.from_env()
        except Exception as exc:
            await self._log_event("tts.disabled", {"error": str(exc)})
            await llm_task
            return

        buffer = ""
        try:
            while True:
                delta = await delta_queue.get()
                if delta == "":
                    break
                buffer += delta
                chunk, buffer = _maybe_extract_speakable_chunk(buffer)
                if chunk:
                    await self._log_event("tts.chunk", {"text": chunk})
                    await self._stream_tts_to_twilio(twilio_ws, chunk, config=tts_config)

            final = buffer.strip()
            if final:
                await self._log_event("tts.chunk.final", {"text": final})
                await self._stream_tts_to_twilio(twilio_ws, final, config=tts_config)
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
        async for audio in stream_tts_audio(text, config=config):
            payload = base64.b64encode(audio).decode("ascii")
            await _twilio_send(twilio_ws, {"event": "media", "streamSid": self._stream_sid, "media": {"payload": payload}})

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


def _maybe_extract_speakable_chunk(buffer: str) -> tuple[str, str]:
    text = buffer
    for punct in (". ", "? ", "! ", "\n"):
        idx = text.find(punct)
        if idx != -1 and idx >= 40:
            cut = idx + len(punct)
            chunk = text[:cut].strip()
            rest = text[cut:].lstrip()
            return chunk, rest

    if len(text) >= 140:
        last_space = text.rfind(" ", 0, 180)
        if last_space > 60:
            chunk = text[: last_space + 1].strip()
            rest = text[last_space + 1 :].lstrip()
            return chunk, rest
    return "", buffer
