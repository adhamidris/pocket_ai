**Frontend Evidence Used**

- Web Register page `src/pages/Register.tsx` with steps `'form' | 'business' | 'agent' | 'uploads'`; fields: `firstName`, `email`, `password`, `confirmPassword`, business: `businessName`, `industry`, `specifyIndustry`, `lineOfBusiness[]`, `country`, `website`; agent: `agentName`, `agentTitle`, `agentTone`, `agentTraits[]`, `agentEscalation`; uploads: grouped URL lists (`vision`, `mission`, `catalog`, `faqs`, `kb`, `sops`, `tc`). [Confirmed]
- Models/Repository/Service/Router analyses within `phases/registeration-web/*-analysis.md` defining multi-tenant scoping via `business_id`, `registration_sessions` TTL, OWNER/ADMIN role checks, idempotency key, and error shape `{ code, message, details? }`. [Confirmed]
- Backend scaffolding has placeholders for auth (e.g., `app/core/security.py`), no implemented JWT verification. [Confirmed]

**Phase Requirements (Security)**

- Authentication
  - Token model: short-lived access token (JWT) in `Authorization: Bearer <token>` for Steps 2–5; Step 1 may be unauthenticated. [Assumption]
  - OAuth (Google) support for Step 1: verify `id_token` against Google JWKS; audience = configured `GOOGLE_CLIENT_ID`; issuer = `https://accounts.google.com`. [Assumption]
  - Password path (Step 1): passwords never logged; hash using Argon2id with strong parameters in downstream auth; never store plaintext. [Assumption]

- Authorization (RBAC)
  - Roles: `owner`, `admin`, `agent` at the business (tenant) level. [Confirmed]
  - Enforcement points:
    - Step 2 (upsert business using `registration_id`): caller must be the session owner (`user_id` matches session). [Confirmed]
    - Step 3 (configure agent) and Step 4 (attach uploads): caller must be `OWNER` or `ADMIN` member of `business_id`. [Confirmed]
    - Step 5 (complete): caller must be the session owner. [Confirmed]
  - Always filter by `business_id` for tenant operations; never infer tenancy from `user_id` alone. [Confirmed]

- Input surface controls
  - Accepted headers: `Authorization` for Steps 2–5; `Idempotency-Key` (≤128 chars) optional for idempotent operations; `Accept-Language` optional. [Assumption]
  - Content-Type: `application/json; charset=utf-8` only; reject others (`415`). [Assumption]
  - Request body caps: ≤128 KB; arrays (e.g., `agentTraits`, link lists) capped per spec. [Assumption]

- Anti-abuse protections
  - Rate limits (per IP + per user):
    - POST `/v1/registration/sessions`: 5/min (Step 1). [Assumption]
    - PUT `/business` and `/agent`: 10/min. [Assumption]
    - POST `/uploads`: 5/min (with 1–50 URLs per request); 500 total per tenant at registration time. [Confirmed from service]
    - POST `/complete`: 10/min. [Assumption]
  - Step 1 bot protection: CAPTCHA challenge (score-based) on suspicious traffic; block disposable emails; greylist repeated failures. [Assumption]

- Idempotency & replay safety
  - Accept `Idempotency-Key`; store a hash (SHA-256) keyed by user+route for 24h; on replay return same response. [Assumption]
  - Never retry non-idempotent DB writes; router may retry only safe upstream calls (none in current flow). [Confirmed]

- Transport & headers
  - TLS 1.2+ required; redirect HTTP→HTTPS; set `Strict-Transport-Security: max-age=15552000; includeSubDomains; preload`. [Assumption]
  - CORS: explicit `ALLOWED_ORIGINS`; methods `POST, PUT, GET, OPTIONS`; allow headers `Authorization, Content-Type, Idempotency-Key, Accept-Language`; credentials off by default. [Confirmed intent]

- Secrets & key management
  - Store secrets in env/secret manager: DB URL, JWT signing keys, Google OAuth client IDs, CAPTCHA keys. [Assumption]
  - Key rotation: support multiple signing keys (kid in JWT header); rotate CAPTCHA/OAuth credentials; JWKS cache TTL 10–15 minutes. [Assumption]

- Error handling & privacy
  - Error shape: `{ code, message, details? }`; avoid leaking PII (mask emails beyond domain, never echo passwords or tokens). [Confirmed]
  - Distinguish `not_found` vs `forbidden` to minimize enumeration; for Step 1 email checks, return generic messages. [Assumption]

**Data Contracts (JSON examples)**

