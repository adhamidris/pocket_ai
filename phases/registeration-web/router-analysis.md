**Frontend Evidence Used**

- Web Register page `src/pages/Register.tsx` with steps `'form' | 'business' | 'agent' | 'uploads'`; fields: `firstName`, `email`, `password`, `confirmPassword`; business: `businessName`, `industry`, `specifyIndustry`, `lineOfBusiness[]`, `lineOfBusinessCustom[]`, `country`, `website`; agent: `agentName`, `agentTitle`, `agentTone`, `agentTraits[]`, `agentEscalation`; uploads: `uploadsVision[]`, `uploadsMission[]`, `uploadsCatalog[]`, `uploadsFaqs[]`, `uploadsKb[]`, `uploadsSops[]`, `uploadsTc[]` + corresponding `...Url` inputs. [Confirmed]
- Models baseline: `phases/registeration-web/models-analysis.md` (entities/enums + `registration_sessions`, `businesses`, `agents(+traits)`, `knowledge_items(+urls/files/texts)`). [Confirmed]
- Repository contracts: `phases/registeration-web/repository-analysis.md` (session CRUD, membership, business/niches, agents, knowledge, pagination, error shape). [Confirmed]
- Service orchestration: `phases/registeration-web/service-analysis.md` (5 operations: start_registration, upsert_business_profile, configure_agent, attach_upload_links, complete_registration; idempotency, step progression). [Confirmed]

**Phase Requirements (Router)**

- Purpose
  - Expose HTTP endpoints for the 4-step registration wizard, mapping 1:1 to service operations. [Confirmed]
  - Enforce multi-tenant scoping via `business_id` on tenant operations; pre-tenant operations use `registration_id` + `user_id` context. [Confirmed]
  - Translate domain errors to HTTP status, preserve `{ code, message, details? }` body. [Confirmed]
  - Support idempotency using `Idempotency-Key` header; forward to service. [Assumption]

- Versioning & Media
  - Base path: `/v1/registration`. [Assumption]
  - Content-Type: `application/json; charset=utf-8`. Reject others with `415`. [Assumption]
  - Accept-Language optional; forwarded to service for `language` defaults (uploads). [Assumption]

- Endpoints
  1) POST `/v1/registration/sessions`
     - Purpose: Start a new registration (Step 1). [Confirmed]
     - Auth: None (public). [Assumption]
     - Headers: `Idempotency-Key` optional. [Assumption]
     - Body: `{ firstName, email, password? }` or OAuth path implied by absence of `password`. [Assumption]
     - Success: `201 Created` with session summary and next step. [Assumption]
     - Errors: `400 validation`, `409 conflict` (duplicate email race), `504 db_timeout`.

  2) PUT `/v1/registration/sessions/{registration_id}/business`
     - Purpose: Upsert business profile (Step 2). [Confirmed]
     - Auth: Required. [Assumption]
     - Headers: `Authorization: Bearer ...`, `Idempotency-Key` optional. [Assumption]
     - Body: `{ businessName, industry, specifyIndustry, lineOfBusiness[], lineOfBusinessCustom[], country?, website? }`. [Confirmed]
     - Success: `200 OK` with created/linked business, niches, and updated session. [Assumption]
     - Errors: `400 validation`, `403 forbidden` (not session owner), `404 not_found` (session), `409 conflict` (already linked to different business), `410 expired`, `504 db_timeout`.

  3) PUT `/v1/registration/businesses/{business_id}/agent`
     - Purpose: Configure agent (Step 3). [Confirmed]
     - Auth: Required (OWNER/ADMIN). [Assumption]
     - Headers: `Authorization`, `Idempotency-Key` optional. [Assumption]
     - Body: `{ agentName?, agentTitle?, agentTone?, agentTraits[], agentEscalation? }`. [Confirmed]
     - Success: `200 OK` with agent (if created) and updated session to `knowledge_uploads`. [Assumption]
     - Errors: `400 validation`, `403 forbidden` (insufficient role), `404 not_found` (agent/session), `410 expired`, `504 db_timeout`.

  4) POST `/v1/registration/businesses/{business_id}/uploads`
     - Purpose: Attach knowledge URLs (Step 4). [Confirmed]
     - Auth: Required (OWNER/ADMIN). [Assumption]
     - Headers: `Authorization`, `Idempotency-Key` optional. [Assumption]
     - Body: `{ links: { vision?: string[], mission?: string[], catalog?: string[], faqs?: string[], kb?: string[], sops?: string[], tc?: string[] }, language? }`. [Confirmed concept; language Assumption]
     - Success: `200 OK` with per-category created counts, duplicate count, and session (possibly marked `completed`). [Assumption]
     - Errors: `400 validation` (0 or >50 URLs), `403 forbidden`, `404 not_found`, `410 expired`, `504 db_timeout`.

  5) POST `/v1/registration/sessions/{registration_id}/complete`
     - Purpose: Mark registration as completed. [Confirmed]
     - Auth: Required. [Assumption]
     - Headers: `Authorization`.
     - Body: `{}` (empty). [Assumption]
     - Success: `200 OK` with final session and progress snapshot. [Assumption]
     - Errors: `400 validation` (incomplete preconditions), `403 forbidden`, `404 not_found`, `410 expired`, `504 db_timeout`.

