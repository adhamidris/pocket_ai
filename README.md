# PocketAI

This repo contains:

- `pocketai_django/`: Django backend + web portal (Chat Portal, RAG, ingestion, admin).
- `mobile/`: React Native / Expo mobile app.

Legacy FastAPI + web frontend code has been removed from the monorepo.

## Django (pocketai_django)

```sh
cd pocketai_django
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

## Mobile (mobile)

```sh
cd mobile
npm install
npm run start
```

## Django LLM configuration

The Django-side AI orchestration loads API credentials from the repository-level `.env`. Add entries such as `DEEPSEEK_API_KEY` or `OPENAI_API_KEY` there before running `python manage.py runserver`.

To confirm that Django can see your key (and that heuristics won’t run), execute:

```sh
cd pocketai_django
DJANGO_SETTINGS_MODULE=pocketai.settings .venv/bin/python - <<'PY'
from apps.services.llm_provider import load_default_provider
print("Provider:", load_default_provider().__class__.__name__)
PY
```

If a provider class name prints (e.g., `DeepSeekChatProvider`), the LLM calls are active; otherwise Django will fall back to heuristics.
