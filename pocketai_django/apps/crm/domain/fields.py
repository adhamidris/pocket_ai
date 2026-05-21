from __future__ import annotations

from typing import Any, Sequence
import uuid

from django.core.exceptions import ValidationError

from apps.accounts.models import BusinessProfile, User
from apps.crm.domain.shared import _log_activity
from apps.crm.models import CrmActivityType, CrmFieldDefinition, CrmFieldTarget


def list_field_definitions(*, business_profile: BusinessProfile, target_object: str | None = None) -> Sequence[CrmFieldDefinition]:
    qs = CrmFieldDefinition.objects.filter(business_profile=business_profile, archived=False)
    if target_object:
        qs = qs.filter(target_object=target_object)
    return tuple(qs.order_by("label"))


def get_field_definition(*, business_profile: BusinessProfile, field_definition_id: uuid.UUID) -> CrmFieldDefinition:
    return CrmFieldDefinition.objects.get(business_profile=business_profile, id=field_definition_id)


def upsert_field_definition(*, business_profile: BusinessProfile, payload: dict[str, Any], actor: User | None = None) -> CrmFieldDefinition:
    target_object = payload.get("target_object") or payload.get("targetObject") or CrmFieldTarget.CONTACT
    key = payload.get("key") or payload.get("label") or ""
    definition, created = CrmFieldDefinition.objects.get_or_create(
        business_profile=business_profile,
        target_object=target_object,
        key=key,
        defaults={"label": str(payload.get("label") or key).strip(), "field_type": payload.get("field_type") or payload.get("fieldType") or ""},
    )
    definition.label = str(payload.get("label") or key).strip()
    definition.field_type = payload.get("field_type") or payload.get("fieldType")
    definition.required = bool(payload.get("required"))
    definition.searchable = bool(payload.get("searchable"))
    definition.filterable = bool(payload.get("filterable"))
    definition.pii = bool(payload.get("pii"))
    definition.options = list(payload.get("options") or [])
    definition.schema = payload.get("schema") if isinstance(payload.get("schema"), dict) else {}
    definition.full_clean()
    definition.save()
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.OTHER,
        "Field definition created" if created else "Field definition updated",
        detail=f"{definition.target_object}:{definition.key}",
    )
    return definition


def update_field_definition(*, business_profile: BusinessProfile, definition: CrmFieldDefinition, payload: dict[str, Any], actor: User | None = None) -> CrmFieldDefinition:
    if definition.business_profile_id != business_profile.id:
        raise ValidationError("Field definition belongs to a different business.")
    target_object = payload.get("target_object") or payload.get("targetObject")
    field_type = payload.get("field_type") or payload.get("fieldType")
    if target_object:
        definition.target_object = target_object
    if field_type:
        definition.field_type = field_type
    if "key" in payload:
        definition.key = str(payload.get("key") or "").strip()
    if "label" in payload:
        definition.label = str(payload.get("label") or "").strip()
    for flag in ("required", "searchable", "filterable", "pii"):
        if flag in payload:
            setattr(definition, flag, bool(payload.get(flag)))
    if "options" in payload:
        definition.options = list(payload.get("options") or [])
    if "schema" in payload:
        definition.schema = payload.get("schema") if isinstance(payload.get("schema"), dict) else {}
    definition.full_clean()
    definition.save()
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.OTHER,
        "Field definition updated",
        detail=f"{definition.target_object}:{definition.key}",
    )
    return definition


def archive_field_definition(*, business_profile: BusinessProfile, actor: User | None, definition: CrmFieldDefinition) -> CrmFieldDefinition:
    if definition.business_profile_id != business_profile.id:
        raise ValidationError("Field definition belongs to a different business.")
    if definition.archived:
        return definition
    definition.archived = True
    definition.save(update_fields=["archived", "updated_at"])
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.OTHER,
        "Field definition archived",
        detail=f"{definition.target_object}:{definition.key}",
    )
    return definition


def restore_field_definition(*, business_profile: BusinessProfile, actor: User | None, definition: CrmFieldDefinition) -> CrmFieldDefinition:
    if definition.business_profile_id != business_profile.id:
        raise ValidationError("Field definition belongs to a different business.")
    if not definition.archived:
        return definition
    definition.archived = False
    definition.full_clean()
    definition.save(update_fields=["archived", "updated_at"])
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.OTHER,
        "Field definition restored",
        detail=f"{definition.target_object}:{definition.key}",
    )
    return definition
