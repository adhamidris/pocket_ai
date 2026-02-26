"""Guardrail test: every ``getattr(settings, "RAG_*")`` key used in the
orchestrator must have a corresponding attribute on
``django.conf.settings``.

This test prevents "dead knob" drift — where a developer adds a new
``getattr(settings, "RAG_NEW_KEY", default)`` call but forgets to add the
setting to ``settings.py``, making it invisible to operators and immune to
``.env`` overrides.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


def _extract_rag_setting_keys(source_path: Path) -> set[str]:
    """Parse *source_path* and return every ``RAG_*`` key referenced via
    ``getattr(settings, "RAG_...", ...)``.
    """
    tree = ast.parse(source_path.read_text(), filename=str(source_path))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "getattr"):
            continue
        if len(node.args) < 2:
            continue
        # First arg should be ``settings`` (name node).
        first = node.args[0]
        if not (isinstance(first, ast.Name) and first.id == "settings"):
            continue
        # Second arg should be a string constant starting with "RAG_".
        second = node.args[1]
        if isinstance(second, ast.Constant) and isinstance(second.value, str):
            if second.value.startswith("RAG_"):
                keys.add(second.value)
    return keys


class TestRagSettingsRegistry(SimpleTestCase):
    """Every ``RAG_*`` key referenced in ``ai_orchestrator.py`` must exist on
    ``django.conf.settings`` so that ``.env`` overrides actually take effect.
    """

    def test_all_orchestrator_rag_keys_exist_in_settings(self):
        orchestrator_path = (
            Path(__file__).resolve().parent.parent / "ai_orchestrator.py"
        )
        self.assertTrue(orchestrator_path.exists(), f"Cannot find {orchestrator_path}")

        keys = _extract_rag_setting_keys(orchestrator_path)
        self.assertTrue(keys, "Expected to find RAG_* getattr calls in orchestrator")

        missing: list[str] = sorted(
            key for key in keys if not hasattr(settings, key)
        )
        if missing:
            bullet_list = "\n".join(f"  - {k}" for k in missing)
            self.fail(
                f"{len(missing)} RAG_* key(s) used in ai_orchestrator.py have no "
                f"entry in settings.py:\n{bullet_list}\n\n"
                "Add each key to pocketai/settings.py with a matching default "
                "so that .env overrides take effect."
            )
