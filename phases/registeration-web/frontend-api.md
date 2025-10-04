Frontend API Plan — Web Registration

Purpose
- Wire the existing 4-step Register wizard in `src/pages/Register.tsx` to the backend `/v1/registration` endpoints.
- Keep it lean and production-minded for a micro‑SaaS: simple fetch client, typed payloads, clear error handling, and idempotency per step.

Scope
- Steps: form → business → agent → uploads → complete.
- Endpoints used (prefix `/v1`):
  - POST `/registration/sessions` → start session (Step 1)
  - PUT `/registration/sessions/{registration_id}/business` → upsert business (Step 2)
  - PUT `/registration/businesses/{business_id}/agent` → configure agent (Step 3)
  - POST `/registration/businesses/{business_id}/uploads` → attach knowledge links (Step 4)
  - POST `/registration/sessions/{registration_id}/complete` → finalize (after Step 4)

Client Setup
- Base URL: `import.meta.env.VITE_API_BASE_URL` (e.g., `http://localhost:8000`).
- Headers per request:
  - `Content-Type: application/json`
  - `X-Request-ID`: generated UUID per request (helps correlation)
  - `Idempotency-Key`: stable per step submission (see below)
  - `Authorization: Bearer <token>`: required for Steps 2–5
  - `X-Captcha-Token`: required for Step 1 only (CAPTCHA HMAC or provider response)
- Idempotency strategy (simple and safe):
  - Compute a SHA-256 (or stable JSON string) of the step’s payload + a client salt; store it in component state so retry/postback reuses the same key.
  - Alternatively, use a UUIDv4 per submit and persist per step until success.
- Error payload shape is consistent: `{ code: string, message: string, details?: object }`. Map to inline errors or toasts.
- CORS: ensure frontend origin is included in backend `ALLOWED_ORIGINS` (Settings). Nothing extra needed client-side.

Proposed API Surface (frontend)
- File: `src/services/registerApi.ts`

  - `startSession(input, opts?)` → POST `/v1/registration/sessions`
    - Input: `{ firstName: string; email: string; password?: string }`
    - Headers: `X-Captcha-Token`, optional `Idempotency-Key`
    - Returns: `{ registrationId: string; user: { id: string; email: string; firstName: string }; nextStep: string }`

  - `upsertBusiness(registrationId, input, opts?)` → PUT `/v1/registration/sessions/{registrationId}/business`
    - Input: `{ businessName: string; industry: string; specifyIndustry?: string; lineOfBusiness: string[]; lineOfBusinessCustom: string[]; country?: string; website?: string }`
    - Returns: `{ business: { id: string; name: string; industryCode: string }, niches: string[], session: { id: string; currentStep: string; stepsCompleted: number; totalSteps: number } }`

  - `configureAgent(businessId, input, opts?)` → PUT `/v1/registration/businesses/{businessId}/agent`
    - Input: `{ agentName?: string; agentTitle?: string; agentTone?: string; agentTraits: string[]; agentEscalation?: string }`
    - Returns: `{ agent?: { id: string; name: string; role: string; tone: string; traits: string[]; escalationRule: string }, session: Session }`

  - `attachUploads(businessId, links, language?, opts?)` → POST `/v1/registration/businesses/{businessId}/uploads`
    - `links`: `{ [category: string]: string[] }` — e.g., `{ vision, mission, catalog, faqs, kb, sops, tc }`
    - `language?`: ISO code (e.g., `en`)
    - Returns: `{ created: Record<string, number>; duplicates: number; session: Session }`

  - `completeRegistration(registrationId, opts?)` → POST `/v1/registration/sessions/{registrationId}/complete`
    - Returns: `{ session: Session; progress: Record<string, unknown> }`

  - Optional (future): `getSession(registrationId)` → poll session status if needed.

Implementation Notes
- Minimal fetch wrapper (`jsonFetch`):
  - Inject base URL, default headers, `Authorization` if token exists, `X-Request-ID`, optional `Idempotency-Key`.
  - Parse non-2xx to `ApiError` with `{ code, message, details }` for uniform handling.
- Auth token management:
  - After Step 1, user must be authenticated for subsequent steps.
  - Depending on backend auth delivery:
    - If there is `POST /auth/login`, call it immediately after `startSession` using the same email/password; store access token in memory + localStorage.
    - If backend will include a token in Step 1 response in future, hydrate from there.
    - For local dev, allow a `.env` fallback token to be used.
- Idempotency keys per step (examples):
  - `form`: hash of `{ firstName, email, provider, captchaSig }`
  - `business`: hash of the business payload fields
  - `agent`: hash of the agent payload fields
  - `uploads`: hash of the links map + language
- Field naming: API v1 uses camelCase; align frontend payload keys directly with API schemas (no mapping layer needed).

Register.tsx Integration (high level)
- Local additions to component state:
  - `registrationId?: string`
  - `businessId?: string`
  - `accessToken?: string`
  - `idempotency: { form?: string; business?: string; agent?: string; uploads?: string }`

