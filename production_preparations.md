# Production Preparations

Purpose:
- This file is a pre-production / go-live checklist.
- It is not a feature spec and not a postmortem.
- Future agents should treat this as operational readiness notes to review before enabling production traffic.

Scope:
- Focus on launch safety, reliability checks, and validation drills.
- Record what must be verified before go-live and who signed off.

## Current Priority: Redis Readiness Finalization

Status:
- Code hardening is already implemented.
- Remaining work is operations validation and monitoring sign-off.

### 1) Production Alerts

Set alerts for:
- Health endpoint reports Redis circuit open (`cache_circuit=open`) for more than 1-2 minutes.
- Log events:
  - `redis.circuit.opened`
  - repeated `redis.circuit.failure`
  - `portal.verification.cache_unavailable`
- Spike in `verification_unavailable` (`503`) responses.

### 2) Staging Outage Drill

Run once before production:
1. Confirm baseline behavior with Redis healthy.
2. Stop Redis in staging for ~5 minutes.
3. Verify degraded behavior is controlled:
   - health shows `cache_circuit=open`
   - verification endpoints return `verification_unavailable` (`503`)
   - critical guarded paths fail safely (no silent pass-through)
   - no 500 error storm caused by cache failures
4. Restore Redis.
5. Verify recovery:
   - `redis.circuit.recovered` appears
   - health returns circuit closed
   - verification flow returns to normal

### 3) Sign-off Criteria

Consider Redis gap closed only when:
- Alerts fire during outage and auto-resolve after recovery.
- Observed behavior matches degraded-mode expectations.
- Recovery is confirmed and documented.

## Trade-offs (Expected)

- Initial alert noise until thresholds are tuned.
- During Redis incidents, some critical flows intentionally fail closed (temporary user-facing rejections) instead of silently degrading.
- Ongoing monitoring/runbook overhead.

## Change Log

- 2026-02-19: Created file and added Redis finalization checklist for production readiness.
