# Portal Streaming Single-Mode (Phase 1)

## Plan
1. Enforce **server-built content blocks** for portal turns by preventing model-driven block streaming.
2. Add a guard + log when the model attempts `portal_emit_blocks` during portal turns (to validate the change in production).
3. Add regression tests:
   - Portal turns do not mix modes (model block events never flip `block_ops_active`).
   - Streaming still emits `block_*` events and a terminal `turn_persisted`.

## Business POV

### What this improves
- **More reliable streaming**: prevents “dual engine fighting” where the UI alternates between model-driven blocks and server-built text blocks mid-turn.
- **Fewer mid-stream glitches**: removes a root cause where model block streaming can “take over” and stop subsequent text deltas from being applied.

### Scenarios to validate (2–5)
1. **Long-form answer (no tools)**  
   Visitor asks for a long explanation; response streams continuously; formatting stays stable; no sudden loss of later text.
2. **Tool-heavy answer (search/read)**  
   Visitor asks a knowledge question; tools run; final answer streams normally (plain text deltas → server-built blocks). No dependency on model emitting portal block events.
3. **Edge: model tries to emit blocks anyway**  
   Model attempts `portal_emit_blocks`; the turn continues in server-built mode, and the attempt is logged for auditing/monitoring.
4. **Cancel mid-stream**  
   Visitor cancels a long answer; turn finalizes cleanly (no half-persisted mixed-mode artifacts).

### Possible regressions
- If the model previously relied on `portal_emit_blocks` for some structured streaming, disabling it may reduce “perfect” structure in rare cases; however, the portal’s server-built blocks remain styled and deterministic, and Phase 2/3 address remaining UX artifacts (caret + pacing).

### How success is measured
- Production logs show **near-zero** `stream.portal_emit_blocks_ignored` events after rollout (attempts eliminated).
- Reduced user-reported “cut-offs” and “flashing” during streaming.
- No increase in turn failures or empty responses; stable `turn_persisted` emission rate.

