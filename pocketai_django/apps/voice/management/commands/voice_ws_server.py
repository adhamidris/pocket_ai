from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import parse_qs, urlparse

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand

import websockets

from apps.voice.models import CallSession
from apps.voice.runtime import VoiceCallRuntime


logger = logging.getLogger(__name__)

PATH_RE = re.compile(r"^/voice/stream/(?P<session_id>[0-9a-fA-F-]{36})/?$")


class Command(BaseCommand):
    help = "Run the Twilio Media Streams WebSocket server (Phase 1)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--host", default="0.0.0.0")
        parser.add_argument("--port", type=int, default=8081)

    def handle(self, *args, **options):
        host = str(options.get("host") or "0.0.0.0")
        port = int(options.get("port") or 8081)
        self.stdout.write(self.style.SUCCESS(f"Starting voice WS server on ws://{host}:{port}"))

        async def _handler(ws, path: str):
            parsed = urlparse(path or "")
            match = PATH_RE.match(parsed.path or "")
            if not match:
                await ws.close(code=1008, reason="invalid_path")
                return

            token = (parse_qs(parsed.query or "").get("token") or [""])[0]
            if not token:
                await ws.close(code=1008, reason="missing_token")
                return

            session_id = match.group("session_id")

            async def _load_token() -> str:
                def _query() -> str:
                    row = CallSession.objects.filter(id=session_id).values_list("stream_token", flat=True).first()
                    return str(row or "")

                return await sync_to_async(_query)()

            expected = await _load_token()
            if not expected or expected != token:
                await ws.close(code=1008, reason="invalid_token")
                return

            runtime = VoiceCallRuntime(session_id=session_id)
            await runtime.run_twilio_stream(ws)

        async def _run():
            async with websockets.serve(_handler, host, port, ping_interval=20, ping_timeout=20):
                await asyncio.Future()

        asyncio.run(_run())

