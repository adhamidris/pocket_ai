import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from unittest import IsolatedAsyncioTestCase

from apps.voice.runtime import VoiceCallRuntime, _has_hangup_action


class _FakeTwilioWs:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


@dataclass
class _FakeSession:
    consent_obtained: bool = True
    objective: str = "Test objective"
    language: str = "en"
    country: str = "US"
    context_items: list[dict] = field(default_factory=list)
    twilio_call_sid: str = "CA_TEST"


class VoicePhase2ConversationPolicyTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        # The runtime clamps these values for safety; keep tests fast but within bounds.
        os.environ["VOICE_CALL_NO_ENGAGEMENT_HELLO_SECONDS"] = "0.5"
        os.environ["VOICE_CALL_NO_ENGAGEMENT_GOODBYE_SECONDS"] = "0.5"
        os.environ["VOICE_CALL_SILENCE_CLOSE_SECONDS"] = "2.0"
        os.environ["VOICE_CALL_SILENCE_HANGUP_AFTER_CLOSE_SECONDS"] = "2.0"

    def tearDown(self) -> None:
        for key in (
            "VOICE_CALL_NO_ENGAGEMENT_HELLO_SECONDS",
            "VOICE_CALL_NO_ENGAGEMENT_GOODBYE_SECONDS",
            "VOICE_CALL_SILENCE_CLOSE_SECONDS",
            "VOICE_CALL_SILENCE_HANGUP_AFTER_CLOSE_SECONDS",
        ):
            os.environ.pop(key, None)
        super().tearDown()

    async def test_wrong_person_strong_denial_hangs_up(self) -> None:
        runtime = VoiceCallRuntime(session_id="session-test")
        runtime._stream_sid = "stream"

        session = _FakeSession(context_items=[{"customer_name": "Adham Idris"}])

        async def _get_session(*, force_refresh: bool = False):
            return session

        runtime._get_session = _get_session  # type: ignore[method-assign]

        async def _fake_log_event(*args, **kwargs) -> None:
            return None

        runtime._log_event = _fake_log_event  # type: ignore[method-assign]

        hangups: list[str] = []

        async def _fake_hangup_call(*, reason: str) -> None:
            hangups.append(reason)
            runtime._terminated = True

        runtime._hangup_call = _fake_hangup_call  # type: ignore[method-assign]

        async def _fake_speak_then_hangup(_ws, *, text: str, language_hint: str | None, reason: str) -> None:
            await runtime._hangup_call(reason=reason)  # type: ignore[misc]

        runtime._speak_then_hangup = _fake_speak_then_hangup  # type: ignore[method-assign]

        called_llm: list[bool] = []

        async def _fake_respond_and_speak(*args, **kwargs) -> None:
            called_llm.append(True)

        runtime._respond_and_speak = _fake_respond_and_speak  # type: ignore[method-assign]

        ws = _FakeTwilioWs()
        await runtime._handle_transcript(
            {"text": "No, this is not Adham Idris", "confidence": 0.9, "stt_language": "en", "is_final": True},
            ws,
            source="final",
        )
        await asyncio.sleep(0.05)
        self.assertEqual(hangups, ["wrong_person"])
        self.assertEqual(called_llm, [])

    async def test_busy_asks_callback_then_hangup_on_answer(self) -> None:
        runtime = VoiceCallRuntime(session_id="session-test")
        runtime._stream_sid = "stream"

        session = _FakeSession(context_items=[{"customer_name": "Adham Idris"}])

        async def _get_session(*, force_refresh: bool = False):
            return session

        runtime._get_session = _get_session  # type: ignore[method-assign]

        async def _fake_log_event(*args, **kwargs) -> None:
            return None

        runtime._log_event = _fake_log_event  # type: ignore[method-assign]

        spoken: list[str] = []
        stored: list[str] = []
        hangups: list[str] = []

        async def _fake_speak_text(_ws, *, text: str, language_hint: str | None, event_type: str) -> None:
            spoken.append(text)
            runtime._last_agent_speech_end_at = 1.0

        runtime._speak_text = _fake_speak_text  # type: ignore[method-assign]

        async def _fake_store_callback_time(text: str) -> None:
            stored.append(text)

        runtime._store_callback_time = _fake_store_callback_time  # type: ignore[method-assign]

        async def _fake_hangup_call(*, reason: str) -> None:
            hangups.append(reason)
            runtime._terminated = True

        runtime._hangup_call = _fake_hangup_call  # type: ignore[method-assign]

        async def _fake_speak_then_hangup(_ws, *, text: str, language_hint: str | None, reason: str) -> None:
            spoken.append(text)
            await runtime._hangup_call(reason=reason)  # type: ignore[misc]

        runtime._speak_then_hangup = _fake_speak_then_hangup  # type: ignore[method-assign]

        ws = _FakeTwilioWs()
        await runtime._handle_transcript(
            {"text": "I'm in a meeting", "confidence": 0.9, "stt_language": "en", "is_final": True},
            ws,
            source="final",
        )
        await asyncio.sleep(0.05)
        self.assertTrue(runtime._waiting_for_callback_time)
        self.assertTrue(any("call you back" in msg.lower() for msg in spoken))

        await runtime._handle_transcript(
            {"text": "Tomorrow at 3pm", "confidence": 0.9, "stt_language": "en", "is_final": True},
            ws,
            source="final",
        )
        await asyncio.sleep(0.05)
        self.assertEqual(stored, ["Tomorrow at 3pm"])
        self.assertEqual(hangups, ["callback_time_captured"])

    async def test_no_engagement_prompts_then_hangup(self) -> None:
        runtime = VoiceCallRuntime(session_id="session-test")
        runtime._stream_sid = "stream"

        session = _FakeSession()

        async def _get_session(*, force_refresh: bool = False):
            return session

        runtime._get_session = _get_session  # type: ignore[method-assign]

        spoken: list[str] = []
        hangups: list[str] = []

        async def _fake_log_event(*args, **kwargs) -> None:
            return None

        runtime._log_event = _fake_log_event  # type: ignore[method-assign]

        async def _fake_interrupt_speech(*args, **kwargs) -> None:
            return None

        runtime._interrupt_speech = _fake_interrupt_speech  # type: ignore[method-assign]

        async def _fake_respond_and_speak(*args, **kwargs) -> None:
            runtime._last_agent_speech_end_at = 1.0

        runtime._respond_and_speak = _fake_respond_and_speak  # type: ignore[method-assign]

        async def _fake_speak_text(_ws, *, text: str, language_hint: str | None, event_type: str) -> None:
            spoken.append(text)
            runtime._last_agent_speech_end_at = 1.0

        runtime._speak_text = _fake_speak_text  # type: ignore[method-assign]

        async def _fake_hangup_call(*, reason: str) -> None:
            hangups.append(reason)
            runtime._terminated = True

        runtime._hangup_call = _fake_hangup_call  # type: ignore[method-assign]

        async def _fake_speak_then_hangup(_ws, *, text: str, language_hint: str | None, reason: str) -> None:
            spoken.append(text)
            await runtime._hangup_call(reason=reason)  # type: ignore[misc]

        runtime._speak_then_hangup = _fake_speak_then_hangup  # type: ignore[method-assign]

        ws = _FakeTwilioWs()
        await runtime._maybe_greet(ws)
        await asyncio.sleep(1.4)

        self.assertTrue(any(msg.strip().lower() == "hello?" for msg in spoken))
        self.assertEqual(hangups, ["no_engagement"])

        # Ensure background tasks are not left running.
        for task in (runtime._no_engagement_task, runtime._silence_close_task):
            if task and not task.done():
                task.cancel()

    async def test_silence_close_triggers_closing_prompt(self) -> None:
        runtime = VoiceCallRuntime(session_id="session-test")
        runtime._stream_sid = "stream"
        runtime._awaiting_first_customer = False
        runtime._last_customer_activity_at = time.monotonic() - 1.0
        runtime._last_agent_speech_end_at = time.monotonic() - 1.0

        session = _FakeSession()

        async def _get_session(*, force_refresh: bool = False):
            return session

        runtime._get_session = _get_session  # type: ignore[method-assign]

        called: list[dict] = []

        async def _fake_respond_and_speak(_ws, *, customer_text: str, customer_language: str | None = None, is_greeting: bool = False, is_closing_prompt: bool = False, closing_check: bool = False) -> None:  # noqa: E501
            called.append({"is_closing_prompt": is_closing_prompt, "closing_check": closing_check})
            runtime._last_agent_speech_end_at = 1.0

        runtime._respond_and_speak = _fake_respond_and_speak  # type: ignore[method-assign]

        async def _fake_log_event(*args, **kwargs) -> None:
            return None

        runtime._log_event = _fake_log_event  # type: ignore[method-assign]

        async def _fake_interrupt_speech(*args, **kwargs) -> None:
            return None

        runtime._interrupt_speech = _fake_interrupt_speech  # type: ignore[method-assign]

        ws = _FakeTwilioWs()
        task = asyncio.create_task(runtime._watch_silence_close(ws))
        await asyncio.sleep(2.6)
        task.cancel()

        self.assertTrue(any(entry.get("is_closing_prompt") for entry in called))
        self.assertTrue(runtime._closing_waiting_for_customer)

    def test_has_hangup_action_accepts_strings_and_dicts(self) -> None:
        self.assertTrue(_has_hangup_action(["hangup"]))
        self.assertTrue(_has_hangup_action([{"type": "hangup"}]))
        self.assertFalse(_has_hangup_action([]))
