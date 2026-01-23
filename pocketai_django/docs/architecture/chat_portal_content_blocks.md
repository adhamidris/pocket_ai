# Chat Portal — Content Blocks (Phase 0)

This doc captures (1) the current streaming + persistence contract for the
public chat portal and (2) the canonical `content_blocks[]` message schema that
will replace segment-based tool chip reconstruction.

## 1) Current contract inventory

### Streaming endpoint

- Backend entrypoint: `apps/api/chat_portal.py` (`stream_send`)
- Transport: Server-Sent Events (SSE)

#### SSE events (common)

- `context_progress`
  - Payload: `{ "state": "...", "label": "...", "meta": {...} }`
- `status`
  - Payload: `{ "state": "...", "label": "...", "meta": {...} }`
- `spinnerStatus`
  - Payload: `{ "message_id": "<uuid>", "text": "...", "pending": true|false }`
- `block_start` / `block_delta` / `block_end`
  - Ordered streaming events for text-like blocks (`text`, `reasoning`, rich text).
- `block_tool_use` / `block_tool_result`
  - Ordered streaming events for tool lifecycle + results.
- `turnPersisted`
  - Fired exactly once after persistence; includes `content_blocks`.
- `turnUpdated`, `actionsComplete`, `actionsError`
  - Optional post-processing events (planner/actions).

### Persistence paths

- Messages are stored as `ConversationMessage` rows (`apps/conversations/models.py`).
- `ConversationMessage.content_blocks` is the deterministic transcript payload for assistant messages.
- Approvals are persisted as `ConversationToolApproval` rows (`apps/conversations/models.py`).
  - `POST portal_tool_approval` updates the approval state.

## 2) Canonical assistant message schema: `content_blocks[]`

Each assistant message stores an ordered array of content blocks on the message:

- Model field: `ConversationMessage.content_blocks` (JSON)
- API exposure: `messages[].content_blocks` in portal bootstrap + message list responses

### Block envelope (required)

Every entry in `content_blocks[]` is a dict with:

- `block_id` (string, stable)
- `type` (`"text" | "tool_use" | "tool_result"`)
  - `reasoning` blocks are supported as well (see below).
- `created_at` (ISO8601 string)
- `payload` (type-specific object)

### Example blocks

Text:
```json
{
  "block_id": "blk_3b8d…",
  "type": "text",
  "created_at": "2026-01-20T12:34:56.000000+00:00",
  "payload": { "text": "Hello **world**" }
}
```

Reasoning (streamed, collapsible UI panel):
```json
{
  "block_id": "blk_7f21…",
  "type": "reasoning",
  "created_at": "2026-01-20T12:34:57.000000+00:00",
  "payload": {
    "title": "Tool step 1",
    "stage": "tool_iteration",
    "collapsed": false,
    "code": "Thinking…\\n"
  }
}
```

Tool use (future streaming):
```json
{
  "block_id": "blk_1a2b…",
  "type": "tool_use",
  "created_at": "2026-01-20T12:34:58.000000+00:00",
  "payload": {
    "event_id": "evt_123",
    "tool_name": "search_knowledge",
    "input_preview": { "query": "pricing" },
    "approval_id": null
  }
}
```

Tool result (future streaming):
```json
{
  "block_id": "blk_9c0d…",
  "type": "tool_result",
  "created_at": "2026-01-20T12:35:01.000000+00:00",
  "payload": {
    "event_id": "evt_123",
    "status": "ok",
    "duration_ms": 240,
    "artifact_id": "artifact_abc",
    "output_preview": { "result": "3 matches" }
  }
}
```

### Phase 0 behavior (implemented)

- When the portal persists an AI message via `ChatPortalService.append_message`, it now ensures `content_blocks` contains a single canonical `"text"` block mirroring `body`.
- Tool events remain in `metadata["tool_events"]` until Phase 1 moves tool lifecycle + results into ordered blocks.
- Reasoning blocks stream as `block_start` + `block_delta` ops (`append_code`) and end with `block_end` (which flips `payload.collapsed=true` for UI auto-collapse). On user cancellation, the backend skips `block_end` so the reasoning panel stays expanded at its last streamed state.
