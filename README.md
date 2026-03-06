# PocketAI

This repo contains:

- `pocketai_django/`: Django backend + web portal (Chat Portal, RAG, ingestion, admin).
- `mobile/`: **Paused** (not actively maintained right now).

MCP is the only active orchestrator path (legacy orchestration is deprecated).

Status:
- Platform is in **beta / launch preparation**.
- Voice stack exists in the repo, but its rollout status should be verified in `pocketai_django/README.md` and current env flags before assuming it is active.

## Django (pocketai_django)

```sh
cd pocketai_django
python3 -m venv .venv
. .venv/bin/activate
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver 127.0.0.1:3000
```

### Local dev workers (sub-agents + voice calls)

See `pocketai_django/README.md` for the full list of processes to run (portal + `process_agent_runs` + voice workers + ngrok).

Docs index: `pocketai_django/docs/README.md` (voice docs are **dev-only**).

## Django LLM configuration

The Django-side AI orchestration loads API credentials from the repository-level `.env`. Add entries such as `DEEPSEEK_API_KEY` or `OPENAI_API_KEY` there before running `.venv/bin/python manage.py runserver`.

To confirm that Django can see your key (and that heuristics won’t run), execute:

```sh
cd pocketai_django
DJANGO_SETTINGS_MODULE=pocketai.settings .venv/bin/python - <<'PY'
from apps.services.llm_provider import load_default_provider
print("Provider:", load_default_provider().__class__.__name__)
PY
```

If a provider class name prints (e.g., `DeepSeekChatProvider`), the LLM calls are active; otherwise Django will fall back to heuristics.
