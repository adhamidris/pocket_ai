# Prompt Catalog (Current MCP · Agentic v2)

This doc is the **single reference** for active prompt surfaces. MCP is the only
orchestrator path used by the chat portal.

## Source of Truth

- **Agentic system prompts (v2)**: `apps/mcp/schemas/agentic_prompts.py`
  - Per‑model templates (OpenAI/DeepSeek) selected via `build_model_specific_prompt`.
  - Default fallback uses `AGENTIC_SYSTEM_PROMPT_V2`.
- **MCP wrapper prompts**: `apps/mcp/prompts.py`
  - System message assembly, planner prompt, and final answer prompt.
- **Legacy prompt builder** (non‑portal): `apps/llm/ai_prompt_builder.py`
  - Only relevant for legacy/non‑MCP flows (e.g., some voice/post‑call processing).

## Agentic v2 Read Contract (Enabled)

Agentic v2 is enabled when:
- `MCP_AGENTIC_READ_V2_ENABLED=true` (root `.env`)

The LLM-facing read tool is:
- `read_knowledge(refs=[{id,cursor?}...], max_chars=...)`

Legacy knobs (page/mode selection) are **not** part of the agentic v2 contract.

## Tool Set (LLM-facing, agentic mode)

Primary knowledge tools:
- `search_knowledge` — hybrid search, returns refs (IDs only)
- `read_knowledge` — reads canonical evidence by ref
- `search_conversation_files`, `read_conversation_file` — chat‑uploaded files

Workflow tools (agentic mode allowlist):
- `email_search`, `email_get_message`, `email_get_thread`, `email_create_draft`, `email_send_draft` (email connectors live)
- `initiate_phone_call` (voice dev‑only)
- Background runs: `create_agent_run`, `list_agent_runs`, `get_agent_run`, `continue_agent_run`
- Gateway tools: `mcp_search_tools`, `mcp_call_tool`

**Not LLM‑facing in agentic mode:**
- Any other knowledge/dataset tools beyond `search_knowledge` + `read_knowledge`
- CRM tools (`create_case`, `update_case_*`, `create_customer`, `create_lead`, `create_appointment`)  
  These are produced via the planner JSON pass, not tool calls.

## Prompt Selection (How MCP Chooses)

`apps/mcp/prompts.py::build_system_message`:
- If **rag_agentic_mode** feature flag is on **and** `MCP_AGENTIC_READ_V2_ENABLED=true`,
  MCP uses the **agentic v2 per‑model prompt**.
- Otherwise it falls back to the non‑agentic MCP system prompt.

`build_model_specific_prompt` logs which template was selected (by model id).

## Reading Order

1. `apps/mcp/schemas/agentic_prompts.py` — per‑model v2 prompts
2. `apps/mcp/prompts.py` — MCP system/planner/final answer prompts
3. `apps/mcp/tools.py` — tool schema details

## Archived Prompt Snapshots

Older prompt snapshots were removed from the repo to avoid confusion.