- Step 1 (Account form → Start Session)
  - On submit: build payload `{ firstName, email, password }`.
  - Generate `idem.form`; call `startSession(payload, { captchaToken, idempotencyKey: idem.form })`.
  - Save `registrationId` and `user` from response.
  - Immediately authenticate (call existing `auth.login` with same credentials) and store token.
  - Move to `business`.
  - Handle errors:
    - `409 conflict` (email exists): prompt to sign in; if success, resume flow by calling `getSession` (optional) or continue to Step 2.
    - `400/429/410`: toast + allow retry with same idempotency key.

- Step 2 (Business profile → Upsert Business)
  - Require token; include `Authorization` header.
  - Generate/use `idem.business`; call `upsertBusiness(registrationId, payload, { idempotencyKey: idem.business })`.
  - Save `businessId = response.business.id` and `session`.
  - Move to `agent`.
  - Map UI fields directly: `businessName`, `industry`, `specifyIndustry`, `lineOfBusiness`, `lineOfBusinessCustom`, `country`, `website`.

- Step 3 (Agent setup → Configure Agent)
  - Generate/use `idem.agent`; call `configureAgent(businessId, payload, { idempotencyKey: idem.agent })`.
  - Save `agent` if returned; update `session`.
  - Move to `uploads`.
  - UI options (title/role, tone, traits, escalation) should come from static lists already used in the page; backend maps labels to enums.

- Step 4 (Knowledge uploads → Attach Upload Links)
  - Build `links` object from URL fields added in the UI:
    - Example mapping: `{ vision: uploadsVision, mission: uploadsMission, catalog: uploadsCatalog, faqs: uploadsFaqs, kb: uploadsKb, sops: uploadsSops, tc: uploadsTc }`.
  - Generate/use `idem.uploads`; call `attachUploads(businessId, links, lang, { idempotencyKey: idem.uploads })`.
  - Optionally display `created` vs `duplicates` counts.
  - Then call `completeRegistration(registrationId)` and route to dashboard.
  - Edge cases: if session already marked `completed`, treat as success and redirect.

Error Handling & UX
- Map status codes:
  - `400 validation`: surface field-level messages when `details` provides hints; otherwise toast.
  - `401/403`: token missing/expired or insufficient role → redirect to login; resume on return.
  - `404 not_found`: show “session expired or resource missing” with a restart link.
  - `410 expired`: session timed out → restart wizard from Step 1.
  - `409 conflict`: show specific guidance (e.g., duplicate email → login).
  - `429 rate_limit`: brief toast and re-enable the button; do not auto-retry.
- Always re-use the same `Idempotency-Key` when retrying a failed submit to prevent duplicates.

Minimal Deliverables (implementation checklist)
- [ ] Add `VITE_API_BASE_URL` to `.env` (frontend) and ensure backend `ALLOWED_ORIGINS` includes the frontend origin.
- [ ] Create `src/services/registerApi.ts` with the functions above and a tiny `jsonFetch` helper.
- [ ] Add an auth helper (`src/services/auth.ts`) with `login(email, password)` and token storage (in-memory + localStorage).
- [ ] In `Register.tsx`, wire submit handlers per step to call the service functions and update `registrationId`/`businessId` state.
- [ ] Add a simple `useIdempotency()` util in `src/hooks/` to generate and persist per-step keys.
- [ ] Integrate CAPTCHA for Step 1; pass token via `X-Captcha-Token` header.
- [ ] Centralize error mapping to toasts + field messages.

Lightweight Types (copy into `registerApi.ts`)
- `type Session = { id: string; currentStep: string; stepsCompleted: number; totalSteps: number }`
- `type StartResponse = { registrationId: string; user: { id: string; email: string; firstName: string }; nextStep: string }`
- `type BusinessResponse = { business: { id: string; name: string; industryCode: string }; niches: string[]; session: Session }`
- `type AgentResponse = { agent?: { id: string; name: string; role: string; tone: string; traits: string[]; escalationRule: string }; session: Session }`
- `type UploadsResponse = { created: Record<string, number>; duplicates: number; session: Session }`
- `type CompletionResponse = { session: Session; progress: Record<string, unknown> }`

Dev/Local Notes
- Until auth is fully wired, allow a temporary fallback access token via `VITE_DEV_BEARER` for Steps 2–5.
- Backend expects camelCase payloads; send URLs as strings; sanitize/trim client-side.
- Set `CAPTCHA_SECRET` blank in backend `.env` to bypass CAPTCHA during local development, or provide a valid `X-Captcha-Token` from your test harness.

Assumptions
- Auth: A standard `login(email, password)` flow exists (or will exist) to issue a JWT used by the router’s `get_current_user` and `require_owner_or_admin`.
- Rate limiting and idempotency keys are enforced server-side; clients should just send sensible keys and handle 429 gracefully.
- Optional `GET /v1/registration/sessions/{id}` can be added later for polling/status restore; not required for initial wiring.

