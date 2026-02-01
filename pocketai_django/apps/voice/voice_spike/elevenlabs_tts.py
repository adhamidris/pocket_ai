from __future__ import annotations

import os
from dataclasses import dataclass
from typing import AsyncIterator

import httpx


@dataclass(frozen=True)
class ElevenLabsConfig:
    api_key: str
    voice_id: str
    model_id: str
    output_format: str

    @staticmethod
    def from_env(*, language: str | None = None) -> "ElevenLabsConfig":
        api_key = (os.getenv("ELEVENLABS_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is not configured.")

        forced_voice_id = (os.getenv("ELEVENLABS_VOICE_ID") or "").strip()
        if forced_voice_id:
            voice_id = forced_voice_id
        else:
            language_norm = (language or "").strip().lower()
            if language_norm == "ar":
                voice_id = (
                    (os.getenv("ELEVENLABS_DEFAULT_VOICE_AR") or "").strip()
                    or (os.getenv("ELEVENLABS_DEFAULT_VOICE_EN") or "").strip()
                )
            else:
                voice_id = (os.getenv("ELEVENLABS_DEFAULT_VOICE_EN") or "").strip()

        if not voice_id:
            raise RuntimeError(
                "ELEVENLABS_VOICE_ID is not configured (or ELEVENLABS_DEFAULT_VOICE_EN/ELEVENLABS_DEFAULT_VOICE_AR)."
            )

        model_id = (os.getenv("ELEVENLABS_MODEL_ID") or "").strip()
        if language and not model_id:
            model_id = (os.getenv(f"ELEVENLABS_MODEL_ID_{language.strip().upper()}") or "").strip()
        if not model_id:
            model_id = "eleven_flash_v2_5"

        output_format = (os.getenv("ELEVENLABS_OUTPUT_FORMAT") or "ulaw_8000").strip()
        return ElevenLabsConfig(api_key=api_key, voice_id=voice_id, model_id=model_id, output_format=output_format)


async def stream_tts_audio(text: str, *, config: ElevenLabsConfig) -> AsyncIterator[bytes]:
    """
    Stream TTS audio bytes from ElevenLabs.

    Notes:
    - Output format must be telephony-friendly (`ulaw_8000`) for Twilio Media Streams.
    - This uses the HTTP streaming endpoint to keep implementation simple for Phase 0.
    """

    if not text.strip():
        return

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{config.voice_id}/stream"
    headers = {
        "xi-api-key": config.api_key,
        "accept": f"audio/{config.output_format}",
        "content-type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": config.model_id,
        # Voice settings can be overridden later; keep defaults for spike.
    }

    timeout = httpx.Timeout(connect=10.0, read=60.0, write=20.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            url,
            headers=headers,
            params={"output_format": config.output_format},
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                if chunk:
                    yield chunk
