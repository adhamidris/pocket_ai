from __future__ import annotations

import asyncio
import logging
import re

from django.core.management.base import BaseCommand

import websockets

from apps.voice.voice_spike.runtime import VoiceSpikeRuntime


logger = logging.getLogger(__name__)

PATH_RE = re.compile(r"^/voice/spike/stream/(?P<session_id>[0-9a-fA-F-]{36})/?$")


class Command(BaseCommand):
    help = "Run the Phase 0 Twilio Media Streams WebSocket server (spike)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--host", default="0.0.0.0")
        parser.add_argument("--port", type=int, default=8081)

    def handle(self, *args, **options):
        host = str(options.get("host") or "0.0.0.0")
        port = int(options.get("port") or 8081)
        self.stdout.write(self.style.SUCCESS(f"Starting voice spike WS server on ws://{host}:{port}"))

        async def _handler(ws, path: str):
            match = PATH_RE.match(path or "")
            if not match:
                await ws.close(code=1008, reason="invalid_path")
                return
            session_id = match.group("session_id")
            runtime = VoiceSpikeRuntime(session_id=session_id)
            await runtime.run_twilio_stream(ws)

        async def _run():
            async with websockets.serve(_handler, host, port, ping_interval=20, ping_timeout=20):
                await asyncio.Future()

        asyncio.run(_run())

