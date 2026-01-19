# Chat Portal: MCP Tool Cards — Timeline Notch (Premium Minimal)

## Plan
1. Restyle tool cards as “timeline notch” components (left line + node) using theme tokens (`hsl(var(--...))`) and scoped CSS.
2. Add a stable `data-tool-state` on cards (running/pending/success/error) so visuals are deterministic.
3. Add a one-shot completion transition (`data-tool-just-finished`) for a subtle “done” sweep without distracting motion.
4. Respect `prefers-reduced-motion` and keep collapsed layout compact; preserve existing expand-to-details behavior.

## Business POV
### Goal
Make tool calls feel like an auditable, premium timeline embedded in the assistant’s response — calmer than “cards”, clearer than raw logs.

### Scenarios
1. **User watches a tool run mid-answer**
   - Expected: A slim “chip” appears with a pulsing node (running) and the answer continues below.
2. **User sees a tool finish**
   - Expected: Status shifts to success/error and a subtle sweep confirms completion without grabbing attention away from the text.
3. **Multiple tools fire back-to-back**
   - Expected: Chips stack cleanly, each reading like a timeline entry (easy scanning, no visual clutter).
4. **Approval required**
   - Expected: Chip state turns “pending” (warning tint) so the user immediately understands it’s blocked; details remain one click away.

### Success Metrics
- **Trust + clarity**: Users can reconstruct “what happened when” at a glance; fewer reports of confusing or noisy tool UI.
- **Brand consistency**: Uses existing theme tokens and minimal motion; looks native in both light and dark modes.

