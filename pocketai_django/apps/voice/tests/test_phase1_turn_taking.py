import asyncio
import json
import os
from unittest import IsolatedAsyncioTestCase

from apps.voice.runtime import VoiceCallRuntime, _merge_transcript_segments


class _FakeTwilioWs:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


class VoicePhase1TurnTakingTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        os.environ["VOICE_STT_FINAL_STABLE_MS"] = "200"
        os.environ["VOICE_STT_BARGE_IN_MIN_CHARS"] = "2"

    def tearDown(self) -> None:
        for key in ("VOICE_STT_FINAL_STABLE_MS", "VOICE_STT_BARGE_IN_MIN_CHARS"):
            os.environ.pop(key, None)
        super().tearDown()

    def test_merge_transcript_segments_expands_when_prefix(self) -> None:
        merged = _merge_transcript_segments("I am not", "I am not Adham Idris")
        self.assertEqual(merged, "I am not Adham Idris")

    def test_merge_transcript_segments_appends_when_no_overlap(self) -> None:
        merged = _merge_transcript_segments("I am not", "a concerned person.")
        self.assertEqual(merged, "I am not a concerned person.")

    async def test_interim_barges_in_but_does_not_trigger_response(self) -> None:
        runtime = VoiceCallRuntime(session_id="session-test")
        runtime._stream_sid = "stream"
        runtime._current_speak_task = asyncio.create_task(asyncio.sleep(10))

        async def _fake_log_event(*args, **kwargs) -> None:
            return None

        runtime._log_event = _fake_log_event  # type: ignore[method-assign]

        ws = _FakeTwilioWs()

        called: list[str] = []

        async def _fake_handle_transcript(payload, _ws, *, source: str) -> None:
            called.append(f"{source}:{payload.get('text')}")

        runtime._handle_transcript = _fake_handle_transcript  # type: ignore[method-assign]

        q: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        await q.put({"text": "no", "confidence": 0.6, "stt_language": "en", "is_final": False})
        await runtime._drain_utterances(q, ws)

        await asyncio.sleep(0.25)
        self.assertEqual(called, [])
        self.assertTrue(any(msg.get("event") == "clear" for msg in ws.sent))

    async def test_barge_in_extends_pending_final_debounce_window(self) -> None:
        runtime = VoiceCallRuntime(session_id="session-test")
        runtime._stream_sid = "stream"
        runtime._current_speak_task = asyncio.create_task(asyncio.sleep(10))
        runtime._awaiting_first_customer = True
        runtime._pending_final_text = "I am not"
        runtime._pending_final_updated_at = 0.01

        async def _fake_log_event(*args, **kwargs) -> None:
            return None

        runtime._log_event = _fake_log_event  # type: ignore[method-assign]

        ws = _FakeTwilioWs()

        q: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        await q.put({"text": "no", "confidence": 0.6, "stt_language": "en", "is_final": False})
        await runtime._drain_utterances(q, ws)

        self.assertFalse(runtime._awaiting_first_customer)
        self.assertGreater(runtime._last_customer_activity_at, 0.0)
        self.assertGreater(runtime._pending_final_updated_at, 0.01)

    async def test_final_transcripts_are_debounced_and_merged(self) -> None:
        runtime = VoiceCallRuntime(session_id="session-test")
        ws = _FakeTwilioWs()

        called: list[str] = []

        async def _fake_handle_transcript(payload, _ws, *, source: str) -> None:
            called.append(str(payload.get("text") or ""))

        runtime._handle_transcript = _fake_handle_transcript  # type: ignore[method-assign]

        runtime._enqueue_final({"text": "I am not", "confidence": 0.9, "stt_language": "en", "is_final": True}, ws)
        await asyncio.sleep(0.05)
        runtime._enqueue_final({"text": "Adham Idris", "confidence": 0.9, "stt_language": "en", "is_final": True}, ws)

        await asyncio.sleep(0.5)
        self.assertEqual(called, ["I am not Adham Idris"])
