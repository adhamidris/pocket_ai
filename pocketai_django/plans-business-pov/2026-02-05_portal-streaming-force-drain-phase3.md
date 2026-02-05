# Portal Streaming: Remove Flashy Force-Drains (Phase 3)

## Plan
1. Replace tool-boundary `force` drains with **sequenced inserts** + a brief **controlled pacing boost** so tool cards can’t overtake paced text.
2. Ensure **block insertion order matches SSE order** when list items (or other blocks) are queued behind pacing.
3. Replace the `turn_persisted` timeout “dump” with a **finalization boost** and a safe **discard + reconcile** fallback (no forced drain).

## Business POV

### What this improves
- Removes the “burst/flash” effect where a large backlog of text appears instantly at tool boundaries.
- Prevents the UI from feeling like two render engines are competing (tool cards appearing before preceding text finishes).
- Reduces end-of-turn “bullet train” dumps by letting pacing finish quickly, then reconciling to canonical persisted blocks.

### Scenarios to validate (2–5)
1. **Tool call after a long paragraph**  
   The paragraph continues typing smoothly; the tool card appears only after the prior paced text catches up (no sudden text dump).
2. **Lists + tool approvals**  
   List items do not get reordered around tool cards (no “tool card inserted mid-list” glitch).
3. **Tool result while pacing backlog exists**  
   Tool result cards don’t force a drain; they appear in-order without triggering a sudden burst of previously buffered text.
4. **Turn finalization (`turn_persisted`)**  
   The tail end of a long answer finishes quickly (boosted pacing) without a single-frame dump; reconcile doesn’t duplicate text.

### Possible regressions
- Tool cards may appear a fraction later if there’s significant pending pacing; the boost should keep this under ~1s in normal cases.
- During boost windows, typing speed may feel faster; this is intentional to “catch up” without dumping.

### How success is measured
- Manual QA: no visible single-frame “dump” at tool boundaries or on `turn_persisted`.
- No block reordering when list items are queued and tools fire.
- Reduced user reports of “flashing words” / “engine fighting” during streaming.

