# Plan

1. Add native OAuth disconnect APIs for first-party integrations:
- `POST /api/email/oauth/disconnect/<provider_key>/`
- `POST /api/integrations/oauth/disconnect/<integration_type>/`
- Enforce tenant membership and actor-user binding for non-staff users.
- Clear encrypted credentials, set account status to `disconnected`, and append audit events.

2. Update Integrations dashboard UI (`/dashboard/integrations/`):
- Add `Disconnect` CTA for connected cards.
- Route each card to the correct disconnect endpoint by integration type.
- Show success/error flash feedback and refresh the cards after action.

3. Add backend tests:
- Successful disconnect for email native OAuth.
- Successful disconnect for native integrations (e.g., Google Calendar).
- Forbidden response when actor does not belong to business.

# Business POV

Scenario 1: User connected Google Calendar by mistake.
- Before: They can connect, but cannot disconnect in Integrations UI.
- After: One-click disconnect in the same card.
- Success metric: reduced support/admin requests for “remove integration”.

Scenario 2: Team rotates OAuth apps and wants clean credential state.
- Before: stale tokens may remain until overwritten.
- After: disconnect immediately clears encrypted credential payload and marks account disconnected.
- Success metric: fewer token-related incidents during env/app rotations.

Scenario 3: Multi-tenant safety under mixed team access.
- Before: risk of ad hoc custom workflows for disconnecting.
- After: endpoint enforces tenant access and same-user account binding by default.
- Success metric: no cross-tenant or cross-user disconnect events in audits.

Scenario 4: Operator trust and compliance checks.
- Before: unclear audit trail for who disconnected what.
- After: explicit `disconnected` audit event per account with hashed identifier metadata.
- Success metric: support/compliance can trace disconnect actions without exposing raw secrets.
