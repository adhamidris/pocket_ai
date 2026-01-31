# AI Phone Calls — Final Phased Plan (MENA-first, Global-ready)

This document is the “final blended plan” combining the original architecture/implementation approach with feasibility constraints, your latest product decisions, and a phased rollout strategy for Egypt + GCC first and global later.

Not legal advice.

---

## 1) Product Goal (Business)

Add a **Voice channel** to PocketAI so orchestrators/sub-agents can place **fully autonomous outbound phone calls** to customers/leads, while:

- A human supervisor can **monitor live transcript + hang up** (no listen-in for v1).
- The platform stores **call recordings + transcripts** and writes outcomes back into the workspace (summary, action items, memory).
- The SaaS owner enforces **mandatory guardrails** (caps, throttles, budgets) that tenants cannot weaken.
- The system supports **Arabic + English**, with dialect preparedness for MENA/GCC.

This naturally splits into two call products on the same engine:

1) **Service / Relationship Calls**: support, verification, collections, onboarding, follow-ups.
2) **Marketing / Outreach Calls** (cold/warm): higher risk; must be gated by a compliance policy engine.

---

## 2) V1 Scope (What we ship first)

### In scope
- Outbound only.
- **Single-call initiation** (no campaign dialer yet): an agent triggers one call at a time.
- Providers:
  - Telephony: **Twilio Voice + Media Streams** (bidirectional)
  - STT: **Deepgram streaming**
  - TTS: **ElevenLabs streaming**
  - LLM: your existing **DeepSeek/OpenAI** stack (no Anthropic requirement)
- Monitoring UI: live **status + transcript + hang up**.
- Recording storage: **Cloudflare R2 (S3-compatible)** for recordings, plus transcript artifacts.
- Mandatory owner guardrails: concurrency, daily dials, duration, budget, country allow-list, marketing default-off, suppression list, audit.
- **Auto-detect country from E.164** and apply country policy.
- **AI disclosure is mandatory** at call start.

### Out of scope (explicitly deferred)
- Inbound calls.
- Campaign primitives (lists/schedules/bulk dialing), “power dialer”, AMD, etc.
- Human takeover (barge-in coaching / whisper / join call). Only hang up for v1.
- Live “background retrieval agent” injection during calls (can be Phase 2/3).
- Twilio-only “local number per workspace” assumption for MENA (see Phase 4 for BYOC/local carrier).

---

## 3) Key Constraints & Design Principles

### Real-time feel requires 3 must-haves (non-negotiable)
1) **Turn-taking + barge-in**: if the customer starts speaking, the agent must stop speaking quickly (flush queued TTS playback + stop generating).
2) **Chunked speech**: stream LLM → TTS in sentence/phrase chunks; do not wait for a full response.
3) **Non-hallucination**: if facts aren’t known, respond with a safe “I’ll check and get back to you” and log a follow-up action item.

### “1000+ concurrent calls” is a scaling program, not a feature flag
- Architect for “1 call ≈ 1 realtime session” (WebSocket + stream state).
- Enforce pacing/backpressure for call creation and for realtime worker capacity.

### MENA telephony coverage reality
For multiple starter markets, “per-workspace local Twilio numbers” may not be available; plan a telephony abstraction with a **BYOC/local carrier** path for later phases.

---

## 4) System Architecture (High-level)

### Components

1) **Call Initiation**
- Agent/sub-agent calls an internal tool (MCP tool) to start a call:
  - validates number, detects country, checks policies/guardrails
  - creates a CallSession in the DB (QUEUED)

2) **Voice Worker (DB-backed queue)**
- Claims queued CallSessions (SELECT … FOR UPDATE SKIP LOCKED pattern)
- Initiates outbound call via provider (Twilio v1)
- Coordinates callbacks and transitions call state

3) **Realtime Call Runtime (ASGI)**
- Twilio Media Streams WS handler:
  - inbound audio → Deepgram STT
  - finalized transcript → LLM turn
  - LLM tokens → chunker → ElevenLabs TTS
  - outbound audio → Twilio WS
- Emits append-only CallEvents and SSE updates to the supervisor UI

4) **Post-call Processing**
- Finalize transcript segments
- Generate summary + action items (LLM)
- Write memory items into existing memory system
- Ingest recording to Cloudflare R2 and store references
- Compute and persist cost

5) **Compliance Policy Engine (Gating Layer)**
- Evaluates (country, call_type, workspace config, time window, suppression/DNC, consent/disclosure requirements)
- Returns allow/deny + required actions (e.g., play disclosure + recording consent flow, enforce allowed hours)
- Logs evidence into audit trail

### Data Flow (V1)

1. Agent → `initiate_phone_call(...)`
2. Create `CallSession(status=QUEUED)`
3. Worker claims session, runs policy + guardrails, starts outbound call
4. Twilio answers → webhook returns TwiML with:
   - mandatory AI disclosure
   - mandatory recording notice + explicit consent (DTMF) before recording starts
   - connect media stream (only after consent)
5. WS pipeline runs STT→LLM→TTS in realtime with barge-in
6. Hang-up or timeout → post-call finalization + recording ingest + summary/memory

