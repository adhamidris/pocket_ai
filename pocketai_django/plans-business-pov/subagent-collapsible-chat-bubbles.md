# Plan
1. Inspect chat portal message rendering/hydration for agent-run messages.
2. Define a collapsible UI wrapper that keeps sub-agent text in the DOM for context.
3. Update chat-portal JS to wrap agent-run messages and preserve block rendering/copy behavior.
4. Add scoped CSS for the collapsible chip/expanded content.
5. Sanity-check initial render + new messages for consistent behavior.

# Business POV
## Scenario 1: Customer delegates a background task
- Expected: The portal shows a compact “Background run update” chip instead of a large pasted block.
- Outcome: The main chat stays readable while still preserving the sub-agent update in context.
- Success metric: Fewer “wall of text” interruptions, more compact transcript without losing content.

## Scenario 2: Background run posts multiple continuations
- Expected: Each continuation becomes its own collapsible chip with the latest update nested inside.
- Outcome: Users can scan the chat quickly and expand only what they need.
- Success metric: Users can identify all sub-agent updates at a glance and open them on demand.

## Scenario 3: Sub-agent asks for approval or input
- Expected: A chip indicates approval/input needed; expanding reveals the full prompt.
- Outcome: The prompt is discoverable without overwhelming the conversation view.
- Success metric: Approvals are still completed without confusion, with fewer complaints about chat clutter.

## Scenario 4: New user revisits conversation history
- Expected: Past sub-agent updates remain collapsed by default but accessible.
- Outcome: History review is faster while preserving auditability of the sub-agent’s output.
- Success metric: Reduced scroll length and improved readability without losing information.
