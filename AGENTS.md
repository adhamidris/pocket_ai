# Repository Guidelines

## Project Structure & Module Organization
- Frontend lives in `src/` (React + TypeScript); feature pages reside under `pages/`, shared UI under `components/`, and cross-cutting hooks in `hooks/` and `contexts/`.
- Backend code is in `backend/app/` with FastAPI routers in `api/`, services in `services/`, SQLAlchemy models in `models/`, and shared utilities in `utils/` and `core/`.
- Database migrations sit in `backend/migrations/`; backend tests belong to `backend/tests/`. Static assets for the web app are under `public/`.

## Build, Test & Development Commands
- `npm install` then `npm run dev` boots the Vite dev server for the frontend.
- `npm run lint` ensures the TypeScript React surface matches the ESLint configuration.
- `cd backend && uvicorn app.main:app --reload` starts the FastAPI app against your configured database.
- `cd backend && pytest` exercises backend unit and service tests; add `-k` for targeted runs.
- `cd backend && alembic upgrade head` applies database migrations after model updates.

## Coding Style & Naming Conventions
- TypeScript follows ESLint defaults: 2-space indentation, camelCase for variables/functions, PascalCase for React components, kebab-case for file names within `components/`.
- Python code uses 4-space indentation with `black`, `ruff`, and `mypy`; align service names with their module purpose (e.g., `CustomersService`).
- Keep Pydantic schemas suffixed with `Schema` or domain intent (e.g., `CustomerDetail`) and colocate them under `backend/app/schemas/`.

## Testing Guidelines
- Prefer backend coverage via `pytest`, organizing tests to mirror `app/` modules (e.g., `tests/api/test_customers.py`).
- Add fixtures for database sessions and sample tenants; assert pagination, filtering, and error branches for Customer 360 flows.
- For the frontend, add React Testing Library specs under `src/__tests__/` when UI logic grows; snapshot tests alone are insufficient.

## Commit & Pull Request Guidelines
- Follow the existing history: concise, present-tense messages (`feat: add customer router`, `fix: reorder CORS`), limited to ~70 characters in the subject.
- Each PR should describe scope, list manual/automated test results, link related issues, and include screenshots or GIFs when UI changes occur.
- Request reviewers familiar with the touched area and ensure all CI lint/test jobs pass before asking for merge.

## Current Implementations Snapshot
- Customer 360 endpoints (`/v1/customers`) are wired with Pydantic schemas, service methods, and router integration.
- `CustomersService` now performs real SQLAlchemy queries for listing, detail fetches with `include` support, note/tag mutations, and bulk import handling.
- Dependency helpers (`require_business_id`, `get_customers_service`) standardize authorization and service access across the backend.
