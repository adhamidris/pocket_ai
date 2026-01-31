from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TenantMemoryPolicyPreset:
    key: str
    label: str
    description: str
    values: dict[str, Any]


# NOTE: These presets are intentionally conservative defaults. They are meant to
# provide a starting point that admins can quickly apply and then customize.
TENANT_MEMORY_POLICY_PRESETS: dict[str, TenantMemoryPolicyPreset] = {
    "general": TenantMemoryPolicyPreset(
        key="general",
        label="General (Default)",
        description="Balanced defaults suitable for most SMB tenants.",
        values={
            "default_hot_period_days": 7,
            "default_warm_period_days": 30,
            "default_archive_after_days": 90,
            "minimum_retention_days": 0,
            "maximum_retention_days": None,
            "purge_enabled": True,
            "legal_hold": False,
        },
    ),
    "banking": TenantMemoryPolicyPreset(
        key="banking",
        label="Banking / FinTech (Conservative)",
        description="More restrictive retention to reduce risk in regulated environments.",
        values={
            "default_hot_period_days": 3,
            "default_warm_period_days": 14,
            "default_archive_after_days": 30,
            "minimum_retention_days": 0,
            "maximum_retention_days": 30,
            "purge_enabled": True,
            "legal_hold": False,
        },
    ),
    "healthcare": TenantMemoryPolicyPreset(
        key="healthcare",
        label="Healthcare (Conservative)",
        description="Shorter retention defaults appropriate for sensitive domains.",
        values={
            "default_hot_period_days": 3,
            "default_warm_period_days": 14,
            "default_archive_after_days": 30,
            "minimum_retention_days": 0,
            "maximum_retention_days": 30,
            "purge_enabled": True,
            "legal_hold": False,
        },
    ),
    "enterprise": TenantMemoryPolicyPreset(
        key="enterprise",
        label="Enterprise (Longer Retention)",
        description="Longer horizons for orgs that want more continuity (still purgeable).",
        values={
            "default_hot_period_days": 14,
            "default_warm_period_days": 60,
            "default_archive_after_days": 180,
            "minimum_retention_days": 0,
            "maximum_retention_days": 365,
            "purge_enabled": True,
            "legal_hold": False,
        },
    ),
}


def tenant_memory_policy_preset_choices() -> list[tuple[str, str]]:
    """Returns (key, label) pairs for admin/UI choice fields."""

    return [(preset.key, preset.label) for preset in TENANT_MEMORY_POLICY_PRESETS.values()]


def get_tenant_memory_policy_preset(preset_key: str) -> TenantMemoryPolicyPreset | None:
    return TENANT_MEMORY_POLICY_PRESETS.get(str(preset_key or "").strip())

