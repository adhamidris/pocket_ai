from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from django.db import transaction

from apps.accounts.models import AgentActionPermission, AgentProfile
from apps.rag.ai_orchestrator import ACTION_REGISTRY, ActionDescriptor, ActionType


@dataclass(frozen=True)
class AgentActionSetting:
    key: str
    label: str
    description: str
    enabled: bool


def list_action_settings(agent: AgentProfile) -> Sequence[AgentActionSetting]:
    """Return the effective action catalog for an agent."""

    overrides = {perm.action_key: perm.is_enabled for perm in agent.action_permissions.all()}
    settings: list[AgentActionSetting] = []
    for action, descriptor in ACTION_REGISTRY.items():
        enabled = overrides.get(action.value, descriptor.default_enabled)
        settings.append(
            AgentActionSetting(
                key=action.value,
                label=descriptor.label,
                description=descriptor.description,
                enabled=enabled,
            )
        )
    return tuple(settings)


def set_action_setting(agent: AgentProfile, *, action_key: str, enabled: bool) -> AgentActionSetting:
    """Create or update the permission toggle for a single action."""

    try:
        action_type = ActionType(action_key)
    except ValueError as exc:
        raise ValueError("Unsupported action key") from exc

    descriptor: ActionDescriptor = ACTION_REGISTRY[action_type]
    with transaction.atomic():
        perm, _created = AgentActionPermission.objects.get_or_create(
            agent_profile=agent,
            action_key=action_type.value,
            defaults={"is_enabled": enabled},
        )
        perm.is_enabled = enabled
        perm.save(update_fields=["is_enabled", "updated_at"])
    return AgentActionSetting(
        key=descriptor.key.value,
        label=descriptor.label,
        description=descriptor.description,
        enabled=enabled,
    )
