from __future__ import annotations

import asyncio
import time

from apps.voice.runtime.helpers import (
    _barge_in_min_chars,
    _chunk_text_for_tts,
    _clip_text,
    _detect_text_language,
    _final_stable_ms,
    _merge_transcript_segments,
    _normalize_lang_for_prompt,
    _pick_best_utterance,
    _resume_after_barge_in_seconds,
    _should_barge_in,
    _stream_target_fields,
    _tts_provider,
    _twilio_send,
)


class VoiceRuntimeTurnTakingMixin:

    async def _drain_utterances(self, utterance_queue: asyncio.Queue[dict[str, object]], twilio_ws) -> None:
        drained: list[dict[str, object]] = []
        while True:
            try:
                drained.append(utterance_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not drained:
            return

        now = time.monotonic()
        vad_items = [item for item in drained if str(item.get("event") or "").strip()]
        transcript_items = [item for item in drained if not str(item.get("event") or "").strip()]

        for item in vad_items:
            ev = str(item.get("event") or "").strip()
            if ev == "vad.speech_started":
                self._customer_speaking = True
                self._last_vad_speech_started_at = now
                self._last_customer_activity_at = now
            elif ev == "vad.utterance_end":
                self._customer_speaking = False
                self._last_vad_utterance_end_at = now

        final_items = [item for item in transcript_items if bool(item.get("is_final"))]
        interim_items = [item for item in transcript_items if not bool(item.get("is_final"))]

        if interim_items:
            # Phase 1 turn-taking: use interim STT for barge-in only. Do not respond until a
            # debounced final utterance is ready.
            best = _pick_best_utterance(interim_items)
            text = str(best.get("text") or "").strip()
            if text and _should_barge_in(text, min_chars=_barge_in_min_chars()):
                active_speech = bool(self._current_speak_task and not self._current_speak_task.done())
                barge_in_at = time.monotonic()
                if active_speech:
                    self._last_barge_in_at = barge_in_at
                    self._last_barge_in_text = _clip_text(text, 120)
                    await self._log_event(
                        "call.barge_in",
                        {
                            "text": _clip_text(text, 80),
                            "stt_language": str(best.get("stt_language") or ""),
                            "has_pending_final": bool(self._pending_final_text),
                        },
                    )
                self._last_customer_activity_at = time.monotonic()
                if self._awaiting_first_customer:
                    self._awaiting_first_customer = False
                await self._interrupt_speech(twilio_ws)
                if active_speech:
                    await self._schedule_auto_resume_after_barge_in(
                        twilio_ws,
                        barge_in_at=barge_in_at,
                        response_id=self._speaking_response_id,
                    )
                # If we already have a pending final transcript, keep pushing the debounce
                # window forward while the customer is still speaking (prevents cutoffs when
                # endpointing is aggressive).
                if self._pending_final_text:
                    self._pending_final_updated_at = time.monotonic()

        if final_items:
            self._last_customer_activity_at = time.monotonic()
            best = _pick_best_utterance(final_items)
            self._enqueue_final(best, twilio_ws)

    def _enqueue_final(self, payload: dict[str, object], twilio_ws) -> None:
        text = str(payload.get("text") or "").strip()
        if not text:
            return
        self._pending_final_text = _merge_transcript_segments(self._pending_final_text, text)
        try:
            confidence = float(payload.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        self._pending_final_confidence = max(self._pending_final_confidence, confidence)
        self._pending_final_language = str(payload.get("stt_language") or self._pending_final_language or "").strip()
        self._pending_final_updated_at = time.monotonic()

        if self._pending_final_task and not self._pending_final_task.done():
            return
        self._pending_final_task = asyncio.create_task(self._flush_pending_final(twilio_ws))

    async def _flush_pending_final(self, twilio_ws) -> None:
        """
        Debounce final transcripts to avoid responding mid-sentence when the STT
        engine emits short finals (aggressive endpointing) and continues emitting
        interims/finals as the customer keeps speaking.
        """
        stable_ms = _final_stable_ms()
        stable_s = stable_ms / 1000.0
        try:
            while True:
                await asyncio.sleep(stable_s)
                now = time.monotonic()
                if not self._pending_final_text:
                    return
                if (now - self._pending_final_updated_at) < stable_s:
                    continue
                text = self._pending_final_text.strip()
                confidence = float(self._pending_final_confidence or 0.0)
                stt_language = str(self._pending_final_language or "").strip()

                self._pending_final_text = ""
                self._pending_final_confidence = 0.0
                self._pending_final_language = ""
                self._pending_final_updated_at = 0.0

                await self._handle_transcript(
                    {"text": text, "confidence": confidence, "stt_language": stt_language, "is_final": True},
                    twilio_ws,
                    source="final",
                )
                return
        finally:
            self._pending_final_task = None

    async def _interrupt_speech(self, twilio_ws) -> None:
        if self._current_speak_task and not self._current_speak_task.done():
            self._current_speak_task.cancel()
            self._last_agent_speech_end_at = time.monotonic()
        if self._stream_sid:
            payload = {"event": "clear"}
            payload.update(_stream_target_fields(self._stream_sid))
            await _twilio_send(twilio_ws, payload)

    async def _schedule_auto_resume_after_barge_in(self, twilio_ws, *, barge_in_at: float, response_id: int) -> None:
        if response_id <= 0:
            return
        self._auto_resume_expected_response_id = response_id
        self._auto_resume_expected_barge_in_at = float(barge_in_at or 0.0)
        if self._auto_resume_task and not self._auto_resume_task.done():
            self._auto_resume_task.cancel()
        self._auto_resume_task = asyncio.create_task(
            self._auto_resume_after_barge_in(twilio_ws, barge_in_at=barge_in_at, response_id=response_id)
        )

    async def _auto_resume_after_barge_in(self, twilio_ws, *, barge_in_at: float, response_id: int) -> None:
        """
        If barge-in was triggered by noise/echo (no real customer final follows),
        continue speaking the remaining text from the interrupted agent response.
        """
        delay = _resume_after_barge_in_seconds()
        max_wait_for_text_s = 2.0
        try:
            await asyncio.sleep(delay)
            if self._terminated:
                return
            if self._waiting_for_callback_time:
                return
            if self._last_customer_final_at > barge_in_at:
                return
            if self._customer_speaking:
                return
            if self._pending_final_text or (self._pending_final_task and not self._pending_final_task.done()):
                return

            deadline = time.monotonic() + max_wait_for_text_s
            while time.monotonic() < deadline:
                if self._terminated:
                    return
                if self._last_customer_final_at > barge_in_at:
                    return
                if self._customer_speaking:
                    return
                if self._auto_resume_response_id == response_id and self._auto_resume_text:
                    break
                await asyncio.sleep(0.1)

            if not (self._auto_resume_response_id == response_id and self._auto_resume_text):
                return
            if self._current_speak_task and not self._current_speak_task.done():
                return

            session = await self._get_session()
            lang_hint = _normalize_lang_for_prompt(session.language or "") or "en"
            await self._log_event(
                "call.resume.start",
                {
                    "response_id": response_id,
                    "remaining_chars": len(self._auto_resume_text),
                },
            )
            self._current_speak_task = asyncio.create_task(
                self._speak_auto_resume(
                    twilio_ws,
                    response_id=response_id,
                    text=self._auto_resume_text,
                    language_hint=lang_hint,
                )
            )
        except asyncio.CancelledError:
            return
        finally:
            current = asyncio.current_task()
            if self._auto_resume_task is current:
                self._auto_resume_task = None

    async def _speak_auto_resume(self, twilio_ws, *, response_id: int, text: str, language_hint: str | None) -> None:
        self._speaking_response_id = response_id
        chunks = _chunk_text_for_tts(text)
        completed = 0
        provider = _tts_provider()
        try:
            for idx, chunk in enumerate(chunks):
                await self._log_event(
                    "tts.resume.chunk",
                    {"response_id": response_id, "chunk_index": idx, "text": _clip_text(chunk, 160)},
                )
                tts_lang = _detect_text_language(chunk, fallback=language_hint)
                if provider == "deepgram" and tts_lang != "ar":
                    dg_cfg = await self._get_deepgram_tts_config(language=tts_lang)
                    await self._stream_tts_to_twilio(twilio_ws, chunk, deepgram_config=dg_cfg)
                else:
                    tts_cfg = await self._get_tts_config(language=tts_lang)
                    await self._stream_tts_to_twilio(twilio_ws, chunk, config=tts_cfg)
                completed = idx + 1
            if chunks:
                spoken_text = " ".join(chunks[:completed]).strip()
                if spoken_text:
                    self._history.append(("agent", spoken_text))
                    self._history = self._history[-(self.runtime_config.max_history_turns * 2) :]
            self._last_agent_speech_end_at = time.monotonic()
            if self._auto_resume_marks_goal_delivered and not self._goal_delivered:
                self._goal_delivered = True
                session = await self._get_session()
                await self._log_event("goal.delivered", {"objective": session.objective})
            await self._log_event("call.resume.completed", {"response_id": response_id})
            self._auto_resume_text = ""
            self._auto_resume_response_id = 0
            self._auto_resume_marks_goal_delivered = False
            self._auto_resume_expected_response_id = 0
            self._auto_resume_expected_barge_in_at = 0.0
            self._interrupted_agent_remaining = ""
            self._interrupted_agent_remaining_at = 0.0
            self._interrupted_agent_response_id = 0
            self._interrupted_agent_marks_goal_delivered = False
        except asyncio.CancelledError:
            self._last_agent_speech_end_at = time.monotonic()
            spoken_text = " ".join(chunks[:completed]).strip()
            if spoken_text:
                self._history.append(("agent", spoken_text))
                self._history = self._history[-(self.runtime_config.max_history_turns * 2) :]
            remaining = " ".join(chunks[completed:]).strip()
            if remaining:
                self._auto_resume_text = remaining
                self._auto_resume_response_id = response_id
                self._interrupted_agent_remaining = remaining
                self._interrupted_agent_remaining_at = time.monotonic()
                self._interrupted_agent_response_id = response_id
            await self._log_event(
                "call.resume.interrupted",
                {"response_id": response_id, "remaining_chars": len(remaining)},
            )
            raise
        finally:
            if self._speaking_response_id == response_id:
                self._speaking_response_id = 0

    async def _finalize_interrupted_llm_response(
        self,
        *,
        response_id: int,
        llm_task: asyncio.Task,
        response_text_parts: list[str],
        spoken_chunk_count: int,
        deliver_goal: bool,
    ) -> None:
        try:
            await llm_task
        except Exception:
            return

        full_text = "".join(response_text_parts).strip()
        if not full_text:
            return

        await self._log_event("llm.response.final", {"text": full_text, "interrupted": True, "response_id": response_id})

        chunks = _chunk_text_for_tts(full_text)
        remaining = " ".join(chunks[int(spoken_chunk_count) :]).strip()
        if not remaining:
            return

        self._interrupted_agent_remaining = remaining
        self._interrupted_agent_remaining_at = time.monotonic()
        self._interrupted_agent_response_id = response_id
        self._interrupted_agent_marks_goal_delivered = deliver_goal

        if self._auto_resume_expected_response_id == response_id and self._auto_resume_expected_barge_in_at > 0:
            self._auto_resume_text = remaining
            self._auto_resume_response_id = response_id
            self._auto_resume_marks_goal_delivered = deliver_goal

        await self._log_event(
            "call.agent_speech.interrupted",
            {
                "response_id": response_id,
                "spoken_chunks": int(spoken_chunk_count),
                "total_chunks": len(chunks),
                "remaining_chars": len(remaining),
                "barge_in_text": self._last_barge_in_text,
            },
        )
