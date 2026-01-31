from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import AsyncIterator

import websockets


@dataclass(frozen=True)
class DeepgramConfig:
    api_key: str
    model: str
    language: str

    @staticmethod
    def from_env(*, language: str) -> "DeepgramConfig":
        api_key = (os.getenv("DEEPGRAM_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("DEEPGRAM_API_KEY is not configured.")
        model = (os.getenv("DEEPGRAM_MODEL") or "nova-2").strip()
        return DeepgramConfig(api_key=api_key, model=model, language=language)


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
        "endpointing": "300",
        "vad_events": "true",
        "smart_format": "true",
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
