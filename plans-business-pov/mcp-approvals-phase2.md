# Plan
1. Add a conversation-scoped approval model + migration to persist pending/approved/denied MCP tool approvals.
2. Gate MCP remote tool execution behind approvals in the orchestrator, emit approval tool events, and resolve with timeout/denial handling.
3. Add chat-portal approval APIs + audit logging, and expand tool-event serialization to support approval phases and input/output payloads.
4. Update portal UI to render approval prompts, wire approve/deny actions, and tighten tool card styling; add tests for approval decisions.

# Business POV
Scenario 1: An account manager asks the agent to “create issue” via GitHub MCP.
- Expected UX: The tool card shows “Pending” with an approval prompt; the manager can approve or deny without leaving the chat.
- Success: No write action runs without explicit confirmation; the chat continues once approved.

Scenario 2: A pending approval times out because no one responds.
- Expected UX: The tool card shows “Expired” and the assistant asks whether to retry or proceed without the action.
- Success: The conversation doesn’t hang; the user is informed and can choose the next step.

Scenario 3: A connector is set to “Approve all,” including read tools.
- Expected UX: Even read calls prompt for approval; the approval panel explains why and shows the tool input context.
- Success: Admins can enforce strict approval policies without unexpected silent calls.

Scenario 4: An approver denies a tool call.
- Expected UX: The card updates to “Denied,” and the assistant confirms the action was not executed.
- Success: No external writes occur; audit logs capture the decision.
