from __future__ import annotations

import asyncio
import base64
import os

from apps.voice.providers.audio_frames import iter_audio_frames
from apps.voice.providers.deepgram_tts import DeepgramTTSConfig, DeepgramTTSWSSession
from apps.voice.providers.deepgram_tts import stream_tts_audio as deepgram_stream_tts_audio
from apps.voice.providers.elevenlabs_tts import ElevenLabsConfig, ElevenLabsWSSession, stream_tts_audio
from apps.voice.runtime.helpers import _stream_target_fields, _twilio_send


class VoiceRuntimeTTSMixin:

    async def _stream_tts_to_twilio(
        self,
        twilio_ws,
        text: str,
        *,
        config: ElevenLabsConfig | None = None,
        deepgram_config: DeepgramTTSConfig | None = None,
    ) -> None:
        if not self._stream_sid:
            return
        frame_ms = int((os.getenv("VOICE_TTS_FRAME_MS") or "20").strip() or 20)
        pace_raw = (os.getenv("VOICE_TTS_PACE") or "").strip().lower()
        pace = False if pace_raw in {"0", "false", "no"} else True

        if deepgram_config is not None:
            audio_iter = deepgram_stream_tts_audio(text, config=deepgram_config)
            output_format = f"mulaw_{deepgram_config.sample_rate}"
        elif config is not None:
            audio_iter = stream_tts_audio(text, config=config)
            output_format = config.output_format
        else:
            return

        async for frame in iter_audio_frames(
            audio_iter,
            output_format=output_format,
            frame_ms=frame_ms,
        ):
            payload = base64.b64encode(frame).decode("ascii")
            message = {"event": "media", "media": {"payload": payload}}
            message.update(_stream_target_fields(self._stream_sid))
            await _twilio_send(twilio_ws, message)
            if pace:
                await asyncio.sleep(frame_ms / 1000.0)

    async def _stream_ws_audio_to_twilio(
        self,
        twilio_ws,
        ws_session: ElevenLabsWSSession | DeepgramTTSWSSession,
        *,
        output_format: str,
    ) -> None:
        """Stream audio from a WS TTS session to Twilio continuously."""
        if not self._stream_sid:
            return
        frame_ms = int((os.getenv("VOICE_TTS_FRAME_MS") or "20").strip() or 20)
        pace_raw = (os.getenv("VOICE_TTS_PACE") or "").strip().lower()
        pace = False if pace_raw in {"0", "false", "no"} else True

        async for frame in iter_audio_frames(
            ws_session.audio_stream(),
            output_format=output_format,
            frame_ms=frame_ms,
        ):
            payload = base64.b64encode(frame).decode("ascii")
            message = {"event": "media", "media": {"payload": payload}}
            message.update(_stream_target_fields(self._stream_sid))
            await _twilio_send(twilio_ws, message)
            if pace:
                await asyncio.sleep(frame_ms / 1000.0)
