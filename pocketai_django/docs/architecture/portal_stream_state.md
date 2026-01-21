# Streaming Turn State Machine

This document captures the Server-Sent Event (SSE) protocol that powers the
public chat portal’s streaming UX. The goal is to keep visitors informed
throughout the turn without leaking internal planner/tool behavior.

## Events

### `block_start`

- Fired when a new **text** block begins streaming.
- Payload:
  ```json
  {
    "message_id": "uuid",
    "block": {
      "block_id": "blk_...",
      "type": "text",
      "created_at": "iso8601",
      "payload": { "text": "" }
    }
  }
  ```

### `block_delta`

- Fired whenever the active text block grows.
- Payload:
  ```json
  {
    "message_id": "uuid",
    "block_id": "blk_...",
    "delta": "text chunk"
  }
  ```

### `block_end`

- Fired when the active text block is closed (e.g., stream completion or tool interleaving).
- Payload:
  ```json
  {
    "message_id": "uuid",
    "block_id": "blk_..."
  }
  ```

### `turnPersisted`

- Fired exactly once after sanitization + persistence succeed.
- `pending` is `false` and `text` contains the persisted answer. `content_blocks`
  is the source-of-truth transcript for deterministic refresh.
- Arrival of this event means the visitor can dismiss spinners; planner/action
  metadata may continue to stream via `turnUpdated`.

### `turnUpdated`

- Sent whenever async planner/action processing adds new metadata.
- Payload:
  ```json
  {
    "message_id": "uuid",
    "metadata_version": 4,
    "answer_confidence": 0.72,
    "ingestion_warnings": [{"label": "Workbook truncated"}],
    "actions": [{"action": "create_case", "status": "queued"}]
  }
  ```
- The widget merges these fields into the existing bubble without replacing the
  answer text.

### `spinnerStatus`

- Optional helper event so the widget can narrate what the model is doing
  without showing filler in the transcript.
- Payload:
  ```json
  {
    "message_id": "uuid",
    "text": "Reading pricing tables…",
    "pending": true
  }
  ```
- Emitted whenever MCP reports a sanitized `placeholder_thinking` string or the
  orchestrator detects a tool-specific status change. Sending an empty `text`
  clears the spinner row.

### `block_tool_use`

- Fired when a tool lifecycle event arrives (started / approval_requested / finished / approval_resolved).
- Payload:
  ```json
  {
    "message_id": "uuid",
    "block": {
      "block_id": "blk_...",
      "type": "tool_use",
      "created_at": "iso8601",
      "payload": {
        "event_id": "evt_...",
        "phase": "started",
        "status": "running",
        "tool_name": "mcp_demo__tool",
        "remote": { "connection_name": "GitHub MCP", "remote_tool": "search" },
        "approval_id": null
      }
    }
  }
  ```

### `block_tool_result`

- Fired when a tool completes (finished / approval_resolved).
- Payload:
  ```json
  {
    "message_id": "uuid",
    "block": {
      "block_id": "blk_...",
      "type": "tool_result",
      "created_at": "iso8601",
      "payload": {
        "event_id": "evt_...",
        "status": "ok",
        "duration_ms": 240,
        "artifact_id": "uuid-or-null",
        "output_preview": { "text": "..." }
      }
    }
  }
  ```

## Rollout Guidelines

- Treat `content_blocks[]` as the transcript source-of-truth.
- `spinnerStatus` remains gated behind `PORTAL_STREAM_STATE_MACHINE`.
