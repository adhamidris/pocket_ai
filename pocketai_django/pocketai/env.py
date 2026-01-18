"""Helpers for loading environment configuration."""

from __future__ import annotations

from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional local-dev dependency
    load_dotenv = None  # type: ignore[assignment]


def load_project_env() -> None:
    """
    Load environment variables from the repository-level .env file.

    This lets local developers drop API keys (OPENAI_API_KEY, DEEPSEEK_API_KEY, etc.)
    into the root `.env` without exporting them manually before running Django.
    """

    project_root = Path(__file__).resolve().parents[2]  # repo root
    dotenv_path = project_root / ".env"
    if dotenv_path.exists() and load_dotenv:
        load_dotenv(dotenv_path=dotenv_path, override=False)
