# Plan
1. Add execution conversation field + migration.
2. Create execution session on run creation.
3. Use FK in run processing/portal.
4. Update tests for new linkage.

# Business POV
## Scenarios
1. **User asks “now email that” after a run finishes**: The follow-up reliably resumes the same sub-agent session tied to the main chat, so the email is sent without errors or rework.
2. **Multiple simultaneous chats for the same business**: Each background run is scoped to its own anchor chat, preventing cross-chat leakage or mixing of session context.
3. **Approval-required run**: The approval is validated against the run’s dedicated execution session, reducing mismatches and failed approvals.
4. **Long-running background task**: The task can be continued hours later because the execution session ID is persisted on the run record, not ephemeral metadata.

## Success Measures
- Continuation success rate for completed runs increases to >99%.
- Zero cross-chat continuation incidents (no wrong-session resumes).
- Reduced support tickets related to “can’t continue run” or “no execution conversation.”
