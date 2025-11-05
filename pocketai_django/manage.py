#!/usr/bin/env python3
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main() -> None:
    """Entrypoint for management commands."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pocketai.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - generated scaffold
        raise ImportError(
            "Couldn't import Django. Install it and ensure it's available on your PYTHONPATH."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
