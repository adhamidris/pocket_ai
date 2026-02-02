# Portal LLM Conversation Flow (Django Backend)

This document walks through a **single chat turn** in the SaaS portal — from the browser sending a message, through Django, into the MCP orchestrator, and back out as streaming events.

**Current status:** MCP runs in **agentic mode** by default. The LLM tool surface is limited to `search_knowledge → read_knowledge` plus a small set of workflow tools (email, voice, gateway, background runs). Table/dataset tools are **not** LLM‑facing in agentic mode.

---

## Scope

This doc covers only the **public chat portal → LLM → knowledge tools** workflow:

- Session bootstrap for the portal widget.
- A single **message send** using `POST /api/chat/stream/send/`.
- The **MCP orchestrator** path with `search_knowledge` + `read_knowledge`.
- How streaming, planning, actions, and extractions are stitched together and persisted.

Out of scope:

- Business onboarding flows.
- Upload/ingestion pipelines.
- Admin dashboards or internal APIs.

---

## Key Actors & Modules

- **Browser / Widget**
  - JS client that calls the API and consumes SSE events.
  - `pocketai_django/frontend/static/js/chat-portal.js`

- **Django API Surface**
  - URL routing: `pocketai_django/apps/api/urls.py`
  - Portal endpoints: `pocketai_django/apps/api/chat_portal.py`

- **Portal Orchestration**
  - `ChatPortalService` in `pocketai_django/apps/conversations/portal.py`

- **LLM Orchestrator**
  - `McpOrchestratorService` in `pocketai_django/apps/mcp/orchestrator.py`

- **LLM Providers**
  - `pocketai_django/apps/llm/llm_provider.py`

- **RAG / Knowledge Layer**
  - Search + read logic: `KnowledgeSearchService` in `pocketai_django/apps/rag/ai_orchestrator.py`
  - MCP tools wrapping that service: `pocketai_django/apps/mcp/tools.py`

- **Planner & Actions**
  - Planner JSON pass + ActionDispatcher in `apps/rag/ai_orchestrator.py`

---

## End‑to‑End Turn Timeline (One Message)

1. **Session bootstrap**  
   Widget resolves business/agent handle and calls `bootstrap_session`.

2. **Message send**  
   Widget sends `POST /api/chat/stream/send/` with `session_token`, `body`, and optional metadata.

3. **MCP tool loop**  
   - `search_knowledge` returns refs (IDs only).
   - `read_knowledge` fetches canonical evidence for the selected refs.

4. **Streaming response**  
   Tool outputs are injected into the prompt, and the answer is streamed via SSE deltas.

5. **Planner pass**  
   A non‑streaming LLM call (`run_planner_only`) returns actions/extractions JSON.

6. **Persistence**  
   Final answer and metadata are stored; action execution runs async.

---

## Sequence Overview

```text
Browser
  ↓ POST /api/chat/stream/send (session_token, body)
Django: apps/api/chat_portal.stream_send
  ↓ ChatPortalService.append_message (persist customer message)
  ↓ Decide orchestrator (MCP)
  ↓ McpOrchestratorService.stream_turn(...)
       - search_knowledge → read_knowledge
       - Streams answer deltas via on_response_text_delta
  ↓ finalize_stream_context(...)
       - run_planner_only → JSON actions/extractions
       - Persist assistant message + metadata
  ↓ SSE writer (event_stream)
       - block_start/block_delta/block_end + turnPersisted + turnUpdated
Browser renders streaming answer
```

---

## Scenario A — Simple search → answer

**Use case:** The answer is contained in the first read.

1. `search_knowledge` returns refs for matching content.
2. `read_knowledge` returns content for those refs.
3. The LLM answers directly from evidence.

---

## Scenario B — Deep read with cursor continuation

**Use case:** The first read is partial or large.

1. `read_knowledge` returns `partial` with `cursor` for one or more refs.
2. The model calls `read_knowledge` again with `{id, cursor}` to continue.
3. The final answer is assembled after the continuation.

---

## Scenario C — Tables / datasets (legacy only)

In agentic mode, the LLM does **not** see `list_tables`, `query_dataset`, or `table_aggregate`.  
Those tools remain implemented for **non‑agentic/legacy** workflows and internal testing.

---

## Planner & Post‑actions

After streaming, `run_planner_only` produces a JSON payload:

```json
{
  "response_text": "",
  "actions": [{ "action": "create_case", "payload": { "...": "..." } }],
  "extractions": [{ "type": "CUSTOMER", "payload": { "...": "..." } }]
}
```

Actions are executed asynchronously by `ActionDispatcher`.
