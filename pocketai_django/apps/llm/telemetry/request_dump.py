from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


_DEFAULT_DUMP_DIR = Path("var/tmp/llm-request-dumps")


def _resolve_dump_dir() -> Path | None:
    raw = (os.getenv("MCP_LLM_REQUEST_DUMP_DIR") or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return _DEFAULT_DUMP_DIR
    return Path(raw)


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _safe_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


@dataclass(frozen=True, slots=True)
class McpLlmRequestDumpMeta:
    provider: str
    model: str | None
    stage: str
    conversation_id: str
    business_id: str | None
    streaming: bool
    message_count: int
    tool_count: int


def _build_openai_tools_payload(
    provider: object,
    *,
    messages: Iterable[Mapping[str, object]],
    tools: Iterable[Mapping[str, object]] | None,
    streaming: bool,
) -> dict[str, Any]:
    from apps.llm.providers.chat import OpenAIChatProvider

    model = str(getattr(provider, "model", "") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini")
    temperature = float(getattr(provider, "temperature", 0.3))
    top_p = float(getattr(provider, "top_p", 0.9))

    payload: dict[str, Any] = {
        "model": model,
        "messages": [dict(msg) for msg in messages],
        "stream": streaming,
    }
    if streaming:
        payload["stream_options"] = {"include_usage": True}

    model_lower = model.lower()
    skip_sampling_params = any(model_lower.startswith(prefix) for prefix in ("o1", "o3", "gpt-5"))
    if not skip_sampling_params:
        payload["temperature"] = temperature
        payload["top_p"] = top_p
    if not streaming:
        payload["response_format"] = OpenAIChatProvider._response_schema()
    if tools:
        payload["tools"] = list(tools)
        payload["tool_choice"] = "auto"

    max_tokens_env = os.getenv("OPENAI_MAX_TOKENS")
    if max_tokens_env:
        try:
            payload["max_tokens"] = max(1, int(max_tokens_env))
        except (TypeError, ValueError):
            pass

    return payload


def _build_deepseek_tools_payload(
    provider: object,
    *,
    messages: Iterable[Mapping[str, object]],
    tools: Iterable[Mapping[str, object]] | None,
    streaming: bool,
) -> dict[str, Any]:
    model = str(getattr(provider, "model", "") or os.getenv("DEEPSEEK_MODEL") or "deepseek-chat")
    temperature = float(getattr(provider, "temperature", 0.3))
    top_p = float(getattr(provider, "top_p", 0.9))

    payload: dict[str, Any] = {
        "model": model,
        "messages": [dict(msg) for msg in messages],
        "temperature": temperature,
        "top_p": top_p,
        "stream": streaming,
    }
    if streaming:
        payload["stream_options"] = {"include_usage": True}
    if tools:
        payload["tools"] = list(tools)
        payload["tool_choice"] = "auto"
    return payload


def _build_provider_payload(
    *,
    provider: object,
    messages: Iterable[Mapping[str, object]],
    tools: Iterable[Mapping[str, object]] | None,
    streaming: bool,
) -> tuple[str, str | None, dict[str, Any]]:
    provider_name = provider.__class__.__name__
    model_name = getattr(provider, "model", None)
    model = str(model_name) if isinstance(model_name, str) and model_name else None

    if provider_name == "OpenAIToolsProvider":
        payload = _build_openai_tools_payload(provider, messages=messages, tools=tools, streaming=streaming)
        return provider_name, payload.get("model"), payload
    if provider_name == "DeepSeekToolsProvider":
        payload = _build_deepseek_tools_payload(provider, messages=messages, tools=tools, streaming=streaming)
        return provider_name, payload.get("model"), payload

    # Unknown provider: fall back to the common OpenAI-compatible envelope.
    payload_fallback: dict[str, Any] = {
        "model": model or "unknown",
        "messages": [dict(msg) for msg in messages],
        "stream": streaming,
    }
    if streaming:
        payload_fallback["stream_options"] = {"include_usage": True}
    if tools:
        payload_fallback["tools"] = list(tools)
        payload_fallback["tool_choice"] = "auto"
    return provider_name, payload_fallback.get("model"), payload_fallback


def maybe_dump_mcp_llm_request(
    *,
    stage: str,
    conversation_id: object,
    business_id: object | None,
    provider: object,
    messages: Iterable[Mapping[str, object]],
    tools: Iterable[Mapping[str, object]] | None,
    streaming: bool,
    bundle_path: Path | None = None,
) -> Path | None:
    """
    If MCP_LLM_REQUEST_DUMP_DIR is set, write a markdown file containing the exact
    Chat Completions payload we send to the provider for this stage.
    """

    dump_dir = _resolve_dump_dir()
    if dump_dir is None:
        return None

    try:
        dump_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None

    message_list = [dict(msg) for msg in messages]
    tool_list = list(tools) if tools is not None else None

    provider_label, model, payload = _build_provider_payload(
        provider=provider,
        messages=message_list,
        tools=tool_list,
        streaming=streaming,
    )

    meta = McpLlmRequestDumpMeta(
        provider=provider_label,
        model=model,
        stage=str(stage or "").strip() or "unknown",
        conversation_id=str(conversation_id),
        business_id=str(business_id) if business_id else None,
        streaming=bool(streaming),
        message_count=len(message_list),
        tool_count=len(tool_list) if tool_list is not None else 0,
    )

    path = bundle_path
    if path is None:
        filename = f"mcp-llm-round_{_utc_stamp()}_{meta.conversation_id}.md"
        path = dump_dir / filename

    try:
        is_new = not path.exists()
        mode = "a" if not is_new else "w"
        with path.open(mode, encoding="utf-8") as handle:
            if is_new:
                handle.write("# MCP LLM Round Dump\n\n")
                handle.write(
                    "This file is generated locally when `MCP_LLM_REQUEST_DUMP_DIR` is set. "
                    "It contains the *exact* Chat Completions payload(s) sent to the LLM, "
                    "including user/system content. Do not enable in production.\n\n"
                )
            handle.write(f"## Stage: `{meta.stage}`\n\n")
            handle.write("### Meta\n\n")
            handle.write(_safe_json(asdict(meta)) + "\n\n")
            handle.write("### Chat Completions Payload\n\n")
            handle.write("```json\n")
            handle.write(_safe_json(payload))
            handle.write("\n```\n")
    except Exception:
        return None

    return path
