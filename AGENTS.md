<INSTRUCTIONS>
# PocketAI Repo (current)

This repo intentionally contains **only**:
- `pocketai_django/` — Django backend + web portal (Chat Portal + RAG + ingestion).
- `mobile/` — Expo / React Native app.

All documentation lives under `pocketai_django/docs/`.

Legacy FastAPI and the legacy web frontend were removed from this repo to avoid agent confusion.

## Common commands

### Django
- `cd pocketai_django`
- `python manage.py runserver`
- `python manage.py process_knowledge_ingestion`

### Mobile
- `cd mobile`
- `npm install`
- `npm run start`
</INSTRUCTIONS>
