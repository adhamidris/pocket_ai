from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import AsyncIterator, Mapping

import httpx
import websockets

logger = logging.getLogger(__name__)

# Module-level client for connection reuse
_shared_client: httpx.AsyncClient | None = None


def _get_shared_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        timeout = httpx.Timeout(connect=5.0, read=60.0, write=20.0, pool=5.0)
        _shared_client = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
    return _shared_client


@dataclass(frozen=True)
class DeepgramTTSConfig:
    api_key: str
    model: str
    encoding: str
    sample_rate: int

    @staticmethod
    def from_env() -> "DeepgramTTSConfig":
        return DeepgramTTSConfig.from_credentials(credentials={}, allow_env_fallback=True)

    @staticmethod
    def from_credentials(
        *,
        credentials: Mapping[str, object] | None = None,
        allow_env_fallback: bool = True,
    ) -> "DeepgramTTSConfig":
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

        model = _value(key="model", env_key="DEEPGRAM_TTS_MODEL", default="aura-2-thalia-en")
        encoding = _value(key="encoding", env_key="DEEPGRAM_TTS_ENCODING", default="mulaw")
        sample_rate_raw = _value(key="sample_rate", env_key="DEEPGRAM_TTS_SAMPLE_RATE", default="8000")
        try:
            sample_rate = int(sample_rate_raw)
        except Exception:
            sample_rate = 8000

        return DeepgramTTSConfig(
            api_key=api_key,
            model=model,
            encoding=encoding,
            sample_rate=sample_rate,
        )


async def stream_tts_audio(text: str, *, config: DeepgramTTSConfig) -> AsyncIterator[bytes]:
    """
    Stream TTS audio bytes from Deepgram via HTTP POST.

    Returns raw mulaw audio bytes (no base64, no JSON wrapper).
    """
    if not text.strip():
        return

    url = "https://api.deepgram.com/v1/speak"
    headers = {
        "Authorization": f"Token {config.api_key}",
        "Content-Type": "application/json",
    }
    params = {
        "model": config.model,
        "encoding": config.encoding,
        "sample_rate": str(config.sample_rate),
        "container": "none",
    }
    payload = {"text": text}

    client = _get_shared_client()
    async with client.stream(
        "POST",
        url,
        headers=headers,
        params=params,
        json=payload,
    ) as resp:
        resp.raise_for_status()
        async for chunk in resp.aiter_bytes():
            if chunk:
                yield chunk


class DeepgramTTSWSSession:
    """WebSocket TTS session for continuous streaming via Deepgram Aura."""

    def __init__(self, config: DeepgramTTSConfig) -> None:
        self._config = config
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._receive_task: asyncio.Task | None = None
        self._closed = False

    async def connect(self) -> None:
        cfg = self._config
        url = (
            f"wss://api.deepgram.com/v1/speak"
            f"?model={cfg.model}&encoding={cfg.encoding}&sample_rate={cfg.sample_rate}"
        )
        headers = {"Authorization": f"Token {cfg.api_key}"}
        try:
            connector = websockets.connect(url, extra_headers=headers, ping_interval=20, ping_timeout=20)
        except TypeError:
            connector = websockets.connect(url, additional_headers=headers, ping_interval=20, ping_timeout=20)  # type: ignore[arg-type]

        self._ws = await connector
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def _receive_loop(self) -> None:
        """Read raw binary audio frames from the WebSocket."""
        try:
            async for message in self._ws:  # type: ignore[union-attr]
                if not message:
                    continue
                # Deepgram WS returns raw binary audio frames (not base64 JSON)
                if isinstance(message, bytes):
                    if message:
                        await self._audio_queue.put(message)
                elif isinstance(message, str):
                    # JSON control messages (metadata, flushed, etc.) — ignore
                    try:
                        data = json.loads(message)
                        if data.get("type") == "Close":
                            break
                    except Exception:
                        pass
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            logger.debug("Deepgram TTS WS receive error: %s", exc)
        finally:
            await self._audio_queue.put(None)

    async def send_text(self, text: str) -> None:
        if self._ws is None or self._closed:
            return
        msg = {"type": "Speak", "text": text}
        await self._ws.send(json.dumps(msg))

    async def flush(self) -> None:
        if self._ws is None or self._closed:
            return
        await self._ws.send(json.dumps({"type": "Flush"}))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"type": "Close"}))
            except Exception:
                pass
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()

    async def audio_stream(self) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._audio_queue.get()
            if chunk is None:
                break
            yield chunk
