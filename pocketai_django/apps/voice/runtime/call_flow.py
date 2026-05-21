from __future__ import annotations

import asyncio
import time

import requests
from asgiref.sync import sync_to_async

from apps.voice.models import CallSession
from apps.voice.providers.credentials import (
    VOICE_PROVIDER_TELNYX,
    VOICE_PROVIDER_TWILIO,
    resolve_telnyx_config,
    resolve_twilio_config,
)
from apps.voice.runtime.helpers import (
    _detect_text_language,
    _no_engagement_goodbye_seconds,
    _no_engagement_hello_seconds,
    _normalize_lang_for_prompt,
    _silence_close_seconds,
    _silence_hangup_after_close_seconds,
    _tts_provider,
)


class VoiceRuntimeCallFlowMixin:

    async def _maybe_greet(self, twilio_ws) -> None:
        if self._greeted or self._terminated:
            return
        session = await self._get_session()
        if not session.consent_obtained:
            return
        self._greeted = True
        self._awaiting_first_customer = True
        self._closing_waiting_for_customer = False
        self._waiting_for_callback_time = False
        self._callback_time_text = ""

        if not self._no_engagement_task or self._no_engagement_task.done():
            self._no_engagement_task = asyncio.create_task(self._watch_no_engagement(twilio_ws))
        if not self._silence_close_task or self._silence_close_task.done():
            self._silence_close_task = asyncio.create_task(self._watch_silence_close(twilio_ws))
        await self._log_event(
            "call.timer.no_engagement.started",
            {"hello_seconds": _no_engagement_hello_seconds(), "goodbye_seconds": _no_engagement_goodbye_seconds()},
        )
        await self._log_event(
            "call.timer.silence_close.started",
            {
                "close_seconds": _silence_close_seconds(),
                "hangup_after_close_seconds": _silence_hangup_after_close_seconds(),
            },
        )

        await self._interrupt_speech(twilio_ws)
        lang_hint = _normalize_lang_for_prompt((session.language or "").strip().lower())
        self._current_speak_task = asyncio.create_task(
            self._respond_and_speak(
                twilio_ws,
                customer_text="",
                customer_language=lang_hint,
                is_greeting=True,
            )
        )

    async def _watch_no_engagement(self, twilio_ws) -> None:
        """
        Human-style "no answer" behavior after greeting:
        - wait ~4s, say "Hello?"
        - wait ~4s, say goodbye + hang up
        """
        try:
            # Don't start timers until the greeting finishes speaking.
            while not self._terminated and self._awaiting_first_customer:
                speak_task = self._current_speak_task
                if speak_task and not speak_task.done():
                    await asyncio.sleep(0.1)
                    continue
                break

            if self._terminated:
                await self._log_event("call.timer.no_engagement.cancelled", {"reason": "terminated"})
                return
            if not self._awaiting_first_customer:
                await self._log_event("call.timer.no_engagement.cancelled", {"reason": "customer_engaged"})
                return

            hello_delay = _no_engagement_hello_seconds()
            goodbye_delay = _no_engagement_goodbye_seconds()
            session = await self._get_session()
            lang_hint = _normalize_lang_for_prompt(session.language or "") or "en"
            await self._log_event(
                "call.timer.no_engagement.armed",
                {"hello_seconds": hello_delay, "goodbye_seconds": goodbye_delay},
            )

            start = time.monotonic()
            while not self._terminated and self._awaiting_first_customer and (time.monotonic() - start) < hello_delay:
                await asyncio.sleep(0.1)
            if self._terminated:
                await self._log_event("call.timer.no_engagement.cancelled", {"reason": "terminated"})
                return
            if not self._awaiting_first_customer:
                await self._log_event("call.timer.no_engagement.cancelled", {"reason": "customer_engaged"})
                return

            await self._log_event("call.no_engagement.hello", {})
            await self._interrupt_speech(twilio_ws)
            hello_text = "Hello?" if lang_hint == "en" else "ألو؟"
            self._current_speak_task = asyncio.create_task(
                self._speak_text(twilio_ws, text=hello_text, language_hint=lang_hint, event_type="tts.static.hello")
            )
            try:
                await self._current_speak_task
            except asyncio.CancelledError:
                return

            if self._terminated:
                await self._log_event("call.timer.no_engagement.cancelled", {"reason": "terminated"})
                return
            if not self._awaiting_first_customer:
                await self._log_event("call.timer.no_engagement.cancelled", {"reason": "customer_engaged"})
                return
            await self._log_event("call.timer.no_engagement.goodbye_armed", {"goodbye_seconds": goodbye_delay})

            start = time.monotonic()
            while not self._terminated and self._awaiting_first_customer and (time.monotonic() - start) < goodbye_delay:
                await asyncio.sleep(0.1)
            if self._terminated:
                await self._log_event("call.timer.no_engagement.cancelled", {"reason": "terminated"})
                return
            if not self._awaiting_first_customer:
                await self._log_event("call.timer.no_engagement.cancelled", {"reason": "customer_engaged"})
                return

            await self._log_event("call.no_engagement.goodbye", {})
            await self._interrupt_speech(twilio_ws)
            goodbye_text = "Okay, I'll let you go. Goodbye." if lang_hint == "en" else "حسنًا، سأغلق الآن. مع السلامة."
            self._current_speak_task = asyncio.create_task(
                self._speak_then_hangup(
                    twilio_ws,
                    text=goodbye_text,
                    language_hint=lang_hint,
                    reason="no_engagement",
                )
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            await self._log_event("call.no_engagement.error", {"error": str(exc)})

    async def _watch_silence_close(self, twilio_ws) -> None:
        """
        Silence-triggered closing:
        - after ~6–8s of silence (both parties), ask: "Anything else before I let you go?"
        - if still no response for another window, say goodbye + hang up
        """
        poll_s = 0.25
        close_after_s = _silence_close_seconds()
        hangup_after_close_s = _silence_hangup_after_close_seconds()
        try:
            await self._log_event(
                "call.timer.silence_close.running",
                {"close_seconds": close_after_s, "hangup_after_close_seconds": hangup_after_close_s},
            )
            while not self._terminated:
                await asyncio.sleep(poll_s)
                if self._terminated:
                    await self._log_event("call.timer.silence_close.stopped", {"reason": "terminated"})
                    return
                if self._awaiting_first_customer:
                    continue
                if self._waiting_for_callback_time:
                    continue
                if self._auto_resume_task and not self._auto_resume_task.done():
                    continue
                speak_task = self._current_speak_task
                if speak_task and not speak_task.done():
                    continue

                now = time.monotonic()
                last_activity = max(self._last_customer_activity_at, self._last_agent_speech_end_at, 0.0)
                if last_activity <= 0.0:
                    continue

                if self._closing_waiting_for_customer:
                    if hangup_after_close_s > 0 and (now - max(last_activity, self._closing_question_asked_at)) >= hangup_after_close_s:
                        session = await self._get_session()
                        lang_hint = _normalize_lang_for_prompt(session.language or "") or "en"
                        await self._log_event(
                            "call.closing.silence_hangup",
                            {
                                "hangup_after_close_seconds": hangup_after_close_s,
                                "silence_seconds": now - max(last_activity, self._closing_question_asked_at),
                            },
                        )
                        await self._interrupt_speech(twilio_ws)
                        goodbye_text = "Okay, I'll let you go. Goodbye." if lang_hint == "en" else "حسنًا، سأغلق الآن. مع السلامة."
                        self._current_speak_task = asyncio.create_task(
                            self._speak_then_hangup(
                                twilio_ws,
                                text=goodbye_text,
                                language_hint=lang_hint,
                                reason="silence_after_closing",
                            )
                        )
                        return
                    continue

                if (now - last_activity) >= close_after_s:
                    session = await self._get_session()
                    lang_hint = _normalize_lang_for_prompt(session.language or "") or "en"
                    self._closing_waiting_for_customer = True
                    self._closing_question_asked_at = now
                    await self._log_event(
                        "call.closing.prompt",
                        {
                            "after_seconds": close_after_s,
                            "silence_seconds": now - last_activity,
                            "hangup_after_close_seconds": hangup_after_close_s,
                        },
                    )
                    await self._interrupt_speech(twilio_ws)
                    self._current_speak_task = asyncio.create_task(
                        self._respond_and_speak(
                            twilio_ws,
                            customer_text="",
                            customer_language=lang_hint,
                            is_closing_prompt=True,
                        )
                    )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            await self._log_event("call.silence_close.error", {"error": str(exc)})

    async def _speak_text(self, twilio_ws, *, text: str, language_hint: str | None, event_type: str) -> None:
        try:
            await self._log_event(event_type, {"text": text})
            tts_lang = _detect_text_language(text, fallback=language_hint)
            provider = _tts_provider()
            if provider == "deepgram" and tts_lang != "ar":
                dg_cfg = await self._get_deepgram_tts_config(language=tts_lang)
                await self._stream_tts_to_twilio(twilio_ws, text, deepgram_config=dg_cfg)
            else:
                tts_cfg = await self._get_tts_config(language=tts_lang)
                await self._stream_tts_to_twilio(twilio_ws, text, config=tts_cfg)
            self._last_agent_speech_end_at = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._log_event("tts.static.error", {"error": str(exc), "event_type": event_type})

    async def _speak_then_hangup(self, twilio_ws, *, text: str, language_hint: str | None, reason: str) -> None:
        try:
            if text:
                await self._speak_text(twilio_ws, text=text, language_hint=language_hint, event_type="tts.static.hangup")
        finally:
            await self._hangup_call(reason=reason)

    async def _hangup_call(self, *, reason: str) -> None:
        if self._terminated:
            return
        self._terminated = True
        await self._log_event("call.hangup.requested", {"reason": reason})
        session = await self._get_session()
        provider = str(session.transport_provider or "").strip().lower()
        if not provider:
            provider = VOICE_PROVIDER_TWILIO if session.twilio_call_sid else ""
        call_sid = str(session.provider_call_sid or session.twilio_call_sid or "").strip()
        if not call_sid:
            await self._log_event("call.hangup.missing_call_sid", {"reason": reason})
            return

        if provider == VOICE_PROVIDER_TWILIO:
            try:
                cfg = await sync_to_async(resolve_twilio_config)(
                    business_id=session.business_profile_id,
                    require_from_number=False,
                )
            except Exception as exc:
                await self._log_event("call.hangup.missing_twilio_config", {"reason": reason, "error": str(exc)})
                return

            url = f"https://api.twilio.com/2010-04-01/Accounts/{cfg.account_sid}/Calls/{call_sid}.json"

            def _post() -> requests.Response:
                return requests.post(
                    url,
                    auth=(cfg.account_sid, cfg.auth_token),
                    data={"Status": "completed"},
                    timeout=20,
                )
        elif provider == VOICE_PROVIDER_TELNYX:
            try:
                cfg = await sync_to_async(resolve_telnyx_config)(
                    business_id=session.business_profile_id,
                    require_from_number=False,
                )
            except Exception as exc:
                await self._log_event("call.hangup.missing_telnyx_config", {"reason": reason, "error": str(exc)})
                return

            url = f"https://api.telnyx.com/v2/texml/Accounts/{cfg.account_sid}/Calls/{call_sid}"

            def _post() -> requests.Response:
                return requests.post(
                    url,
                    headers={"Authorization": f"Bearer {cfg.api_key}", "Accept": "application/json"},
                    data={"Status": "completed"},
                    timeout=20,
                )
        else:
            await self._log_event("call.hangup.unsupported_provider", {"reason": reason, "provider": provider})
            return

        try:
            resp = await asyncio.to_thread(_post)
        except Exception as exc:
            await self._log_event("call.hangup.request_failed", {"reason": reason, "error": str(exc)})
            return

        if resp.status_code >= 400:
            await self._log_event(
                "call.hangup.failed",
                {"reason": reason, "status": int(resp.status_code), "body": str(resp.text or "")[:300]},
            )
            return
        await self._log_event("call.hangup.ok", {"reason": reason})

    async def _store_callback_time(self, text: str) -> None:
        value = str(text or "").strip()
        if not value:
            return

        def _update() -> None:
            session = CallSession.objects.filter(id=self.session_id).first()
            if not session:
                return
            insights = session.insights if isinstance(getattr(session, "insights", None), dict) else {}
            insights = dict(insights)
            follow_ups = insights.get("follow_ups")
            if not isinstance(follow_ups, list):
                follow_ups = []
            follow_ups = [item for item in follow_ups if not (isinstance(item, dict) and item.get("type") == "callback")]
            follow_ups.append({"type": "callback", "callback_time_text": value, "captured_during_call": True})
            insights.setdefault("schema_version", 1)
            insights["follow_ups"] = follow_ups
            session.insights = insights
            session.save(update_fields=["insights", "updated_at"])

        await sync_to_async(_update)()
