Conversations App (apps/conversations)
=====================================

Purpose
-------
This app owns conversation persistence, chat portal session orchestration,
streamed portal turns, file artifacts, and frontend response block shaping.

Directory Map
-------------
- models.py
  Conversation, ConversationMessage, ConversationExtraction, feedback, and
  portal turn/file models.
- portal.py
  Compatibility/service surface for ChatPortalService.
- portal_*.py
  Portal service mixins and support modules. These are transitional root modules
  and should be grouped under a portal package in the next cleanup pass.
- portal_files.py
  Compatibility bridge for portal file helpers.
- portal_files_pkg/
  Portal file blocks, creation, storage, ingestion, signed-token helpers, and
  shared file errors.
- portal_turn_runner.py
  Portal turn execution worker and event builder.
- portal_turn_events.py
  Portal turn event persistence/Redis publishing.
- portal_turn_processing.py
  Queue-style processing service for pending portal turns.
- portal_turn/
  Internal portal turn builder mixins: blocks, text streaming, reasoning, tools,
  debug helpers, and event folding.
- rich_blocks/
  Incremental rich content block parsing/building.
- content_blocks.py
  Canonical ordered `content_blocks[]` schema (Phase 0: text blocks only).
- response_blocks.py
  Sanitizes model output into UI-friendly text/table blocks.
- tests/
  Portal + identifier tests.

Key Flows
---------
1) Session bootstrap
   ChatPortalService.bootstrap_session() -> returns agent, business, session,
   and existing messages for the authenticated chat workspace.

2) Message append
   ChatPortalService.append_message() -> writes ConversationMessage and updates
   conversation metadata.

3) Response blocks
   normalize_response_blocks() -> safe, bounded blocks for the frontend.

Configuration Touchpoints
-------------------------
- PORTAL_STREAM_STATE_MACHINE (streaming status updates)
- MCP_LONG_CHAT_MEMORY_ENABLED (summary + pinned identifiers)

Response Blocks Limits
----------------------
- MAX_TEXT_LINES=32
- MAX_TEXT_LENGTH=800
- MAX_TABLE_COLUMNS=12
- MAX_TABLE_ROWS=60

Quick Start (Dev)
----------------
- Create a session token:
  Conversation.objects.create(business_profile=..., agent_profile=...)
- Append a message:
  ChatPortalService().append_message(...)

Examples
--------
Bootstrap a session:
```python
service = ChatPortalService()
bootstrap = service.bootstrap_session(
    business_slug="aug-pharma",
    agent_slug="ahmed",
    existing_session_token=None,
    metadata={"utm_source": "demo"},
)
```

Append a customer message:
```python
service.append_message(
    session_token=bootstrap.session.session_token,
    sender=ConversationSender.CUSTOMER,
    body="Can you check invoice 9125779195?",
)
```

Response blocks normalization:
```python
blocks = normalize_response_blocks(payload.get("response_blocks"))
```

ASCII Flow
----------
Portal client
   ↓
ChatPortalService (portal.py)
   ↓
Conversation + Message models
   ↓
PortalTurnRunner + MCP/LLM response
   ↓
rich_blocks / response_blocks -> frontend

Troubleshooting
---------------
- Messages missing:
  - Verify session token and conversation status.
- Response blocks empty:
  - Validate block structure against response_blocks.py limits.

Observability
-------------
- Conversation state is in `Conversation.status` and `last_activity_at`.

Related Docs
------------
- `docs/architecture/llm_conversation_backend_flow.md`
- `docs/ops/manual_qa_playbook.md`

Glossary (Quick)
----------------
- Conversation: a single visitor session.
- Extraction: structured action payload (case/lead/appointment).
- Response blocks: structured UI blocks (text/table).

Where To Start (Reading Order)
------------------------------
1) `apps/conversations/portal.py`
2) `apps/conversations/models.py`
3) `apps/conversations/response_blocks.py`

High-Level Architecture
-----------------------
Conversations (session + messages)
   ↓
MCP + RAG
   ↓
LLM responses -> response blocks
