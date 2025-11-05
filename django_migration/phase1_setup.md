# Phase 1 – Django Skeleton & Asset Pipeline

## Project bootstrap
- Django project scaffolded under `pocketai_django/` with standard `manage.py`, `pocketai/` settings module, and `frontend` app dedicated to templates/static assets.
- Placeholder API package structure created under `pocketai_django/apps/` (`api`, `services`, `models`) alongside `core/`, `utils/`, and `migrations/` for later backend work.
- Basic health endpoint wired at `/api/health/` returning JSON via `apps.api.views.placeholder`.

## Settings
- `pocketai/settings.py` configures `TEMPLATES` to load from `frontend/templates` and static files from `frontend/static` with `STATIC_ROOT=BASE_DIR / "var/staticfiles"` for `collectstatic`.
- ASGI/WSGI modules included for future deployment targets.

## Tailwind pipeline
- `tailwind.config.ts` now scans Django template and static JS directories to preserve class usage during builds.
- Added npm script `build:tailwind-django` that compiles `src/index.css` into `pocketai_django/frontend/static/css/main.css` (`--minify`).
- Initial compiled asset generated; rebuild via `npm run build:tailwind-django`.

## Next steps (Phase 2 preview)
- Build base template inheritance (`base.html`, nav/footer partials) and load textual content from Django contexts.
- Begin porting shared layout components and global scripts, keeping copy server-side.
