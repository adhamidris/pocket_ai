# Plan
1. Add per-agent default approval settings for MCP execution (inherit/override).
2. Add “Always allow this tool” shortcut in the portal approval flow, persisting per-agent tool preferences safely.
3. Add a conversation-scoped activity log endpoint + portal UI to review approvals and tool events over time.
4. Expand tests to cover agent defaults, preference persistence, and activity history.

# Business POV
Scenario 1: A team lead sets an agent’s default to “Approve writes.”
- Expected UX: All read tools run without interruption while write tools pause for approval; no connector-by-connector setup needed.
- Success: Faster workflows for safe tools; fewer unnecessary approval prompts.

Scenario 2: An account manager approves “create_issue” and checks “Always allow this tool.”
- Expected UX: Future calls to the same tool (for this agent) auto-run; the decision is scoped to that agent and visible in audit/activity history.
- Success: Repetitive approvals disappear without weakening other tools’ safety.

Scenario 3: A supervisor needs to review what happened in a conversation after the fact.
- Expected UX: “Activity” shows a chronological log of tool starts/finishes and approvals (approved/denied/expired) with timestamps and tool names.
- Success: Incidents can be explained and audited without reading raw tool payloads.

Scenario 4: An approval is denied.
- Expected UX: The activity log records the denial and the assistant proceeds without executing the tool; no silent retries.
- Success: Operators trust that denial is enforced and visible.
