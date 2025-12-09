# Streaming Turn State Machine

This document captures the Server-Sent Event (SSE) protocol that powers the
public chat portal’s streaming UX. The goal is to keep visitors informed
throughout the turn without leaking internal planner/tool behavior.

## Events

### `turnPending`

- Fired whenever the assistant bubble text changes during streaming.
- Payload:
  ```json
  {
    "message_id": "uuid",
    "text": "partial answer text",
    "pending": true,
    "session_status": "live",
    "spinner_text": "Reading sales workbook…",
    "metadata_version": 3
  }
  ```
- `metadata_version` increments whenever planner metadata or spinner text
  changes so the widget can coalesce out-of-order events.

### `turnPersisted`

- Fired exactly once after sanitization + persistence succeed.
- Same payload shape as `turnPending`, but `pending` is `false` and `text`
  contains the persisted answer.
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

## Rollout Guidelines

- Behind `PORTAL_STREAM_STATE_MACHINE` until the frontend + backend ship
  together.
- Legacy clients (which only know about `delta`/`final/turnPersisted`) continue
  to work when the flag is off.
- Once the flag is enabled, only `turnPending`/`turnPersisted`/`turnUpdated`
  and `spinnerStatus` are emitted; the widget must not rely on `delta` events.