- Error Mapping (domain → HTTP)
  - `validation` → `400 Bad Request` [Confirmed]
  - `conflict` → `409 Conflict` [Confirmed]
  - `not_found` → `404 Not Found` [Confirmed]
  - `expired` → `410 Gone` [Assumption]
  - `forbidden` → `403 Forbidden` [Assumption]
  - `db_timeout` → `504 Gateway Timeout` [Assumption]

- Idempotency
  - Header: `Idempotency-Key` (opaque string, ≤ 128 chars). Forward to service `idempotency_key`. [Assumption]
  - Applies to: POST `/sessions`, PUT `/sessions/{id}/business`, PUT `/businesses/{id}/agent`, POST `/businesses/{id}/uploads`. [Assumption]
  - Replay returns prior `200/201` with identical body; conflicts return `409`. [Assumption]

**Data Contracts (JSON examples)**

- POST /v1/registration/sessions — request
  {
    "firstName": "Aisha",                 // [Confirmed] 1–80 chars
    "email": "aisha@example.com",        // [Confirmed] RFC5322 basic
    "password": "hunter2!!"              // [Confirmed] ≥8; omit for Google [Assumption]
  }

- POST /v1/registration/sessions — 201 response
  {
    "registrationId": "b6b7c2ce-2a04-4d24-9c24-2a0830b0c2a1",  // [Assumption]
    "user": { "id": "2c9f...", "email": "aisha@example.com", "firstName": "Aisha" }, // [Confirmed/Assumption]
    "nextStep": "business"                                      // [Confirmed]
  }

- PUT /v1/registration/sessions/{registration_id}/business — request
  {
    "businessName": "Pocket AI Studio",       // [Confirmed] 2–120
    "industry": "SaaS & Software",            // [Confirmed]
    "specifyIndustry": "",                     // [Confirmed]
    "lineOfBusiness": ["Customer Support"],    // [Confirmed]
    "lineOfBusinessCustom": [],                 // [Confirmed]
    "country": "United States",               // [Confirmed]
    "website": "https://example.com"          // [Confirmed]
  }

- PUT /v1/registration/sessions/{registration_id}/business — 200 response
  {
    "business": { "id": "caa6...", "name": "Pocket AI Studio", "industryCode": "industry:smb-software" }, // [Assumption mapping]
    "niches": ["niche:customer-support"],       // [Assumption mapping]
    "session": { "id": "b6b7...", "currentStep": "agent_setup", "stepsCompleted": 2, "totalSteps": 4 } // [Assumption]
  }

- PUT /v1/registration/businesses/{business_id}/agent — request
  {
    "agentName": "Pocket Assistant",          // [Confirmed]
    "agentTitle": "Support Specialist",       // [Confirmed label] → enum [Assumption]
    "agentTone": "Friendly",                   // [Confirmed label] → enum [Assumption]
    "agentTraits": ["Helpful", "Patient"],     // [Confirmed labels] → enums [Assumption]
    "agentEscalation": "On fallback"           // [Assumption label] → enum
  }

- PUT /v1/registration/businesses/{business_id}/agent — 200 response
  {
    "agent": {
      "id": "3f98...", "name": "Pocket Assistant", "role": "support", "tone": "friendly",
      "traits": ["patient", "proactive"], "escalationRule": "on_fallback"
    },
    "session": { "currentStep": "knowledge_uploads", "stepsCompleted": 3 }
  }