---

## 5) Data Model (Conceptual)

Minimal set (names indicative):

- `VoiceConfiguration` (workspace-level)
  - enabled flags (`service_calls_enabled`, `marketing_calls_enabled`)
  - budgets/caps (see defaults below)
  - retention policy for recordings/transcripts
  - disclosure templates (AI disclosure is mandatory)
  - trust tier + ramp schedule

- `OutboundIdentity`
  - workspace ↔ provider mapping (Twilio SID/From number now; BYOC later)
  - allowed countries, status

- `CallSession`
  - workspace, agent profile, optional agent run link
  - `to_number`, `country`, `call_type`, `objective`, `language`
  - state machine: queued → initiating → ringing → in_progress → completed/failed/cancelled
  - transcript anchors, recording reference(s), costs, policy decisions snapshot

- `CallEvent` (append-only)
  - transcript final chunks, llm turns, tts chunks (metadata), status transitions, errors, latencies

- `TranscriptSegment` (final transcript, ordered)

- `CallRecordingAsset`
  - provider recording id/url + R2 object key + retention state

- `VoiceCostRecord` / aggregated costs on CallSession

- `VoiceAuditEvent` (immutable evidence)
  - “policy_evaluated”, “disclosure_played”, “opt_out_requested”, “call_blocked”, etc.

---

## 6) Public/Agent Interfaces

### MCP tool: `initiate_phone_call`
Required:
- `phone_number` (E.164)
- `objective` (call purpose)

Optional/controlled:
- `call_type`: `service` (default) or `marketing`
- `language`: `en` or `ar` (default based on workspace, or inferred)
- `context_items`: structured notes the agent wants available pre-call
- `max_duration_minutes` (upper-bounded by policy)

Returns:
- `call_session_id`
- initial status + any policy warnings (non-sensitive)

Additional admin/supervisor endpoints:
- `GET status/transcript`
- `POST hangup(call_session_id)`
- `GET recording links` (role-gated)

---

## 7) Mandatory Guardrails (Owner-controlled defaults)

Defaults should be easy to change in `VoiceConfiguration` and/or “global owner policy”, but v1 ships with conservative baselines:

### Trust tiers (example defaults)
You can tune these quickly; the point is to enforce hard caps.

- **Trial**
  - max concurrent calls: 1
  - max calls/day: 10
  - max call duration: 6 minutes
  - monthly budget: $50
  - marketing calls: OFF

- **Verified**
  - max concurrent calls: 3
  - max calls/day: 100
  - max call duration: 10 minutes
  - monthly budget: $500
  - marketing calls: OFF by default; unlock per-country after compliance config

- **Enterprise**
  - max concurrent calls: 20 (start lower, ramp up)
  - max calls/day: 2,000 (start lower, ramp up)
  - max call duration: 15 minutes
  - monthly budget: $5,000+ (configurable)
  - marketing calls: OFF by default; unlock per-country after compliance config

### Universal hard rules
- **Country auto-detection** from E.164 is mandatory; if country is unknown → block.
- **AI disclosure message is mandatory** at the start of every call.
- Workspace-level **country allow-list**; default allow-list = selected launch markets only.
- Mandatory **opt-out handling**: “don’t call again” → suppression list (workspace-level).
- Budget/cap checks occur:
  - at initiation time
  - at worker claim time (race-safe)
  - mid-call (duration timeout)

---

## 8) Compliance Policy Engine (Gating)

### Why this exists
With cold outreach + dial-any-number, “generic calling” becomes a compliance product. The engine prevents illegal/high-risk behavior by default and leaves a complete audit trail.

### Inputs
- `country` (from E.164)
- `call_type` (`service|marketing`)
- `workspace configuration + trust tier`
- time-of-day (country-local)
- suppression list / DNC equivalents (where available to you)
- whether required disclosures/consent flow is configured

### Output
- allow/deny
- required actions:
  - play AI disclosure (always)
  - play recording notice and/or collect consent (policy-driven)
  - enforce allowed calling hours (policy-driven)
  - enforce retry/callback limitations (policy-driven)
- audit evidence payload (immutable)

### Default gating stance
- **Marketing calls are OFF by default in all countries** until that country’s compliance module is implemented and workspace is configured.
- Service calls can be enabled earlier with stricter caps.

---

## 9) Recording + Storage (Cloudflare R2)

### V1 approach
- Record the call via telephony provider.
- On recording completion callback:
  - download from provider
  - store in **Cloudflare R2**
  - store `CallRecordingAsset` with object key, hash, duration, retention deadline
- Apply role-based access and retention deletion jobs.

---

## 10) Phases & Exit Criteria (Execution Plan)

### Phase 0 — Realtime Spike & Unknowns (1–2 weeks)
Deliverables:
- One working outbound call where:
  - Twilio WS receives inbound audio and plays outbound TTS back
  - Deepgram STT yields finalized utterances
  - DeepSeek/OpenAI produces response
  - ElevenLabs returns ulaw_8000 audio chunks
- Prototype barge-in + chunking.
Exit criteria:
- “Feels live” for short test calls; no obvious talk-over loops; transcript matches audio.

