# Phase 5 – Dynamic Interactions & Streaming

## Chat portal SSR + progressive enhancement
- Added `frontend/chat/portal.html` which renders the public chat experience server-side (agent info, transcript, CSAT prompt).
- New JS module `frontend/static/js/chat-portal.js` progressively enhances the page: handles message submit, SSE streaming, status badge updates, CSAT form, and toasts.

## Stubbed backend endpoints
- Created lightweight endpoints under `apps/api/chat_portal.py` mimicking chat APIs: message send, SSE stream, heartbeat events, and CSAT submission. Responses are mocked but mirror production contracts so the frontend wiring is validated.
- `apps/api/urls.py` now exposes `/api/chat/messages/`, `/api/chat/stream/send/`, `/api/chat/events/`, and `/api/chat/csat/`.

## Django view wiring
- `frontend.views.chat_portal` resolves slugs, constructs mock agent/business context, seeds initial transcript, and passes API endpoint URLs to the template.
- Landing/legals unchanged except for shared `_mobile_app_section()` helper reused across views.
- `frontend/urls.py` now routes `/<business_slug>/<agent_slug>/` to the chat portal view.

## Asset rebuild
- Re-ran `npm run build:tailwind-django` to keep the generated CSS aligned with new markup classes.

## Next steps (Phase 6 preview)
- Retire remaining React routes by migrating dashboard and registration flows to Django templates.
- Replace React data hooks with Django views + htmx/Fetch modules, then remove Vite tooling once parity is confirmed.
- Harden chat endpoints to call real backend services and add CSRF/auth handling in preparation for the backend migration.
