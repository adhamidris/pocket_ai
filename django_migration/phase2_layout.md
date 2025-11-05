# Phase 2 – Layout & Global Content Control

## Base template hierarchy
- Added `frontend/base.html` defining the global shell (head, font imports, Tailwind bundle, nav/footer includes, toast portal, extensible blocks).
- Navigation, footer, and toast container rendered as partials under `frontend/templates/frontend/partials/` to keep markup reusable across pages.

## Server-side copy management
- Introduced `frontend/context_processors.site_globals` and wired it into `TEMPLATES` settings so navigation/CTA/footer text is authored in Python.
- `frontend/views.landing` now supplies a `page.hero` structure with all hero copy (badges, CTA labels, stats, demo transcript) to ensure strings originate server-side.

## Initial SSR landing composition
- `frontend/index.html` extends the base layout and recreates the hero section using the existing Tailwind classes, pulling every string from context.
- Demo transcript, CTA buttons, and stat values mirror the React experience while staying pure SSR.

## Tailwind integration
- Rebuilt `pocketai_django/frontend/static/css/main.css` after adding new templates to guarantee all classes are preserved.

## Next actions (Phase 3 preview)
- Convert reusable UI atoms (buttons, cards, message bubbles) into template snippets or template tags for shared use.
- Begin extracting additional sections (Features, Testimonials, Footer richness) with server-delivered copy to match the React marketing page.
