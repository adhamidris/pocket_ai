# Repository Guidelines

## Project Structure & Module Organization
- Django backend lives at the repo root. Core app packages sit under `apps/`, shared utilities under `core/`, and settings inside `pocketai/`. Static assets and templates for the public chat experience are in `frontend/static/` and `frontend/templates/`.
- Long‑running orchestration and LLM integrations are housed in `apps/services/`, while FastAPI-style API views reside under `apps/api/`. Database migrations live in `migrations/`, and SQLite dev data is stored in `db.sqlite3`.
- Browser assets for the public widget ship through `frontend/static/js/chat-portal.js`; adjust accompanying HTML in `frontend/templates/frontend/`.

## Build, Test & Development Commands
- `python -m venv venv && source venv/bin/activate` – create/activate the local virtualenv.
- `pip install -r requirements.txt` – install backend dependencies.
- `python manage.py runserver` – start the Django dev server; reads local `.env` when present.
- `python manage.py test` – run the Django test suite. Use `python manage.py test apps.api` for targeted runs.
- `npm install && npm run dev` (inside any JS build subfolder) – only required when touching bundled frontend assets.

## Coding Style & Naming Conventions
- Python: 4-space indentation, `black` formatting, `ruff` linting, and `mypy` typing. Service classes use descriptive PascalCase (`ChatPortalService`), while module-level helpers remain snake_case.
- JavaScript/TypeScript: 2-space indentation, camelCase for functions/variables, PascalCase for React components. Keep kebab-case file names within `frontend/components/`.
- Favor descriptive names for API endpoints (e.g., `chat-stream-send`) and keep templates organized by feature under `frontend/templates/frontend/<feature>/`.

## Testing Guidelines
- Django tests live under `apps/*/tests/` or `backend/tests/`. Mirror the module path (e.g., `apps/api/tests/test_chat_portal.py` for `apps/api/chat_portal.py`).
- Use `pytest` semantics where possible; rely on Django’s `TestCase` for DB-backed checks.
- Aim to cover orchestrator edge cases (placeholder handling, streaming events) and API response fields. Include regression tests whenever adjusting streaming or SSE logic.

## Commit & Pull Request Guidelines
- Follow the existing concise, present-tense style: `feat: add portal SSE queue`, `fix: guard placeholder dedupe`.
- Each PR should state scope, manual/automated test results, and link any Jira/GitHub issues. Include screenshots/GIFs for UI-facing changes (chat portal, dashboards).
- Ensure all lint/test jobs pass before requesting review. Tag reviewers familiar with the touched subsystem (API, services, frontend) and highlight any migrations or config steps in the description.
