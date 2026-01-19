# Chat Portal: Inline Tool Activity (No Chip/Card)

## Plan
1. Define an **inline activity row** component that renders as a single muted line inside the assistant message flow (no container border/card).
2. Use persisted ordering (`text_offset` + `sequence_index`) to insert rows **exactly** where tools fired (already supported by current backend events).
3. States + transitions:
   - **running**: subtle purple spinner icon with a faint background wash.
   - **pending approval**: clear inline approval row (approve/deny) anchored at the trigger point.
   - **success / error**: colored check / x icon only (no duration text).
4. Accessibility + motion:
   - Respect `prefers-reduced-motion` (no shimmer/sweep, just icon swap).
   - Ensure keyboard focus/ARIA labels for row and approval buttons.
5. Manual verification:
   - Streaming: text → tool row → text, repeated, without reordering.
   - Refresh: same placement reconstruction from stored tool events.
   - Multiple sequential tools: stable order by `sequence_index`.
   - Approval: buttons visible, actionable, never hidden by “tools hidden” preference.

## Business POV
### Goal
Make tool usage feel “in the background” and premium: visible enough to build trust, but quiet enough to keep the conversation readable.

### Scenarios
1. **User reads an answer while tools run**
   - Expected: A single muted inline line appears where the tool is used; the answer keeps flowing naturally.
2. **Approval is required**
   - Expected: The inline line becomes a clear call-to-action (approve/deny) anchored at the point it was triggered.
3. **User refreshes**
   - Expected: Inline rows reappear in the same places; the tool timeline reads identically to the live run.

### Success Metrics
- Higher perceived “smoothness” and less UI clutter (qualitative feedback).
- Fewer confusion/support reports about tools “jumping around”.
- Approvals completion rate stays stable or improves (no lost visibility).
