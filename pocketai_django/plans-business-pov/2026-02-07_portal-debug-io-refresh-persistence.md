# Plan: Persist Portal Debug Tool I/O Across Refresh

## Technical Plan
1. Locate the final turn persistence path where `debug_tools` is already emitted in `turn_persisted` events.
2. Persist the same `debug_tools` payload into the assistant `ConversationMessage.metadata` when debug tracing is enabled.
3. Keep existing metadata intact (merge, do not replace) so agent-run/task metadata is unaffected.
4. Reuse existing frontend metadata hydration so refresh/bootstrap reads `metadata.debug_tools` and repaints debugger panel.
5. Add tests to verify metadata persistence and bootstrap serialization includes `debug_tools`.
6. Run targeted Django tests covering new behavior.

## Business POV

### Scenario 1: Retrieval quality investigation after refresh
- **Before:** QA sees suspicious tool behavior, refreshes, and loses the debug panel state.
- **After:** The exact tool request/response payload remains attached to the assistant message and reappears immediately.
- **Success metric:** Fewer “cannot reproduce after refresh” incidents; faster root-cause analysis.

### Scenario 2: PM reviewing customer complaint replay
- **Before:** PM can’t verify what the LLM actually sent/received without reproducing the turn live.
- **After:** PM opens the historical conversation and inspects the exact same debugger payload from that turn.
- **Success metric:** Time-to-understand complaint drops; less engineering back-and-forth.

### Scenario 3: Engineer comparing pre/post retrieval changes
- **Before:** Engineers must keep tab open and avoid reloads to compare traces.
- **After:** Engineers can refresh, switch sessions, come back, and still inspect exact I/O.
- **Success metric:** Debug workflow becomes reliable; fewer manual screen recordings.

### Scenario 4: Non-debug sessions in production
- **Before/After expectation:** No debug payload is persisted when tool debug tracing is off.
- **Success metric:** No added metadata noise for normal customer conversations.

### Risk / Tradeoff
- Storing debug payload in message metadata increases record size for traced turns.
- Mitigation: persist only when debug tracing is enabled and only for the assistant turn that produced it.