- Access token (JWT claims example)
  {
    "iss": "https://auth.pocket.ai",            // [Assumption]
    "sub": "2c9f7f4e-3d62-4a9b-8b3c-2f7b034b0a21", // [Assumption]
    "aud": "pocket-ai-api",                     // [Assumption]
    "exp": 1735689600,                           // [Assumption]
    "iat": 1735686000,                           // [Assumption]
    "scope": "registration:write",               // [Assumption]
    "roles": ["owner"],                          // [Assumption]
    "biz": "caa65c6c-3630-4c3f-88a7-9113e6c8e6d7" // [Assumption]
  }

- OAuth (Google) id_token verification inputs
  {
    "id_token": "eyJhbGci...",                   // [Confirmed concept]
    "client_id": "GOOGLE_CLIENT_ID"             // [Assumption]
  }

- Error response (rate limited)
  {
    "code": "validation",                        // [Confirmed]
    "message": "Too many requests",              // [Assumption]
    "details": { "limit": "5/min" }             // [Assumption]
  }

- Request headers (examples)
  - Authorization: Bearer eyJ...
  - Idempotency-Key: E4fT_2025-10-02_register
  - Content-Type: application/json; charset=utf-8
  - Accept-Language: en

**Tenancy & Permissions**

- Resolution of `business_id`: Step 2 attaches `business_id` to the session; Steps 3–4 always require path `business_id` and verify membership (OWNER/ADMIN). [Confirmed]
- Session ownership: For `/sessions/{registration_id}/business` and `/sessions/{registration_id}/complete`, router obtains `user_id` from token and ensures it matches session owner via service. [Confirmed]
- Cross-tenant safeguard: For Steps 3–4, reject if user lacks membership on `business_id` or roles not in {OWNER, ADMIN}. [Confirmed]

**Constraints & Quality Gates**

- Token lifetimes: Access 15 minutes; Refresh 30 days (if used). [Assumption]
- JWT validation: verify signature (kid→JWKS), exp/nbf, iss/aud; allow 60s clock skew. [Assumption]
- CORS allowlist is non-wildcard; preflight caches for 10 minutes (`Access-Control-Max-Age: 600`). [Assumption]
- Step 1: throttle by IP + email; require CAPTCHA on elevated risk; enforce password min length 8. [Assumption]
- Idempotency: reject keys >128 chars; store SHA-256 of key; TTL 24h. [Assumption]

**Dependencies & Stubs**

- AuthN: JWT verification (access/refresh), Google JWKS fetch/verify, password hashing & email verification workflow (upstream). [Assumption]
- AuthZ: membership resolver to check roles per `business_id`. [Confirmed]
- Rate limiting: shared store (Redis/Memcache) with sliding window; keyed by `user_id` and IP. [Assumption]
- Idempotency storage: small KV with TTL for request hash + response. [Assumption]
- CAPTCHA provider (e.g., reCAPTCHA/Turnstile) SDK/verify endpoint. [Assumption]

**Risks & Tradeoffs**

- Public Step 1 can be abused for account enumeration/spam; mitigations (generic errors, CAPTCHA, rate limits) add friction. [Assumption]
- Overly strict CORS can break legitimate onboarding flows (e.g., multi-origin previews); require explicit allowlist management. [Assumption]
- Idempotency storage consistency under concurrent duplicates; must ensure atomic put-if-absent to avoid thundering herd. [Assumption]
- Third-party dependencies (Google JWKS, CAPTCHA) introduce availability and latency risks; cache and fallbacks needed. [Assumption]

**Acceptance Criteria**

- Authentication/Authorization rules documented per endpoint/step, including role requirements and `business_id` scoping. [Confirmed]
- CORS policy, TLS/HSTS, and header allowlist specified. [Confirmed]
- Rate limits and CAPTCHA requirements defined for Step 1; URL/link caps for Step 4 restated. [Confirmed]
- Idempotency key policy defined (header name, size, TTL, storage behavior). [Confirmed]
- Error shape retained across all authn/authz failures without leaking secrets. [Confirmed]

**Open Questions**

- Should Step 1 require CAPTCHA always, or based on risk score only? [Assumption] -- DO NOT REQUIRE ANY CAPTCHA AT THE MOMENT
- Will Step 1 be public, or gated by temporary token/email verification? [Assumption] -- will be public
- Preferred token delivery: Authorization header only, or support secure HttpOnly cookies? [Assumption] -- the simplest one.
- Exact rate-limit values and burst windows per endpoint? [Assumption] -- do the standard, keep it minimal and simple
- Should we restrict Google signups to specific Workspace domains? [Assumption] -- no we should not
- Do we require email verification before allowing Step 3/4 tenant actions? [Assumption] -- Email verification should be done after registeration completes, if fails to verify then account should be frozen.

