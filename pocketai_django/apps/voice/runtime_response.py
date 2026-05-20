from __future__ import annotations

import asyncio
import time
from typing import Callable

from apps.llm.ai_prompt_builder import PromptBundle
from apps.llm.llm_provider import DeepSeekChatProvider, OpenAIChatProvider, _ResponseTextExtractor, load_default_provider
from apps.voice.deepgram_tts import DeepgramTTSWSSession
from apps.voice.elevenlabs_tts import ElevenLabsWSSession
from apps.voice.runtime_helpers import (
    _clip_text,
    _deepgram_ws_send,
    _detect_text_language,
    _extract_recipient_name,
    _has_hangup_action,
    _maybe_extract_speakable_chunk,
    _maybe_extract_speakable_chunk_first,
    _tts_provider,
)


class VoiceRuntimeResponseMixin:

    async def _respond_and_speak(
        self,
        twilio_ws,
        *,
        customer_text: str,
        customer_language: str | None = None,
        is_greeting: bool = False,
        is_closing_prompt: bool = False,
        closing_check: bool = False,
    ) -> None:
        session = await self._get_session()
        if not session.consent_obtained:
            await self._log_event("guard.no_consent", {})
            return

        provider = load_default_provider()
        if not provider:
            await self._log_event("llm.disabled", {})
            return
        self._agent_response_seq += 1
        response_id = self._agent_response_seq
        self._speaking_response_id = response_id

        system_prompt = (
            "You are a phone-call agent for a business. Be natural, concise, and helpful.\n"
            "Never hallucinate. Never invent prices, fees, policies, dates, or promises.\n"
            "Only state facts that are explicitly present in the provided context or said by the customer.\n"
            "If you don't have confirmed information, say you don't have it and offer a follow-up call.\n"
            "Language policy: respond in the customer's language (Arabic or English). If the customer code-switches, you may code-switch.\n"
            "If speaking Arabic, prefer clear Modern Standard Arabic unless the customer uses a dialect.\n"
            "Ask short clarifying questions when needed.\n"
            "Return JSON with keys: response_text (string), actions (array), extractions (empty array).\n"
            "Allowed actions: hangup (payload may be empty). Use hangup only if you are ending the call.\n"
        )
        history_lines = "\n".join(f"{role}: {text}" for role, text in self._history[-12:])
        customer_language_hint = (customer_language or session.language or "").strip().lower()
        context_block = await self._build_context_block()

        recipient_name = _extract_recipient_name(session.context_items)
        # Humanized flow: greet first, then deliver the objective on the customer's first reply.
        deliver_goal = bool(not is_closing_prompt and not is_greeting and (not self._goal_delivered and not closing_check))

        if is_closing_prompt:
            user_prompt = (
                f"Call objective: {session.objective}\n"
                f"Workspace default language: {session.language}\n"
                f"Customer language hint: {customer_language_hint}\n"
                f"Country: {session.country}\n\n"
                f"{context_block}\n\n"
                "Conversation so far:\n"
                f"{history_lines}\n\n"
                "The customer has been silent for a while.\n"
                "Ask a short closing question: 'Anything else before I let you go?'\n"
                "Do NOT restate the objective and do NOT hang up yet.\n"
            )
        elif is_greeting:
            name_note = f"Recipient name (optional to mention): {recipient_name}\n" if recipient_name else ""
            user_prompt = (
                f"Call objective: {session.objective}\n"
                f"Workspace default language: {session.language}\n"
                f"Customer language hint: {customer_language_hint}\n"
                f"Country: {session.country}\n"
                f"{name_note}\n"
                f"{context_block}\n\n"
                "No customer speech yet.\n"
                "Greet briefly and naturally. You may mention the recipient name if available, but do not ask for confirmation.\n"
                "Do NOT deliver the objective yet — wait for the customer's first reply before explaining the reason for the call.\n"
                "Ask a short, natural opener to elicit a first response (avoid sounding like an IVR).\n"
            )
        else:
            closing_instructions = ""
            if closing_check:
                closing_instructions = (
                    "You previously asked: 'Anything else before I let you go?'\n"
                    "If the customer indicates 'no' or that they are done, say goodbye and include a hangup action.\n"
                    "If the customer has another request/question, continue naturally and do NOT hang up.\n\n"
                )
            interrupted_note = ""
            if not closing_check and self._interrupted_agent_remaining and self._interrupted_agent_remaining_at:
                if (time.monotonic() - self._interrupted_agent_remaining_at) <= 120.0:
                    clip = _clip_text(self._interrupted_agent_remaining, 600)
                    if clip:
                        interrupted_note = (
                            "You were interrupted earlier and may not have finished saying this (not yet delivered):\n"
                            f"{clip}\n\n"
                            "First respond to the customer's latest message. Then, if it feels natural to continue the interrupted "
                            "information, continue briefly. If it does NOT feel appropriate, do not continue it.\n\n"
                        )
            goal_instruction = ""
            if deliver_goal:
                goal_instruction = (
                    "The objective has not been delivered yet. Deliver it once, briefly, as part of your response.\n\n"
                )

            user_prompt = (
                f"Call objective: {session.objective}\n"
                f"Workspace default language: {session.language}\n"
                f"Customer language hint: {customer_language_hint}\n"
                f"Country: {session.country}\n\n"
                f"{context_block}\n\n"
                "Conversation so far:\n"
                f"{history_lines}\n\n"
                f"Customer just said: {customer_text}\n\n"
                f"{closing_instructions}"
                f"{interrupted_note}"
                f"{goal_instruction}"
                "Rules:\n"
                "- Be natural, concise, and helpful. Avoid sounding like an IVR.\n"
                "- Do NOT ask the customer to confirm they've received information unless they asked you to repeat/clarify.\n"
                "- If the customer asks something not supported by the provided context, say you will follow up.\n"
            )
        bundle = PromptBundle(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            transcript=[],
            knowledge_snippets=[],
            actions_catalog=[],
        )

        await self._log_event(
            "call.agent_speech.started",
            {
                "response_id": response_id,
                "is_greeting": bool(is_greeting),
                "is_closing_prompt": bool(is_closing_prompt),
                "closing_check": bool(closing_check),
                "deliver_goal": bool(deliver_goal),
            },
        )

        loop = asyncio.get_running_loop()
        delta_queue: asyncio.Queue[str] = asyncio.Queue()
        response_text_parts: list[str] = []
        llm_result: dict | None = None
        streaming_to_tts = True

        def _emit_text(delta: str) -> None:
            nonlocal streaming_to_tts
            if not delta:
                return
            response_text_parts.append(delta)
            if streaming_to_tts:
                loop.call_soon_threadsafe(delta_queue.put_nowait, delta)

        def _build_stream_callback() -> Callable[[str], None]:
            if isinstance(provider, DeepSeekChatProvider):
                return _emit_text
            if isinstance(provider, OpenAIChatProvider):
                extractor = _ResponseTextExtractor(_emit_text)

                def _on_delta(json_delta: str) -> None:
                    extractor.feed(json_delta)

                return _on_delta
            return _emit_text

        stream_cb = _build_stream_callback()

        async def _llm_thread() -> None:
            nonlocal llm_result
            try:
                llm_result = await asyncio.to_thread(provider.generate, bundle, on_stream_delta=stream_cb)
            except Exception as exc:
                await self._log_event("llm.error", {"error": str(exc)})
            finally:
                loop.call_soon_threadsafe(delta_queue.put_nowait, "")

        llm_task = asyncio.create_task(_llm_thread())

        # --- Resolve TTS configs ---
        tts_prov = _tts_provider()
        try:
            tts_config_en = await self._get_tts_config(language="en")
            tts_config_ar = await self._get_tts_config(language="ar")
        except Exception as exc:
            await self._log_event("tts.disabled", {"error": str(exc)})
            await llm_task
            if self._speaking_response_id == response_id:
                self._speaking_response_id = 0
            return

        # --- Try WebSocket TTS (parallel streaming) ---
        ws_session: ElevenLabsWSSession | DeepgramTTSWSSession | None = None
        ws_output_format: str = tts_config_en.output_format
        use_ws_tts = True  # will flip to False on failure
        try:
            if tts_prov == "deepgram" and (customer_language_hint or "en") != "ar":
                dg_cfg = await self._get_deepgram_tts_config(language="en")
                ws_session = DeepgramTTSWSSession(dg_cfg)
                ws_output_format = f"mulaw_{dg_cfg.sample_rate}"
            else:
                ws_session = ElevenLabsWSSession(tts_config_en)
                ws_output_format = tts_config_en.output_format
            await ws_session.connect()
        except Exception as exc:
            await self._log_event("tts.ws.connect_failed", {"error": str(exc), "provider": tts_prov})
            ws_session = None
            use_ws_tts = False

        spoken_chunks: list[str] = []
        spoke_any = False

        if use_ws_tts and ws_session is not None:
            # --- WebSocket TTS path: concurrent text sender + audio receiver ---
            audio_receiver_task: asyncio.Task | None = None
            deepgram_pending_flush_chars = 0
            deepgram_last_flush_at = time.monotonic()
            try:
                audio_receiver_task = asyncio.create_task(
                    self._stream_ws_audio_to_twilio(twilio_ws, ws_session, output_format=ws_output_format)
                )

                # Text sender: reads LLM deltas, chunks, sends to WS
                buffer = ""
                first_chunk_sent = False
                first_chunk_min = 8  # lower threshold for first chunk (fast path)
                while True:
                    delta = await delta_queue.get()
                    if delta == "":
                        break
                    buffer += delta
                    # First-chunk fast path: use lower min_chars for first chunk
                    if not first_chunk_sent:
                        chunk, buffer = _maybe_extract_speakable_chunk_first(buffer, min_chars=first_chunk_min)
                    else:
                        chunk, buffer = _maybe_extract_speakable_chunk(buffer)
                    if chunk:
                        await self._log_event("tts.chunk", {"response_id": response_id, "text": chunk})
                        # For WS TTS, detect language: if Arabic and using Deepgram, we can't
                        # use the WS session — fall back to HTTP for this chunk.
                        chunk_lang = _detect_text_language(chunk, fallback=customer_language_hint)
                        if chunk_lang == "ar" and tts_prov == "deepgram":
                            # Arabic chunk on Deepgram: fall back to HTTP ElevenLabs for this chunk
                            tts_cfg = tts_config_ar
                            await self._stream_tts_to_twilio(twilio_ws, chunk, config=tts_cfg)
                        else:
                            if isinstance(ws_session, ElevenLabsWSSession):
                                await ws_session.send_text(chunk, flush=True)
                            else:
                                deepgram_pending_flush_chars, deepgram_last_flush_at = await _deepgram_ws_send(
                                    ws_session,
                                    chunk,
                                    pending_chars=deepgram_pending_flush_chars,
                                    last_flush_at=deepgram_last_flush_at,
                                    force_flush=False,
                                )
                        spoken_chunks.append(chunk)
                        await self._log_event("tts.chunk.done", {"response_id": response_id, "text": _clip_text(chunk, 160)})
                        spoke_any = True
                        first_chunk_sent = True

                # Flush remaining buffer
                final = buffer.strip()
                if final:
                    await self._log_event("tts.chunk.final", {"response_id": response_id, "text": final})
                    chunk_lang = _detect_text_language(final, fallback=customer_language_hint)
                    if chunk_lang == "ar" and tts_prov == "deepgram":
                        await self._stream_tts_to_twilio(twilio_ws, final, config=tts_config_ar)
                    else:
                        if isinstance(ws_session, ElevenLabsWSSession):
                            await ws_session.send_text(final, flush=True)
                        else:
                            deepgram_pending_flush_chars, deepgram_last_flush_at = await _deepgram_ws_send(
                                ws_session,
                                final,
                                pending_chars=deepgram_pending_flush_chars,
                                last_flush_at=deepgram_last_flush_at,
                                force_flush=True,
                            )
                    spoken_chunks.append(final)
                    spoke_any = True

                # Ensure any pending Deepgram text is emitted before closing the session.
                if isinstance(ws_session, DeepgramTTSWSSession) and deepgram_pending_flush_chars > 0:
                    await ws_session.flush()

                # Signal end-of-stream and wait for audio to finish
                await ws_session.close()
                if audio_receiver_task:
                    await audio_receiver_task

                if spoke_any:
                    self._last_agent_speech_end_at = time.monotonic()
                if deliver_goal and spoke_any and not self._goal_delivered:
                    self._goal_delivered = True
                    await self._log_event("goal.delivered", {"objective": session.objective})
            except asyncio.CancelledError:
                streaming_to_tts = False
                self._last_agent_speech_end_at = time.monotonic()
                # Close WS immediately on barge-in
                try:
                    await ws_session.close()
                except Exception:
                    pass
                if audio_receiver_task and not audio_receiver_task.done():
                    audio_receiver_task.cancel()
                spoken_text = " ".join(spoken_chunks).strip()
                if spoken_text:
                    self._history.append(("agent", spoken_text))
                    self._history = self._history[-(self.runtime_config.max_history_turns * 2) :]
                await self._log_event(
                    "call.agent_speech.cancelled",
                    {
                        "response_id": response_id,
                        "spoken_chunks": len(spoken_chunks),
                        "barge_in_text": self._last_barge_in_text,
                    },
                )
                asyncio.create_task(
                    self._finalize_interrupted_llm_response(
                        response_id=response_id,
                        llm_task=llm_task,
                        response_text_parts=response_text_parts,
                        spoken_chunk_count=len(spoken_chunks),
                        deliver_goal=deliver_goal,
                    )
                )
                if self._speaking_response_id == response_id:
                    self._speaking_response_id = 0
                return
            except Exception as exc:
                await self._log_event("tts.ws.error", {"error": str(exc), "provider": tts_prov})
                # Close WS session on error
                try:
                    await ws_session.close()
                except Exception:
                    pass
                if audio_receiver_task and not audio_receiver_task.done():
                    audio_receiver_task.cancel()
                # Fall through to HTTP fallback below
                use_ws_tts = False
            finally:
                if self._speaking_response_id == response_id and use_ws_tts:
                    self._speaking_response_id = 0

        if not use_ws_tts:
            # --- HTTP TTS fallback path (original sequential approach) ---
            buffer = ""
            spoken_chunks = []
            spoke_any = False
            try:
                while True:
                    delta = await delta_queue.get()
                    if delta == "":
                        break
                    buffer += delta
                    chunk, buffer = _maybe_extract_speakable_chunk(buffer)
                    if chunk:
                        await self._log_event("tts.chunk", {"response_id": response_id, "text": chunk})
                        tts_lang = _detect_text_language(chunk, fallback=customer_language_hint)
                        if tts_prov == "deepgram" and tts_lang != "ar":
                            dg_cfg = await self._get_deepgram_tts_config(language=tts_lang)
                            await self._stream_tts_to_twilio(twilio_ws, chunk, deepgram_config=dg_cfg)
                        else:
                            tts_cfg = tts_config_ar if tts_lang == "ar" else tts_config_en
                            await self._stream_tts_to_twilio(twilio_ws, chunk, config=tts_cfg)
                        spoken_chunks.append(chunk)
                        await self._log_event("tts.chunk.done", {"response_id": response_id, "text": _clip_text(chunk, 160)})
                        spoke_any = True

                final = buffer.strip()
                if final:
                    await self._log_event("tts.chunk.final", {"response_id": response_id, "text": final})
                    tts_lang = _detect_text_language(final, fallback=customer_language_hint)
                    if tts_prov == "deepgram" and tts_lang != "ar":
                        dg_cfg = await self._get_deepgram_tts_config(language=tts_lang)
                        await self._stream_tts_to_twilio(twilio_ws, final, deepgram_config=dg_cfg)
                    else:
                        tts_cfg = tts_config_ar if tts_lang == "ar" else tts_config_en
                        await self._stream_tts_to_twilio(twilio_ws, final, config=tts_cfg)
                    spoken_chunks.append(final)
                    spoke_any = True
                if spoke_any:
                    self._last_agent_speech_end_at = time.monotonic()
                if deliver_goal and spoke_any and not self._goal_delivered:
                    self._goal_delivered = True
                    await self._log_event("goal.delivered", {"objective": session.objective})
            except asyncio.CancelledError:
                streaming_to_tts = False
                self._last_agent_speech_end_at = time.monotonic()
                spoken_text = " ".join(spoken_chunks).strip()
                if spoken_text:
                    self._history.append(("agent", spoken_text))
                    self._history = self._history[-(self.runtime_config.max_history_turns * 2) :]
                await self._log_event(
                    "call.agent_speech.cancelled",
                    {
                        "response_id": response_id,
                        "spoken_chunks": len(spoken_chunks),
                        "barge_in_text": self._last_barge_in_text,
                    },
                )
                asyncio.create_task(
                    self._finalize_interrupted_llm_response(
                        response_id=response_id,
                        llm_task=llm_task,
                        response_text_parts=response_text_parts,
                        spoken_chunk_count=len(spoken_chunks),
                        deliver_goal=deliver_goal,
                    )
                )
                if self._speaking_response_id == response_id:
                    self._speaking_response_id = 0
                return
            except Exception as exc:
                await self._log_event("tts.error", {"error": str(exc)})
            finally:
                if self._speaking_response_id == response_id:
                    self._speaking_response_id = 0

        await llm_task
        full_text = ""
        if isinstance(llm_result, dict):
            full_text = str(llm_result.get("response_text") or "").strip()
            usage = llm_result.get("llm_usage")
            if full_text:
                await self._log_event("llm.response.final", {"text": full_text, "llm_usage": usage or {}, "response_id": response_id})
            if not full_text:
                full_text = "".join(response_text_parts).strip()
            spoken_text = " ".join(spoken_chunks).strip() if spoken_chunks else ""
            if spoken_text:
                self._history.append(("agent", spoken_text))
                self._history = self._history[-(self.runtime_config.max_history_turns * 2) :]
            elif full_text:
                self._history.append(("agent", full_text))
                self._history = self._history[-(self.runtime_config.max_history_turns * 2) :]
            actions = llm_result.get("actions")
            if _has_hangup_action(actions):
                await self._log_event("call.hangup.action", {"actions": actions})
                await self._hangup_call(reason="llm_action")
