# PocketAI Django

This is the Django backend + web portal for PocketAI (Chat Portal, RAG, ingestion, admin).

## Quickstart

```sh
cd pocketai_django
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

## Docs

- `docs/product/` — business & SaaS docs (start with `docs/product/saas_brief.md`)
- `docs/architecture/` — RAG/LLM flow docs
- `docs/ops/` — rollout/runbooks/load testing
- `docs/prompts/` — prompt catalogs

## Notes

- Environment variables are loaded from the repository root `.env` via `pocketai/env.py`.
- Do not commit virtualenvs or runtime artifacts (`.venv/`, `var/`, `db.sqlite3`).