- POST /v1/registration/businesses/{business_id}/uploads — request
  {
    "links": {
      "vision": ["https://example.com/vision"],  // [Confirmed]
      "faqs": ["https://example.com/faqs"]
    },
    "language": "en"                             // [Assumption]
  }

- POST /v1/registration/businesses/{business_id}/uploads — 200 response
  {
    "created": { "vision": 1, "faqs": 1 },      // [Assumption]
    "duplicates": 0,                               // [Assumption]
    "session": { "currentStep": "completed", "stepsCompleted": 4 }
  }

- POST /v1/registration/sessions/{registration_id}/complete — 200 response
  {
    "session": { "id": "b6b7...", "currentStep": "completed", "stepsCompleted": 4, "totalSteps": 4 },
    "progress": { "business_created": true, "agent_configured": true, "knowledge_count": 3 }
  }

Error payload (all endpoints)
- Shape: `{ "code": string, "message": string, "details"?: object }` [Confirmed]

**Tenancy & Permissions**

- Step 1 `/sessions`: unauthenticated; creates user + session (pre-tenant). [Assumption]
- Steps 2–5: authenticated; router resolves `user_id` from token; all tenant operations require `business_id` (path param) and membership OWNER/ADMIN (Steps 3–4) or session ownership (Steps 2 & 5). [Assumption]
- Cross-tenant guard: always include `business_id` in service calls; never infer from user alone. [Confirmed intent]

**Constraints & Quality Gates**

- Content-Length: max 128 KB per request. [Assumption]
- Upload links: 1–50 URLs per request; 500 total per business at registration time (enforced by service). [Confirmed via service]
- Timeouts: request handling should not exceed 5s overall; DB statement timeout already 2s in repositories. [Assumption]
- Idempotency-Key: ≤ 128 chars; treat as opaque; cache window 24h. [Assumption]
- Rate limits (per user): `POST /sessions` 5/min; `PUT /business` 10/min; `PUT /agent` 10/min; `POST /uploads` 5/min; `POST /complete` 10/min. [Assumption]
- Error mapping: as defined above; unknown errors map to `500` with code `internal_error`. [Assumption]

**Dependencies & Stubs**

- Auth dependency to provide `user_id` and roles for the current token. [Assumption]
- Request ID middleware to attach `request_id` to logs and error payloads (observability phase). [Assumption]
- Idempotency storage (cache/DB) for `Idempotency-Key` replay semantics (service accepts key; router may also dedupe). [Assumption]
- Input validation layer (schema contracts already defined in schema phase); router binds/validates and forwards to service. [Confirmed]

**Risks & Tradeoffs**

- Label-to-enum mapping errors (role/tone/traits/industry/LOB) may surface as `validation` if the client sends unknown labels; ensure UI options remain consistent. [Assumption]
- Idempotent POST semantics depend on storage for keys; if unavailable, duplicates may slip through at router level (service still dedupes URLs). [Assumption]
- Using `410 Gone` for expired sessions communicates clearer semantics than `409` but may need client handling updates. [Assumption]
- Public Step 1 may invite abuse; mitigate with CAPTCHA or stricter rate limits (security phase). [Assumption]

**Acceptance Criteria**

- All five endpoints are specified with paths, verbs, headers, auth requirements, status codes, and error mappings. [Confirmed]
- Request/response JSON examples provided; all fields tagged and aligned with UI/service. [Confirmed]
- Tenancy and permission rules stated; cross-tenant risks and guards documented. [Confirmed]
- Constraints (size, limits, timeouts, idempotency, rate limits) documented. [Confirmed]

**Open Questions**

- Should Step 1 require auth (e.g., email verification step) or remain public? [Assumption] -- The user should be authenticated upon receiving his email and his password.
- Confirm final header name for idempotency (`Idempotency-Key` vs `X-Idempotency-Key`). [Assumption] -- Use Idempotency-Key (no X- prefix)
- Do we expose a read endpoint for session status (GET `/sessions/{registration_id}`)? [Assumption] -- yes
- Are there regional/CORS constraints for the registration routes? [Assumption] -- not sure so you do the needed for me.
- Should `/uploads` return the created item IDs per URL for client-side linking? [Assumption] -- okay.

