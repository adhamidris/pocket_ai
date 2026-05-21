from __future__ import annotations

from typing import Mapping

from apps.conversations.models import Conversation

from ...types import ToolExecutionContext


def _request_user_input_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    prompt = str(arguments.get("prompt") or arguments.get("question") or "").strip()
    raw_questions = arguments.get("questions")
    questions: list[str] = []
    if isinstance(raw_questions, list):
        for item in raw_questions:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if text:
                questions.append(text)
    if not questions and prompt:
        questions = [prompt]
    if not questions:
        return {
            "tool": "request_user_input",
            "status": "error",
            "error_code": "validation_failed",
            "error": "missing_prompt",
            "hint": "Provide prompt or questions for request_user_input.",
        }

    schema = arguments.get("schema")
    schema_payload = dict(schema) if isinstance(schema, Mapping) else {}
    return {
        "tool": "request_user_input",
        "status": "needs_user",
        "questions": questions[:10],
        "schema": schema_payload,
        "hint": "Awaiting user input. Ask the user, then resume after they reply.",
    }