### Phase 1 — MVP (Service calls) (2–4 weeks)
Deliverables:
- `apps/voice/` app with models + migrations.
- DB-backed worker to claim/execute calls.
- Realtime WS runtime + SSE monitoring endpoints.
- Post-call summary/action items + memory write-back.
- Recording ingestion to R2.
- Mandatory guardrails enforced at initiation + worker claim + runtime timeout.
Exit criteria:
- Agent can initiate a call, supervisor can watch transcript + hang up, and workspace gets recording + transcript + summary + costs.

### Phase 2 — Arabic + Dialect QA (1–3 weeks)
Deliverables:
- Arabic + English in production with tuned STT/TTS configurations.
- QA pack for Egyptian + Gulf accents; code-switch tests.
Exit criteria:
- Consistent STT confidence and acceptable TTS naturalness for supported dialects.

### Phase 3 — Compliance Policy Engine (parallel) (2–4+ weeks)
Deliverables:
- Policy evaluation service + audit logging.
- AI disclosure template system (mandatory).
- Country module framework (per-country rules live in data/config, not code where possible).
Exit criteria:
- Marketing remains blocked until configured; service calls are safely constrained.

### Phase 4 — Country Enablement (ongoing)
Deliverables per country:
- “country policy module” (hours, consent/recording, opt-out, caller identity requirements).
- Telephony capability mapping for the country:
  - Twilio where possible
  - otherwise plan BYOC/local carrier integration
Exit criteria:
- You can explicitly flip a country/call_type from BLOCKED → ALLOWED with confidence and audit evidence.

### Phase 5 — Scale to 1000+ Concurrent Calls (after stability)
Deliverables:
- Horizontal scaling plan for realtime workers.
- Call-creation pacing/backpressure and operational runbooks.
- Observability dashboards for latency, error rate, costs, and cap enforcement.
Exit criteria:
- Sustained high concurrency with bounded latency + bounded cost + acceptable failure rates.

---

## 11) Launch Recommendation (Starter Countries)

Starter list: **Egypt, UAE, KSA, Qatar, Kuwait, Jordan, Oman**

Day 1 recommendation:
- Enable **Service calls** only (very strict caps initially).
- Keep **Marketing calls disabled everywhere** until country modules and local requirements are implemented and verified.

---

## 12) Success Metrics (Operational)
- Latency: time from customer end-of-utterance → agent audio start (P50/P95).
- Transcript accuracy: STT confidence distribution.
- Hangup rate during disclosure.
- Cost per minute and cost per call (by workspace tier).
- Policy blocks by country/call_type (and reasons).
- Abuse signals: high dial counts, high short-call ratio, high block rate, high opt-out rate.

---

## 13) Next Step (Immediate)

Phase 0 spike is the fastest way to de-risk:
- validate bidirectional audio, barge-in behavior, chunked speech, and stable transcript.
- once confirmed, Phase 1 is straightforward engineering work following existing worker/event patterns.

---

## 14) Repo Implementation Notes (Current)

This repository now contains:

- Phase 0 spike: `pocketai_django/apps/voice/views_spike.py` + `pocketai_django/apps/voice/management/commands/voice_spike_ws_server.py`
- Phase 1 MVP:
  - Models + queue fields: `pocketai_django/apps/voice/models.py`
  - Twilio webhooks (signature-validated): `pocketai_django/apps/voice/views_twilio.py`
  - Media Streams runtime + WS auth token: `pocketai_django/apps/voice/runtime.py` + `pocketai_django/apps/voice/management/commands/voice_ws_server.py`
  - Call initiation worker: `pocketai_django/apps/voice/management/commands/voice_call_worker.py`
  - Post-call worker (transcript + summary + optional R2 upload): `pocketai_django/apps/voice/management/commands/voice_post_call_worker.py`
  - MCP tool: `initiate_phone_call` (registered in `pocketai_django/apps/mcp/tools.py`)
  - Supervisor API (transcript SSE + hangup): `pocketai_django/apps/api/voice_calls.py`
- Phase 2 (in-progress → implemented here): Arabic + code-switch QA
  - Arabic TwiML consent/disclosure prompts: `pocketai_django/apps/voice/views_twilio.py`
  - STT dual-stream for Arabic calls (ar + en) and per-chunk TTS voice selection: `pocketai_django/apps/voice/runtime.py`
  - QA checklist/runbook: `pocketai_django/docs/voice/phase2_arabic_qa.md`
- Phase 3 (implemented here): Compliance Policy Engine
  - Country policies (DB-backed): `pocketai_django/apps/voice/models.py` (`VoiceCountryPolicy`)
  - Policy evaluation + audit trail: `pocketai_django/apps/voice/policy_engine.py` + `pocketai_django/apps/voice/models.py` (`VoiceCallAuditEvent`)
  - Seeded launch-country modules: `pocketai_django/apps/voice/migrations/0005_seed_voice_country_policies.py`
  - Runbook: `pocketai_django/docs/voice/phase3_policy_engine.md`

Runbook: `pocketai_django/docs/voice/phase1_mvp.md`
