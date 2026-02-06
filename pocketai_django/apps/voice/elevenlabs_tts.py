from __future__ import annotations

import os
from dataclasses import dataclass
from typing import AsyncIterator, Mapping

import httpx

# Module-level client for connection reuse (significant latency reduction)
_shared_client: httpx.AsyncClient | None = None


def _get_shared_client() -> httpx.AsyncClient:
    """Get or create a shared httpx client for connection reuse."""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        timeout = httpx.Timeout(connect=5.0, read=60.0, write=20.0, pool=5.0)
        _shared_client = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
    return _shared_client


@dataclass(frozen=True)
class ElevenLabsConfig:
    api_key: str
    voice_id: str
    model_id: str
    output_format: str

    @staticmethod
    def from_env(*, language: str | None = None) -> "ElevenLabsConfig":
        return ElevenLabsConfig.from_credentials(
            credentials={},
            language=language,
            allow_env_fallback=True,
        )

    @staticmethod
    def from_credentials(
        *,
        credentials: Mapping[str, object] | None,
        language: str | None = None,
        allow_env_fallback: bool,
    ) -> "ElevenLabsConfig":
        source = dict(credentials or {})

        def _value(*, key: str, env_key: str, default: str = "", fallback_keys: tuple[str, ...] = ()) -> str:
            for candidate in (key, *fallback_keys):
                if candidate in source:
                    text = str(source.get(candidate) or "").strip()
                    if text or not allow_env_fallback:
                        return text
            if allow_env_fallback:
                text = (os.getenv(env_key) or "").strip()
                if text:
                    return text
            return default

        api_key = _value(key="api_key", env_key="ELEVENLABS_API_KEY")
        if not api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is not configured.")

        forced_voice_id = _value(
            key="voice_id",
            env_key="ELEVENLABS_VOICE_ID",
        )
        if forced_voice_id:
            voice_id = forced_voice_id
        else:
            language_norm = (language or "").strip().lower()
            if language_norm == "ar":
                voice_id = _value(
                    key="default_voice_ar",
                    env_key="ELEVENLABS_DEFAULT_VOICE_AR",
                    fallback_keys=("voice_id_ar",),
                ) or _value(
                    key="default_voice_en",
                    env_key="ELEVENLABS_DEFAULT_VOICE_EN",
                    fallback_keys=("voice_id_en",),
                )
            else:
                voice_id = _value(
                    key="default_voice_en",
                    env_key="ELEVENLABS_DEFAULT_VOICE_EN",
                    fallback_keys=("voice_id_en",),
                )

        if not voice_id:
            raise RuntimeError(
                "ELEVENLABS_VOICE_ID is not configured (or ELEVENLABS_DEFAULT_VOICE_EN/ELEVENLABS_DEFAULT_VOICE_AR)."
            )

        model_id = _value(
            key="model_id",
            env_key="ELEVENLABS_MODEL_ID",
        )
        if language and not model_id:
            lang = language.strip().upper()
            model_id = _value(
                key=f"model_id_{language.strip().lower()}",
                env_key=f"ELEVENLABS_MODEL_ID_{lang}",
            )
        if not model_id:
            model_id = "eleven_flash_v2_5"

        output_format = _value(
            key="output_format",
            env_key="ELEVENLABS_OUTPUT_FORMAT",
            default="ulaw_8000",
        )
        return ElevenLabsConfig(
            api_key=api_key,
            voice_id=voice_id,
            model_id=model_id,
            output_format=output_format,
        )


async def stream_tts_audio(text: str, *, config: ElevenLabsConfig) -> AsyncIterator[bytes]:
    """
    Stream TTS audio bytes from ElevenLabs.

    Notes:
    - Output format must be telephony-friendly (`ulaw_8000`) for Twilio Media Streams.
    - Uses shared HTTP client for connection reuse (reduces latency by ~100-200ms).
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

    client = _get_shared_client()
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
