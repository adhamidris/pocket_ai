from __future__ import annotations

from apps.mcp.prompting.agentic_prompts import (
    DEEPSEEK_CHAT_SYSTEM_PROMPT,
    DEEPSEEK_REASONER_SYSTEM_PROMPT,
    DEFAULT_MAX_SEARCH_QUERY_VARIANTS,
    OPENAI_CHAT_SYSTEM_PROMPT,
    OPENAI_REASONING_SYSTEM_PROMPT,
    READ_TOOL_DESCRIPTION,
    SEARCH_TOOL_DESCRIPTION,
    TONE_INSTRUCTIONS,
    _search_query_variant_limit,
    _search_query_variants_hint,
    _search_query_variants_workflow_phrase,
    _select_template,
    build_model_specific_prompt,
    get_tone_instruction,
)
