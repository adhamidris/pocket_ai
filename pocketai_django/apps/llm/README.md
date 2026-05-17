LLM App (apps/llm)
=================

Purpose
-------
This app owns LLM provider integrations and prompt construction. It defines:
- How prompts are assembled (PromptBuilder + PromptBundle).
- How chat-completions are called (OpenAI/DeepSeek providers).
- Shared logging, token estimation, and provider selection logic.

Directory Map
-------------
- llm_provider.py
  Provider clients + MCP tool-calling providers + selection helpers.
- ai_prompt_builder.py
  PromptBundle + PromptBuilder (legacy orchestrator prompt composition).
- apps.py
  Django app config.

Key Flows
---------
1) MCP provider (tool-calling)
   MCP orchestrator calls load_mcp_provider() -> BaseMcpProvider.chat(...)
   -> assistant response with tool_calls metadata.

2) Legacy provider (prompt bundle)
   AiOrchestratorService builds PromptBundle -> BaseLLMProvider.generate(...)
   -> JSON response_text/actions/extractions payload.

Prompt Bundle (Legacy)
----------------------
PromptBuilder returns a PromptBundle containing:
- system_prompt (policy + tone + rules)
- user_prompt (current request + knowledge ledger)
- transcript (recent messages)
- knowledge_snippets
- actions_catalog

Configuration Touchpoints
-------------------------
Provider selection:
- MCP_PROVIDER (preferred tool provider: "openai" or "deepseek")
- LLM_PROVIDER (preferred legacy provider)

OpenAI:
- OPENAI_API_KEY
- OPENAI_MODEL
- OPENAI_BASE_URL
- OPENAI_TOOLS_RESPONSE_FORMAT (for tool calls)

DeepSeek:
- DEEPSEEK_API_KEY
- DEEPSEEK_MODEL
- DEEPSEEK_BASE_URL

Debug + logging:
- LLM_LOG_TOKEN_ESTIMATE (logs token estimates at DEBUG)
- LLM_DEBUG_PAYLOADS (logs full payloads at DEBUG)
- LLM_HTTP_TIMEOUT_CONNECT / LLM_HTTP_TIMEOUT_READ

Quick Start (Dev)
----------------
- Pick a provider in `.env`:
  MCP_PROVIDER=deepseek
  DEEPSEEK_API_KEY=...
- Legacy prompt test (non-MCP):
  Set `LLM_PROVIDER` and call through legacy orchestration.

Provider Selection Order
------------------------
- MCP:
  - If MCP_PROVIDER=deepseek -> DeepSeekToolsProvider, fallback OpenAIToolsProvider
  - If MCP_PROVIDER=openai -> OpenAIToolsProvider, fallback DeepSeekToolsProvider
  - If unset -> OpenAI if key present, else DeepSeek
- Legacy:
  - If LLM_PROVIDER=deepseek -> DeepSeekChatProvider, fallback OpenAIChatProvider
  - If LLM_PROVIDER=openai -> OpenAIChatProvider, fallback DeepSeekChatProvider
  - If unset -> OpenAI if key present, else DeepSeek

Examples
--------
Legacy (PromptBundle -> generate):
```python
bundle = PromptBuilder(agent).build(
    conversation=conversation,
    knowledge_snippets=snippets,
    transcript=messages,
    actions_catalog=actions,
    knowledge_log=knowledge_log,
)
provider = load_default_provider()
response = provider.generate(bundle)
```

MCP (tool-calling -> chat):
```python
provider = load_mcp_provider()
payload = provider.chat(messages, tools=TOOL_DEFINITIONS, on_stream_delta=...)
```

ASCII Flow
----------
PromptBuilder (apps/llm)
   ↓
Provider (OpenAI/DeepSeek)
   ↓
LLM response (JSON for legacy, tool_calls for MCP)

Troubleshooting
---------------
- Provider not loading:
  - Check API key env vars and provider names.
  - Look for `llm.provider.disabled` in `var/logs/rag.log`.
- Missing token logs:
  - Set `LLM_LOG_TOKEN_ESTIMATE=true` and run with DEBUG logging.
- Tool call failures:
  - Confirm MCP provider is loaded (load_mcp_provider) and tools are advertised.

Observability
-------------
- `llm.trace` entries in `var/logs/rag.log`
- `llm.usage` logs include prompt/completion/total tokens when provider returns them.

Common Env Presets
------------------
OpenAI (MCP + legacy):
```
MCP_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
OPENAI_TOOLS_RESPONSE_FORMAT=json_object
LLM_PROVIDER=openai
```

DeepSeek (MCP + legacy):
```
MCP_PROVIDER=deepseek
DEEPSEEK_API_KEY=...
DEEPSEEK_MODEL=deepseek-chat
LLM_PROVIDER=deepseek
```

Token Budget Notes
------------------
- MCP tools are budgeted in the orchestrator, not in this app.
- This layer only reports token usage when providers return it.
- Use `LLM_LOG_TOKEN_ESTIMATE=true` for approximate sizing before sending.

Related Docs
------------
- `docs/architecture/llm_conversation_backend_flow.md`
- `docs/rag/rag_rollout_ops.md`

Glossary (Quick)
----------------
- PromptBundle: structured prompt input for legacy LLM calls.
- MCP provider: tool-calling provider that returns tool_calls + content.

Where To Start (Reading Order)
------------------------------
1) `apps/llm/llm_provider.py` — provider selection + HTTP calls.
2) `apps/llm/ai_prompt_builder.py` — prompt composition rules.
3) `apps/mcp/orchestrator.py` — how MCP uses the provider.

High-Level Architecture
-----------------------
Knowledge (ingest/store)
   ↓
RAG (retrieve/score)
   ↓
MCP (tool loop + guards)
   ↓
LLM (providers + prompt bundles)
