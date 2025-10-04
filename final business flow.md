# Final Business Flow

Scope

- Living document that maps the end-to-end business flow from the current frontend to the backend design required to support it.
- Updated iteratively as we review pages/components and demo data.
- Goal: enable reverse-building backend APIs, data models, and integrations directly from observed frontend behavior.

How To Use

- We’ll add a section per page/screen with flows, state, data, and API contracts.
- Global inventory sections aggregate entities, APIs, events, and permissions across pages.
- Keep assumptions explicit; convert assumptions into confirmations as we learn.

Assumptions (To Confirm)

- Tech stack: TBD (frontend framework, router, state mgmt, API client).
- Auth: TBD (session/JWT/OAuth), roles/tiers: TBD.
- Environments: local, staging, production; feature flags: TBD.

Global Architecture

- Navigation & Routing: TBD (routes, guards, redirects, deep links).
- Authentication & Session: TBD (login flow, token storage/refresh, logout).
- Global Client State: TBD (store shape, caching, optimistic updates).
- Error Handling: TBD (user-facing toasts/modals, retry rules, fallbacks).
- Telemetry: TBD (analytics events, error reporting, performance metrics).
- Feature Flags: TBD (flag sources, rollout strategy).

Page Index (Working List)

- We’ll populate this from the pages you specify, in priority order.
- Example format: [name] → route(s) → status (pending/in-progress/done)

Per-Page Blueprint (Template)

## Page: <Name>

- Route(s): <e.g., /dashboard, /orders/:id>
- Entry Points: <nav clicks, deep links, redirects>
- User Roles: <who can access / what changes>
- Key Components: <UI components by name/file if known>

Flow

- Happy Path: <numbered steps from entry to success>
- Alternate Paths: <cancellations, retries, edge flows>
- Error States: <validation, network, permission, empty>

Client State

- Local State: <form fields, view state, error state>
- Global State: <store slices/atoms involved>
- Persistence: <localStorage/indexedDB/cookies>

Server Interactions

- Queries: <endpoint, params, cache key, when>
- Mutations: <endpoint, payload, optimistic rules, invalidations>
- Side Effects: <background refetch, redirects, notifications>

Data Contracts

- Entities Used: <list entities and fields>
- Request/Response Schemas: <shape, types, nullable rules>
- Validation Rules: <client and server>

Security & Permissions

- Access Control: <role/ownership checks>
- Sensitive Data: <PII/PCI, masking/redaction>

Observability

- Analytics: <events with payloads>
- Logging: <client logs, correlation IDs>

Open Questions

- <Unknowns to resolve>

---

API Inventory (Aggregated)

- Queries: <list discovered GET endpoints with purpose and inputs>
- Mutations: <list discovered POST/PUT/PATCH/DELETE with side effects>
- Webhooks/Streams: <inbound/outbound events, topics, schemas>

Data Model Glossary

- Entities: <name → canonical fields → relationships>
- Enumerations: <status/type enums with meanings>
- Derived Fields: <computed fields and formulas>

Permissions Matrix

- Roles: <role names>
- Resources: <entities/endpoints>
- Matrix: <role × action (read/create/update/delete/export)>

Background Jobs & Integrations

- Jobs: <schedules, triggers, idempotency, retries>
- Third Parties: <service, endpoints used, secrets/config>

Demo Data & Seeding

- Sources: <fixtures, mocks, mirage/msw, seed scripts>
- Coverage: <which entities and scenarios are represented>

Error Catalog

- Codes/Messages: <normalized errors and user messaging>
- Recovery: <retry/backoff, user actions, support links>

Release Notes (Working)

- v0.1: Scaffold created; awaiting page list.

