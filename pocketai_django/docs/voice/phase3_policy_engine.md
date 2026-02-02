# Phase 3 — Compliance Policy Engine (Country Modules + Audit)

**Status:** Implemented; dev‑only (not production).

Phase 3 adds a **compliance gating layer** on top of Phase 1/2 voice calling.

Goals:

- Marketing calls remain **blocked by default** until explicitly enabled **per country**.
- A country module can enforce requirements (hours, recording rules, etc.) without changing runtime code.
- The platform writes an **immutable audit trail** for policy decisions.

Not legal advice.

---

## What’s implemented

### 1) Country policy modules (DB-backed)

Model: `apps.voice.models.VoiceCountryPolicy`

Key fields:

- `country` (ISO2), `timezone` (IANA TZ)
- `service_calls_allowed`, `marketing_calls_allowed`
- `recording_allowed`, `recording_consent_required`
- Optional call window:
  - `allowed_weekdays` (`[0..6]`, Monday=0)
  - `allowed_call_time_start`, `allowed_call_time_end`
  - Enforcement toggle: `policy_config.enforce_call_window`

Seeded defaults (via migration) for launch countries:

- EG, AE, SA, QA, KW, JO, OM

Defaults include a suggested 09:00–21:00 local window, but **enforcement is disabled** by default (`enforce_call_window=false`).

### 2) Policy evaluation service

Code: `apps.voice.policy_engine.evaluate_voice_compliance_policy`

Inputs:

- `business_profile_id`, `agent_profile_id`
- `call_type` (`service|marketing`)
- `country` (auto-detected from E.164 at tool-time)

Outputs:

- `allowed` boolean
- `reason_code` (string)
- `required_actions` (e.g., AI disclosure, recording consent)
- `warnings` and `details` (structured)

### 3) Immutable audit trail

Model: `apps.voice.models.VoiceCallAuditEvent`

Written by: `apps.voice.policy_engine.audit_policy_decision`

This logs:

- `policy_evaluated` for allowed decisions
- `policy_blocked` for denied decisions

---

## Where policy is enforced

1) **Tool-time** (`initiate_phone_call`)
- Validates the request and blocks if compliance denies.

2) **Worker-time** (`voice_call_worker`)
- Re-evaluates policy right before dialing (race-safe).
- Cancels the CallSession if blocked.

---

## How to enable “allowed hours” enforcement

For a given `VoiceCountryPolicy` row:

- Set:
  - `allowed_weekdays`
  - `allowed_call_time_start`
  - `allowed_call_time_end`
- And flip:
  - `policy_config.enforce_call_window = true`

---

## Notes / current limitations

- Call-window logic is currently “one window for all call types”. If you need different windows for service vs marketing, extend `policy_config` with per-type windows.
- Marketing calls are blocked unless:
  - Workspace enables them (`VoiceConfiguration.marketing_calls_enabled=true`)
  - AND the country policy allows them (`VoiceCountryPolicy.marketing_calls_allowed=true`)
