# Portal Streaming Rollout (Phase 5)

Date: 2026-02-05

## Plan (no feature flag)
1. Ship the Phase 1–4 changes globally (backend + portal JS/template updates).
2. Ensure deploy config matches the intended runtime:
   - Turn execution: `PORTAL_TURN_EXECUTION_MODE=worker`
   - Event buses: `PORTAL_TURN_EVENT_BUS=redis`, `PORTAL_SESSION_EVENT_BUS=redis`
3. Force client asset refresh:
   - Bump `PORTAL_ASSET_VERSION` (or restart the web process if you rely on the default timestamp version).
4. Monitor immediately after deploy (first 30–60 minutes):
   - Stream errors / 500s on `/api/chat/events/` and `/api/chat/turns/<id>/events/`
   - `stream.turn_sse` + `stream.session_sse` metrics (time-to-first-event, events_sent, keepalives)
   - `stream.portal_emit_blocks_ignored` (should be ~0; if non-zero, models are still trying)
5. Run the Phase 0 prompt set in the portal UI (long text, lists, tool approvals, cancel) and confirm UX invariants.

Rollback (if needed):
- Re-deploy the previous build (or revert the commit set) and bump `PORTAL_ASSET_VERSION` again to invalidate cached JS.

## Business POV

### What this improves (visitor experience)
- **No more “dual-engine fighting”**: portal turns stay single-mode (server-built blocks), preventing mid-stream cutoffs and bursts.
- **Caret feels intentional**: the purple caret stays anchored to the actual typing location (leaf blocks only).
- **Smoother pacing around tools**: tool cards no longer trigger force-drain “flash dumps”; text stays sequential and styled.

### Scenarios to validate (2–5)
1. **Long markdown answer** (lists + paragraphs): smooth typing without sudden “fast flash” dumps.
2. **Tool boundary** (search → answer): tool card appears without overtaking queued list items; no end-of-turn dump.
3. **Markdown table**: table styles apply while streaming and persist after `turn_persisted`.
4. **Cancel mid-stream**: output stops cleanly; no late “catch-up” burst after cancellation.

### What might regress
- Tool cards can appear slightly later if there is a large pacing backlog (intentional to preserve ordering).
- If upstream infra is tuned for very high concurrent SSE clients, Postgres connection limits can become the bottleneck (independent of the UX fixes).

### How success is measured
- Drop in user-perceived “cutoffs”, “spinner silence”, and “flashing words”.
- Near-zero `stream.portal_emit_blocks_ignored` events (confirms single-mode is enforced).
- Stable `turn_persisted` emission rate with no increase in failed/cancelled turns.

