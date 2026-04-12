from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from django.db import transaction

from apps.accounts.models import AgentActionPermission, AgentProfile


@dataclass(frozen=True)
class AgentActionSetting:
    key: str
    label: str
    description: str
    enabled: bool


@dataclass(frozen=True)
class _ActionDescriptor:
    key: str
    label: str
    description: str
    default_enabled: bool = True


ACTIVE_ACTION_REGISTRY: dict[str, _ActionDescriptor] = {
    "read_knowledge": _ActionDescriptor(
        key="read_knowledge",
        label="Read Knowledge Document",
        description="Request the full content of one or more knowledge uploads by ID.",
    ),
}


def list_action_settings(agent: AgentProfile) -> Sequence[AgentActionSetting]:
    """Return the effective action catalog for an agent."""

    overrides = {perm.action_key: perm.is_enabled for perm in agent.action_permissions.all()}
    settings: list[AgentActionSetting] = []
    for action_key, descriptor in ACTIVE_ACTION_REGISTRY.items():
        enabled = overrides.get(action_key, descriptor.default_enabled)
        settings.append(
            AgentActionSetting(
                key=action_key,
                label=descriptor.label,
                description=descriptor.description,
                enabled=enabled,
            )
        )
    return tuple(settings)


def set_action_setting(agent: AgentProfile, *, action_key: str, enabled: bool) -> AgentActionSetting:
    """Create or update the permission toggle for a single action."""

    descriptor = ACTIVE_ACTION_REGISTRY.get(action_key)
    if descriptor is None:
        raise ValueError("Unsupported action key")

    with transaction.atomic():
        perm, _created = AgentActionPermission.objects.get_or_create(
            agent_profile=agent,
            action_key=action_key,
            defaults={"is_enabled": enabled},
        )
        perm.is_enabled = enabled
        perm.save(update_fields=["is_enabled", "updated_at"])
    return AgentActionSetting(
        key=descriptor.key,
        label=descriptor.label,
        description=descriptor.description,
        enabled=enabled,
    )
