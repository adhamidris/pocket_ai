import asyncio
import json
import os
import time
from dataclasses import dataclass
from unittest import IsolatedAsyncioTestCase

from apps.voice.runtime import VoiceCallRuntime


class _FakeTwilioWs:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


@dataclass
class _FakeSession:
    consent_obtained: bool = True
    language: str = "en"
    objective: str = "Test objective"
    country: str = "US"
    context_items: list[dict] = None  # type: ignore[assignment]


class VoicePhase5AutoResumeTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        os.environ["VOICE_CALL_RESUME_AFTER_BARGE_IN_SECONDS"] = "0.5"

    def tearDown(self) -> None:
        os.environ.pop("VOICE_CALL_RESUME_AFTER_BARGE_IN_SECONDS", None)
        super().tearDown()

    async def test_interrupt_speech_updates_last_agent_speech_end_at(self) -> None:
        runtime = VoiceCallRuntime(session_id="session-test")
        runtime._stream_sid = "stream"
        runtime._current_speak_task = asyncio.create_task(asyncio.sleep(10))

        ws = _FakeTwilioWs()
        before = runtime._last_agent_speech_end_at
        await runtime._interrupt_speech(ws)

        await asyncio.sleep(0)
        self.assertGreater(runtime._last_agent_speech_end_at, before)
        self.assertTrue(any(msg.get("event") == "clear" for msg in ws.sent))

    async def test_auto_resume_speaks_remaining_text_when_no_customer_final(self) -> None:
        runtime = VoiceCallRuntime(session_id="session-test")
        runtime._stream_sid = "stream"
        runtime._auto_resume_response_id = 1
        runtime._auto_resume_text = "Continuing the sentence now."

        session = _FakeSession(context_items=[])

        async def _get_session(*, force_refresh: bool = False):
            return session

        runtime._get_session = _get_session  # type: ignore[method-assign]

        async def _fake_log_event(*args, **kwargs) -> None:
            return None

        runtime._log_event = _fake_log_event  # type: ignore[method-assign]

        spoken: list[str] = []

        async def _fake_speak_auto_resume(_ws, *, response_id: int, text: str, language_hint: str | None) -> None:
            spoken.append(text)

        runtime._speak_auto_resume = _fake_speak_auto_resume  # type: ignore[method-assign]

        ws = _FakeTwilioWs()
        barge_in_at = time.monotonic()
        await runtime._auto_resume_after_barge_in(ws, barge_in_at=barge_in_at, response_id=1)
        await asyncio.sleep(0.05)

        self.assertEqual(spoken, ["Continuing the sentence now."])

    async def test_auto_resume_does_not_run_after_customer_final(self) -> None:
        runtime = VoiceCallRuntime(session_id="session-test")
        runtime._stream_sid = "stream"
        runtime._auto_resume_response_id = 1
        runtime._auto_resume_text = "Should not be spoken."
        runtime._last_customer_final_at = time.monotonic()

        session = _FakeSession(context_items=[])

        async def _get_session(*, force_refresh: bool = False):
            return session

        runtime._get_session = _get_session  # type: ignore[method-assign]

        async def _fake_log_event(*args, **kwargs) -> None:
            return None

        runtime._log_event = _fake_log_event  # type: ignore[method-assign]

        spoken: list[str] = []

        async def _fake_speak_auto_resume(_ws, *, response_id: int, text: str, language_hint: str | None) -> None:
            spoken.append(text)

        runtime._speak_auto_resume = _fake_speak_auto_resume  # type: ignore[method-assign]

        ws = _FakeTwilioWs()
        barge_in_at = time.monotonic() - 1.0
        await runtime._auto_resume_after_barge_in(ws, barge_in_at=barge_in_at, response_id=1)
        await asyncio.sleep(0.05)

        self.assertEqual(spoken, [])

