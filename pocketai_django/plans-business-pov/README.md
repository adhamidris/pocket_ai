# Plans — Business POV

When starting a new engineering plan (a set of steps you intend to execute), also write a business-oriented POV for the plan in this folder.

## What to include
- **Plan**: the numbered engineering steps (what will be built/changed).
- **Business POV**: 2–5 short scenarios that describe the expected end-user/tenant experience after the plan lands.
  - What the user sees in the portal (latency, clarity, error behavior).
  - What the tenant sees in the dashboard (controls, safety behavior, auditability).
  - How you’d judge success (e.g., p95 latency, fewer retries, fewer “vague answers”, fewer escalations).
  - Call out any tradeoffs/regressions explicitly.

## Suggested filename format
- `YYYY-MM-DD_<short-plan-name>.md`

## Template
```md
# <Plan Name> — Business POV

## Plan
1. ...
2. ...
3. ...

## Business POV
### Scenario 1: <end-user situation>
- Before:
- After:
- Success signals:

### Scenario 2: <tenant/admin situation>
- Before:
- After:
- Success signals:
```

