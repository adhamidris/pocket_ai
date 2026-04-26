# Portal Streaming Protocol (v1)

This document captures the **actual** Server-Sent Events (SSE) contract consumed
by the authenticated chat workspace frontend (`frontend/static/js/chat-portal.js`).

The portal has two independent SSE streams:

1. **Turn stream** (token/block streaming for a single send)
2. **Session stream** (status + sub-agent/task/inbox + voice transcript updates)

This protocol is intentionally **stable**: later phases can change how events are
transported (Redis, workers, websockets), but the **event schema** should remain
compatible.

## Turn Stream (SSE)

Endpoint:
- `GET /api/chat/turns/<turn_id>/events/?session_token=...&since=...`

Notes:
- `session_token` is required.
- `since` is optional and mirrors `Last-Event-ID` semantics (resume from `seq`).
- Response includes SSE comments:
  - `: stream_open`
  - `: keepalive`

### Envelope

The server emits SSE events named **`turnEvent`**. The SSE `id:` is the monotonic
turn sequence (`seq`) used for resume.

```text
id: 12
event: turnEvent
data: {"turn_id":"...","seq":12,"type":"block_delta","payload":{...}}
```

Payload:
```json
{
  "turn_id": "uuid",
  "seq": 12,
  "type": "block_delta",
  "payload": {}
}
```

### `type` values (current)

- `status` - human-readable phase updates (searching/reading/responding)
- `block_start` - a new content block begins
- `block_delta` - incremental updates for an existing block (op-based)
- `block_end` - a block has closed
- `block_tool_use` - tool lifecycle update as a `tool_use` block
- `block_tool_result` - tool completion update as a `tool_result` block
- `turn_persisted` - final answer persisted (includes canonical `content_blocks`)
- `turn_cancelled` - turn cancellation marker (best-effort)

### Block Events

#### `block_start`
Payload:
```json
{
  "block": {
    "block_id": "blk_...",
    "type": "paragraph",
    "created_at": "iso8601",
    "payload": {}
  }
}
```

#### `block_delta`
Payload (op-based, supports incremental rendering without raw markdown leakage):
```json
{
  "block_id": "blk_...",
  "ops": [
    { "op": "append_inline", "nodes": [{ "text": "Hello" }] },
    { "op": "append_code", "text": "print('hi')\\n" }
  ]
}
```

#### `block_end`
Payload:
```json
{ "block_id": "blk_..." }
```

### Tool Block Events

Tool lifecycle events are streamed as content blocks so the UI can render
ordered cards inline with text.

#### `block_tool_use`
Payload:
```json
{
  "block": {
    "block_id": "blk_...",
    "type": "tool_use",
    "created_at": "iso8601",
    "payload": {
      "event_id": "evt_...",
      "phase": "started",
      "status": "running",
      "tool_name": "search_knowledge"
    }
  }
}
```

#### `block_tool_result`
Payload:
```json
{
  "block": {
    "block_id": "blk_...",
    "type": "tool_result",
    "created_at": "iso8601",
    "payload": {
      "event_id": "evt_...",
      "status": "ok",
      "duration_ms": 240,
      "artifact_id": "uuid-or-null",
      "output_preview": {}
    }
  }
}
```

### Finalization Event

#### `turn_persisted`
Emitted exactly once after sanitization + persistence succeed.

Payload:
```json
{
  "text": "final answer",
  "message_id": "uuid",
  "session_status": "open",
  "metadata_version": 1,
  "content_blocks": []
}
```

## Session Stream (SSE)

Endpoint:
- `GET /api/chat/events/?session_token=...`

The session stream is a long-lived SSE channel used for:
- conversation status changes
- sub-agent run/task updates
- inbox/request updates
- voice call transcript updates

### Event Names (current)

- `statusChanged` - `{ "status": "open|closed|..." }`
- `conversationMessage` - `{ "message": { "id": "...", "sender": "...", "content_blocks": [...] } }` (primarily sub-agent / voice)
- `agentRunsSnapshot` - initial snapshot of current runs on connect
- `agentRunEvent` - incremental run events
- `agentRequestsSnapshot` - initial snapshot of agent requests on connect
- `agentRequestEvent` - incremental request events
- `voiceCallTranscript` - incremental transcript events (best-effort)
- `heartbeat` - keepalive marker

## Compatibility Notes

- Additive-only: new `type` values and new payload fields are allowed; existing
  fields must remain backward compatible.
- Ordering: `seq` is monotonic per turn and must remain stable to support resume.
- The canonical persisted transcript is always `content_blocks[]` on the final
  persisted assistant message.
