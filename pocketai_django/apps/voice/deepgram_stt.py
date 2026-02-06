from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import AsyncIterator, Mapping

import websockets


@dataclass(frozen=True)
class DeepgramConfig:
    api_key: str
    model: str
    language: str
    endpointing_ms: int = 300

    @staticmethod
    def from_env(*, language: str) -> "DeepgramConfig":
        return DeepgramConfig.from_credentials(
            credentials={},
            language=language,
            allow_env_fallback=True,
        )

    @staticmethod
    def from_credentials(
        *,
        credentials: Mapping[str, object] | None,
        language: str,
        allow_env_fallback: bool,
    ) -> "DeepgramConfig":
        source = dict(credentials or {})

        def _value(*, key: str, env_key: str, default: str = "") -> str:
            if key in source:
                text = str(source.get(key) or "").strip()
                if text or not allow_env_fallback:
                    return text
            if allow_env_fallback:
                text = (os.getenv(env_key) or "").strip()
                if text:
                    return text
            return default

        api_key = _value(key="api_key", env_key="DEEPGRAM_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPGRAM_API_KEY is not configured.")

        model = _value(key="model", env_key="DEEPGRAM_MODEL", default="nova-2") or "nova-2"
        endpointing_ms_raw = _value(
            key="endpointing_ms",
            env_key="DEEPGRAM_ENDPOINTING_MS",
            default="300",
        )
        try:
            endpointing_ms = int(endpointing_ms_raw)
        except Exception:
            endpointing_ms = 300
        endpointing_ms = max(0, min(2000, endpointing_ms))
        return DeepgramConfig(api_key=api_key, model=model, language=language, endpointing_ms=endpointing_ms)


def _deepgram_ws_url(*, config: DeepgramConfig) -> str:
    # Twilio Media Streams sends audio/x-mulaw @ 8kHz (raw bytes).
    params = {
        "encoding": "mulaw",
        "sample_rate": "8000",
        "channels": "1",
        "model": config.model,
        "language": config.language,
        "interim_results": "true",
        "punctuate": "true",
        "endpointing": str(int(config.endpointing_ms)),
        "vad_events": "true",
        "smart_format": "true",
        "numerals": "true",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"wss://api.deepgram.com/v1/listen?{query}"


async def deepgram_transcripts(
    *,
    config: DeepgramConfig,
    audio_source: AsyncIterator[bytes],
) -> AsyncIterator[dict]:
    """
    Connect to Deepgram and yield raw event dicts.

    Caller is responsible for extracting final transcripts.
    """

    url = _deepgram_ws_url(config=config)
    headers = {"Authorization": f"Token {config.api_key}"}

    connect_kwargs = {"ping_interval": 20, "ping_timeout": 20}
    try:
        connector = websockets.connect(url, extra_headers=headers, **connect_kwargs)
    except TypeError:  # pragma: no cover - websockets API compat
        connector = websockets.connect(url, additional_headers=headers, **connect_kwargs)  # type: ignore[arg-type]

    async with connector as ws:
        send_task = None

        async def _send_audio() -> None:
            async for chunk in audio_source:
                if not chunk:
                    continue
                await ws.send(chunk)

        send_task = await _schedule(_send_audio())
        try:
            async for message in ws:
                if not message:
                    continue
                try:
                    payload = json.loads(message)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    yield payload
        finally:
            if send_task:
                send_task.cancel()


async def _schedule(coro):
    import asyncio

    return asyncio.create_task(coro)
