"""OpenAI provider adapter for the LlmOrchestrator.

This module performs *real* API calls to OpenAI when provided an API key.
It streams assistant deltas to the caller, parses the final structured payload,
and persists the AGENT message using the base orchestrator's `on_final`.

Usage (example):

    from app.services.ai_runtime_facade import AgentTurnRuntimeService
    from app.services.providers.openai_orchestrator import OpenAIOrchestrator

    service = AgentTurnRuntimeService(session)
    prep = service.prepare(business_id=..., agent_id=..., conversation_id=...)
    orch = OpenAIOrchestrator(session, model="gpt-4o-mini")
    for evt in orch.start_turn_with_runtime(prep.start_ctx, prep.runtime, user_text="Hi!"):
        if evt["type"] == "delta":
            stream_to_ui(evt["data"])
        elif evt["type"] == "final":
            print("Done")

Notes:
- Requires `OPENAI_API_KEY` (or pass api_key=...).
- Uses OpenAI Chat Completions API with `response_format={"type":"json_object"}` as a broadly
  compatible way to enforce JSON output. The system prompt instructs the model to emit a single
  JSON object matching the `AiMessagePayload` schema. The OutputParser will validate it.
- If the SDK is unavailable, the adapter raises a clear ImportError.

This file doesn't modify interfaces in `llm_orchestrator.py`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, Literal, TypedDict
from fastapi import BackgroundTasks

from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models.conversations import ConversationMessage

from app.schemas.ai_runtime import AiMessagePayload
from app.services.llm_orchestrator import (
    LlmOrchestrator,
    StartTurnContext,
)
from app.services.ai_prompt_service import (
    PreparedAgentRuntime,
    OutputParser,
)


class OpenAIOrchestrator(LlmOrchestrator):
    """Concrete orchestrator that calls OpenAI's Chat Completions API (streaming)."""

    def __init__(
        self,
        session: Session,
        *,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
        request_timeout: float | None = 60.0,
        background_tasks: BackgroundTasks | None = None,
    ) -> None:
        super().__init__(session)
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or ""
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL") or None
        self.request_timeout = request_timeout
        self._background_tasks = background_tasks
        if not self.api_key:
            raise RuntimeError(
                "OpenAIOrchestrator requires an API key. "
                "Set OPENAI_API_KEY or pass api_key=..."
            )

        try:
            # New SDK: `openai` (>=1.0)
            import openai  # type: ignore
            self._openai = openai
            self._client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.request_timeout)
            self._mode = "new"
        except Exception:
            try:
                # Legacy SDK: openai.ChatCompletion
                import openai as legacy_openai  # type: ignore
                self._openai = legacy_openai
                self._client = legacy_openai
                self._mode = "legacy"
                legacy_openai.api_key = self.api_key
                if self.base_url:
                    legacy_openai.base_url = self.base_url
            except Exception as exc:  # pragma: no cover
                raise ImportError(
                    "OpenAI SDK not installed. Run `pip install openai`."
                ) from exc

        self._parser = OutputParser()

    def start_turn_with_runtime(
        self,
        ctx: StartTurnContext,
        runtime: PreparedAgentRuntime,
        *,
        user_text: str,
        stream: bool = True,
    ) -> Iterable[dict]:
        """Start a turn using prepared runtime (prompt, tools, config, plan).

        Yields events:
          - {"type":"delta","data": <str>} for streaming text
          - {"type":"final","data": {"text": <str>, "payload": <dict>}}
          - {"type":"error","data": <str>} on failure

        On completion, calls `on_final(...)` to persist the AGENT message.
        """
        system_prompt = self._compose_system_prompt(runtime)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ]

        temperature = _coerce_float(runtime.model_config_json.get("temperature"), 0.3)
        top_p = _coerce_float(runtime.model_config_json.get("top_p"), 0.9)

        # Enforce JSON object output – schema is validated post-hoc by OutputParser.
        response_format = {"type": "json_object"}

        try:
            if self._mode == "new":
                if stream:
                    yield from self._stream_new_sdk(ctx, messages, temperature, top_p, response_format)
                else:
                    delta_text, json_payload = self._nonstream_new_sdk(messages, temperature, top_p, response_format)
                    yield {"type": "final", "data": {"text": delta_text, "payload": json_payload}}
                    self._finalize(ctx, delta_text, json_payload)
            else:
                if stream:
                    yield from self._stream_legacy_sdk(ctx, messages, temperature, top_p, response_format)
                else:
                    delta_text, json_payload = self._nonstream_legacy_sdk(messages, temperature, top_p, response_format)
                    yield {"type": "final", "data": {"text": delta_text, "payload": json_payload}}
                    self._finalize(ctx, delta_text, json_payload)
        except Exception as exc:
            yield {"type": "error", "data": str(exc)}

    # ---------- SDK helpers ----------

    def _stream_new_sdk(self, ctx: StartTurnContext, messages, temperature, top_p, response_format) -> Iterator[dict]:
        """Streaming using OpenAI SDK v1+ (chat.completions)."""
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            response_format=response_format,  # enforce JSON object
            stream=True,
        )

        assembled = []
        for chunk in stream:
            choice = getattr(chunk, "choices", [None])[0]
            if not choice:
                continue
            delta = getattr(choice, "delta", None)
            if delta and getattr(delta, "content", None):
                piece = delta.content
                assembled.append(piece)
                yield {"type": "delta", "data": piece}

        final_text = "".join(assembled).strip()
        json_payload = _safe_extract_json(final_text)
        yield {"type": "final", "data": {"text": final_text, "payload": json_payload}}
        self._finalize(ctx, final_text, json_payload)  # will be replaced by caller in outer method

    def _nonstream_new_sdk(self, messages, temperature, top_p, response_format) -> tuple[str, dict]:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            response_format=response_format,
            stream=False,
        )
        content = resp.choices[0].message.content or ""
        return content, _safe_extract_json(content)

    def _stream_legacy_sdk(self, ctx: StartTurnContext, messages, temperature, top_p, response_format) -> Iterator[dict]:
        """Streaming using legacy OpenAI SDK."""
        stream = self._client.ChatCompletion.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            response_format=response_format,
            stream=True,
        )
        assembled = []
        for chunk in stream:
            delta = chunk["choices"][0]["delta"]
            if "content" in delta and delta["content"]:
                piece = delta["content"]
                assembled.append(piece)
                yield {"type": "delta", "data": piece}

        final_text = "".join(assembled).strip()
        json_payload = _safe_extract_json(final_text)
        yield {"type": "final", "data": {"text": final_text, "payload": json_payload}}
        self._finalize(ctx, final_text, json_payload)  # replaced by caller in outer method

    def _nonstream_legacy_sdk(self, messages, temperature, top_p, response_format) -> tuple[str, dict]:
        resp = self._client.ChatCompletion.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            response_format=response_format,
            stream=False,
        )
        content = resp["choices"][0]["message"]["content"] or ""
        return content, _safe_extract_json(content)

    # ---------- internal ----------

    def _compose_system_prompt(self, runtime: PreparedAgentRuntime) -> str:
        # Provide minimal, enforce-json footer to the compiled prompt.
        footer = (
            "\n\nReturn ONE valid JSON object only (no markdown fences). "
            "The JSON must match the provided schema. Do not include extra keys."
        )
        return (runtime.prompt_template or "").rstrip() + footer

    def _finalize(self, ctx: StartTurnContext, final_text: str, json_payload: dict) -> None:
        # Parse & validate payload; persist via base orchestrator.
        payload_model = self._parser.parse_payload(json_payload)
        self.on_final(ctx=ctx, text=final_text, payload=payload_model)
        # After persisting the AGENT message, apply payload side-effects (customer/case/escalation).
        try:
            # Best-effort: fetch the latest message id in this conversation (the one we just added).
            msg_id = self.session.execute(
                select(ConversationMessage.id)
                .where(ConversationMessage.conversation_id == ctx.conversation_id)
                .order_by(ConversationMessage.sent_at.desc(), ConversationMessage.id.desc())
                .limit(1)
            ).scalar_one_or_none()
        except Exception:
            msg_id = None
        # Use background task for non-blocking payload processing
        from app.services.background_tasks import BackgroundTaskRunner
        runner = BackgroundTaskRunner(background_tasks=self._background_tasks)
        runner.dispatch_payload_processing(
            business_id=ctx.business_id,
            conversation_id=ctx.conversation_id,
            message_id=(msg_id or ctx.conversation_id),
            payload=payload_model,
            agent_id=ctx.agent_id,
        )


# ---------- utilities ----------

def _safe_extract_json(text: str) -> dict:
    """Extract a JSON object from the model output. If it's raw JSON, parse directly.
    If it's mixed text, try to find the first '{'..last '}' span."""
    stripped = text.strip()
    if not stripped:
        return {}
    # Direct JSON?
    try:
        return json.loads(stripped)
    except Exception:
        pass
    # Try span extraction
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(stripped[start : end + 1])
        except Exception:
            return {}
    return {}


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


__all__ = ["OpenAIOrchestrator"]
