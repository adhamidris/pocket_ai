# Chat Portal Streaming Consistency

## Plan
1) Replace end-of-round transcript re-rendering with a deterministic reconcile pass:
   - Use `block_id` as the stable key
   - Update existing DOM in place (no teardown)
   - Insert missing blocks (no entrance animation)
   - Remove extra blocks
   - Reorder blocks to match the persisted canonical order
2) Add a "finalizing" guard so `turnPersisted` cannot trigger late animations (email field stagger, stream-enter).
3) Smooth reasoning ("thinking") behavior:
   - Keep open while active
   - Auto-collapse when complete (only if user did not manually toggle)
   - Animate the collapse instead of snapping native `<details>`
4) Fix scroll UX:
   - Never force-scroll when the visitor is not near bottom
   - Keep "follow-scroll" smooth only when the visitor is already following the stream
5) Remove/gate debug logs that run during normal streaming.

## Business POV
- Scenario: Visitor watches a long response stream while reading. Expected UX: content streams smoothly without the UI "changing" at the end; no late animations or layout snaps when persistence occurs.
- Scenario: Visitor scrolls up mid-stream to reread prior messages. Expected UX: no forced scroll-to-bottom; new content continues streaming; user can return via the existing scroll-to-bottom control.
- Scenario: Agent drafts an email and visitor rejects it. Expected UX: email card transitions happen immediately on state changes; no 1-frame width/height jitter; follow-up assistant text appears without layout shift.
- Scenario: Network hiccup drops some streaming events but persistence succeeds. Expected UX: reconcile inserts/updates the missing blocks at finalization without re-rendering the entire message or re-triggering animations.
- Scenario: Hard refresh on an existing conversation. Expected UX: transcript renders deterministically from persisted `content_blocks` and does not re-stream/animate historical content.

