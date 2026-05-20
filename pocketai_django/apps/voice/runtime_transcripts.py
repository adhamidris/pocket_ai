from __future__ import annotations

import asyncio
import time

from apps.voice.runtime_helpers import (
    _extract_recipient_name,
    _is_busy_signal,
    _is_wrong_person_strong,
    _normalize_lang_for_prompt,
    _should_skip_duplicate,
)


class VoiceRuntimeTranscriptMixin:

    async def _handle_transcript(self, payload: dict[str, object], twilio_ws, *, source: str) -> None:
        if self._terminated:
            return
        text = str(payload.get("text") or "").strip()
        if not text:
            return
        if _should_skip_duplicate(text, last_text=self._last_handled_text, last_at=self._last_handled_at):
            return
        confidence = float(payload.get("confidence") or 0.0)
        stt_language = str(payload.get("stt_language") or "").strip()
        event_type = "stt.final" if source == "final" else "stt.interim"
        await self._log_event(event_type, {"text": text, "confidence": confidence, "stt_language": stt_language})

        now = time.monotonic()
        self._last_customer_activity_at = now
        self._last_customer_final_at = now
        self._customer_speaking = False
        if self._auto_resume_task and not self._auto_resume_task.done():
            self._auto_resume_task.cancel()
        self._auto_resume_text = ""
        self._auto_resume_response_id = 0
        self._auto_resume_marks_goal_delivered = False
        self._auto_resume_expected_response_id = 0
        self._auto_resume_expected_barge_in_at = 0.0

        session = await self._get_session()
        language_hint = _normalize_lang_for_prompt(stt_language or session.language or "") or "en"
        recipient_name = _extract_recipient_name(session.context_items)

        # Any customer turn cancels a pending close-wait state; we'll re-enter later if silence persists.
        closing_check = False
        if self._closing_waiting_for_customer:
            closing_check = True
            self._closing_waiting_for_customer = False
            self._closing_question_asked_at = 0.0

        if self._awaiting_first_customer:
            self._awaiting_first_customer = False
            await self._log_event("call.customer.engaged", {"text": text})

        if self._waiting_for_callback_time:
            self._waiting_for_callback_time = False
            self._callback_time_text = text
            await self._store_callback_time(text)
            await self._log_event("call.callback_time.captured", {"text": text})
            await self._interrupt_speech(twilio_ws)
            confirm_text = "Thanks — when it’s convenient, I’ll call you back. Goodbye." if language_hint == "en" else "شكرًا، سأتصل بك في الوقت المناسب. مع السلامة."
            self._current_speak_task = asyncio.create_task(
                self._speak_then_hangup(
                    twilio_ws,
                    text=confirm_text,
                    language_hint=language_hint,
                    reason="callback_time_captured",
                )
            )
            return

        if _is_wrong_person_strong(text, language_hint=stt_language or session.language or "", recipient_name=recipient_name):
            await self._log_event("call.wrong_person", {"text": text, "recipient_name": recipient_name})
            await self._interrupt_speech(twilio_ws)
            apology = "Sorry about that — I’ll update our records. Goodbye." if language_hint == "en" else "أعتذر عن الإزعاج—سأقوم بتحديث بياناتنا. مع السلامة."
            self._current_speak_task = asyncio.create_task(
                self._speak_then_hangup(
                    twilio_ws,
                    text=apology,
                    language_hint=language_hint,
                    reason="wrong_person",
                )
            )
            return

        if _is_busy_signal(text, stt_language or session.language or ""):
            await self._log_event("call.busy", {"text": text})
            self._waiting_for_callback_time = True
            await self._interrupt_speech(twilio_ws)
            prompt_text = "No problem — when should I call you back?" if language_hint == "en" else "تمام، متى تحب أن أتصل بك مرة أخرى؟"
            self._current_speak_task = asyncio.create_task(
                self._speak_text(
                    twilio_ws,
                    text=prompt_text,
                    language_hint=language_hint,
                    event_type="call.busy.ask_callback",
                )
            )
            return

        self._history.append(("customer", text))
        self._history = self._history[-(self.runtime_config.max_history_turns * 2) :]
        self._last_handled_text = text
        self._last_handled_at = time.monotonic()

        await self._interrupt_speech(twilio_ws)
        self._current_speak_task = asyncio.create_task(
            self._respond_and_speak(
                twilio_ws,
                customer_text=text,
                customer_language=_normalize_lang_for_prompt(stt_language),
                closing_check=closing_check,
            )
        )

    # Note: Phase 1 disables responding to interim transcripts. We keep the
    # previous interim debounce implementation around for future experimentation.
