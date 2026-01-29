Plan
- Locate approval handling for email tools
- Trace how approval triggers agent runs
- Identify conflict causing re-run
- Report root cause and fix options

Business POV
- Scenario: Agent drafts an email, asks for approval, user approves. Expected: Email sends once and task completes without redoing research.
- Scenario: Approval is denied. Expected: Task stops cleanly with a clear “not sent” outcome and no extra tool calls.
- Scenario: Approval is delayed. Expected: Task remains in “Waiting for approval” without losing prior work.
- Scenario: Approval arrives after a long delay. Expected: Task resumes with full context (no repeated searches), then completes quickly.
