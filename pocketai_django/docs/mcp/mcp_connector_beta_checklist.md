# MCP Connector (BETA) — QA Checklist

## Dashboard
- Navigate to `Dashboard → MCP Connector (Beta)` and confirm the tab loads.
- Create a connection (manual + marketplace template) and confirm it appears in the list.
- Toggle `Enabled` off/on and confirm state persists after refresh.

## Connection Test
- Click **Test** on a connection and confirm:
  - Success: tool count is populated and the connection shows “Tested”.
  - Failure: error message is shown and stored (without leaking secrets).

## Agent Assignment
- Open **Agents** for a connection and opt an agent out, then refresh and confirm:
  - Assigned count decreases.
  - Agent shows as disabled for that connection.
- Opt the agent back in and confirm assignment is restored.

## Runtime (MCP Orchestrator)
- Start a chat session with MCP orchestrator enabled and confirm:
  - Enabled + assigned MCP connections contribute tool schemas to the LLM tool list.
  - Opted-out agents do not receive the remote tools.
  - Disabled connections do not execute or appear as callable tools.

## Security
- Try adding a connection pointing to private/local addresses and confirm it’s blocked (SSRF guard).
