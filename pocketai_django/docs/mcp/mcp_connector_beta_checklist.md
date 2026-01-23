# MCP Connector (BETA) — QA Checklist

## Dashboard
- Navigate to `Dashboard → MCP Connector (Beta)` and confirm the tab loads.
- Create a connection (manual + marketplace, e.g. GitHub) and confirm it appears in the list.
- Toggle `Enabled` off/on and confirm state persists after refresh.

## Connection Test
- Click **Test** on a connection and confirm:
  - The UI shows a loading state and prevents double-click spam while the test runs.
  - Success: tool count is populated and the connection shows “Tested”.
  - Failure: error message is shown and stored (without leaking secrets).
  - Rate limit (429): UI shows a cooldown/countdown before allowing another test.

## Auto Test Jobs (Production)
- After creating or updating a connection (including OAuth), the platform should enqueue an automatic “Test connection” job.
- Ensure the worker is running in production as a long-lived process:
  - `./venv/bin/python manage.py run_mcp_connection_test_jobs`
  - For a one-off drain: `./venv/bin/python manage.py run_mcp_connection_test_jobs --once --limit 50`
- Expected UX: connections briefly show “Testing” and then populate tool cache without the user clicking **Test**.

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
