# Phase 4 – Page-by-Page Template Migration

## Landing page parity
- Completed marketing layout by adding Trusted By marquee and Mobile App promo partials.
- Landing view now serves copy for all hero and marketing sections, composed via reusable partials.

## Legal pages migrated
- Added SSR privacy and terms pages (`/privacy/`, `/terms/`) using shared `frontend/legal/page.html` layout.
- All legal content (overview, collection, acceptable use, etc.) authored in Python via `_legal_section` helper with server-managed strings.
- Mobile app promo appended to legal pages for consistent CTA.

## Template helpers
- Introduced `_legal_section` helper in `frontend/views.py` for structured legal sections.
- Reused `_mobile_app_section` across landing and legal views.

## URL updates
- `frontend/urls.py` now routes privacy and terms endpoints to Django views.

## Tailwind
- Rebuilt `frontend/static/css/main.css` to ensure new templates/classes are included.

## Next actions (Phase 5 preview)
- Implement interactive behaviours (chat streaming, CSAT) with server-provided JSON endpoints and progressive enhancement scripts.
- Begin migrating authenticated dashboard pages with stub data and component partials.
- Plan API adapters for conversations/customers to drive SSR-rendered tables with follow-up htmx/JS enhancements.
