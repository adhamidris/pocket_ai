Plan
- Inspect tool allowlist usage
- Remove allowlist handling in runs
- Block sub-agent tools globally
- Clean prompt/portal references
- Summarize changes and next steps

Business POV
- Scenario: User starts a background run without specifying tools. Expected: run has access to all safe tools by default (no accidental “missing tool” failures).
- Scenario: User tries to chain a sub-agent into another sub-agent. Expected: tool call is rejected to preserve isolation and avoid runaway task spawning.
- Scenario: User continues a run and expects email tools to be available. Expected: run continues with full tool access without having to re-specify allowlists.
- Scenario: Operator reviews prompts for background runs. Expected: no mention of “tool allowlist” so the product feels simpler and less error-prone.
