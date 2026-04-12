from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.accounts.models import BusinessProfile
from .records import CrmCompany, CrmContact


def _normalize_field_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower())
    return normalized.strip("_")


class CrmFieldTarget(models.TextChoices):
    CONTACT = "contact", "Contact"
    COMPANY = "company", "Company"


class CrmFieldType(models.TextChoices):
    TEXT = "text", "Text"
    LONG_TEXT = "long_text", "Long Text"
    NUMBER = "number", "Number"
    BOOLEAN = "boolean", "Boolean"
    DATE = "date", "Date"
    DATETIME = "datetime", "Datetime"
    SELECT = "select", "Select"
    MULTI_SELECT = "multi_select", "Multi Select"
    OBJECT = "object", "Object"
    OBJECT_LIST = "object_list", "Object List"


class CrmFieldDefinition(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(BusinessProfile, related_name="crm_field_definitions", on_delete=models.CASCADE)
    target_object = models.CharField(max_length=16, choices=CrmFieldTarget.choices)
    key = models.CharField(max_length=120)
    label = models.CharField(max_length=255)
    field_type = models.CharField(max_length=24, choices=CrmFieldType.choices)
    required = models.BooleanField(default=False)
    searchable = models.BooleanField(default=False)
    filterable = models.BooleanField(default=False)
    pii = models.BooleanField(default=False)
    archived = models.BooleanField(default=False)
    options = models.JSONField(default=list, blank=True)
    schema = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_field_definition"
        ordering = ("target_object", "label")
        constraints = [
            models.UniqueConstraint(fields=["business_profile", "target_object", "key"], name="crm_field_definition_unique_key"),
        ]
        indexes = [
            models.Index(fields=["business_profile", "target_object"], name="crm_fdef_biz_target_idx"),
            models.Index(fields=["business_profile", "archived"], name="crm_fdef_biz_arch_idx"),
        ]

    def save(self, *args, **kwargs):
        self.key = _normalize_field_key(self.key or self.label)
        super().save(*args, **kwargs)

    def clean(self):
        self.key = _normalize_field_key(self.key or self.label)
        self.label = (self.label or "").strip()
        if not self.key:
            raise ValidationError({"key": "Field key is required."})
        if not self.label:
            raise ValidationError({"label": "Field label is required."})
        if self.field_type in {CrmFieldType.SELECT, CrmFieldType.MULTI_SELECT}:
            options = _normalize_options(self.options)
            if not options:
                raise ValidationError({"options": "Select fields require at least one option."})
            self.options = options
        else:
            self.options = []
        if self.field_type in {CrmFieldType.OBJECT, CrmFieldType.OBJECT_LIST}:
            if not isinstance(self.schema, dict) or not self.schema:
                raise ValidationError({"schema": "Object fields require a schema definition."})
            _validate_schema_definition(self.schema)
        else:
            self.schema = {}


class CrmFieldValue(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(BusinessProfile, related_name="crm_field_values", on_delete=models.CASCADE)
    field_definition = models.ForeignKey(CrmFieldDefinition, related_name="values", on_delete=models.CASCADE)
    contact = models.ForeignKey(CrmContact, related_name="field_values", null=True, blank=True, on_delete=models.CASCADE)
    company = models.ForeignKey(CrmCompany, related_name="field_values", null=True, blank=True, on_delete=models.CASCADE)
    value_json = models.JSONField(null=True, blank=True)
    search_text = models.TextField(blank=True, default="")
    exact_text = models.CharField(max_length=255, blank=True, default="")
    number_value = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    boolean_value = models.BooleanField(null=True, blank=True)
    date_value = models.DateField(null=True, blank=True)
    datetime_value = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_field_value"
        ordering = ("field_definition__label", "created_at")
        constraints = [
            models.UniqueConstraint(
                fields=["field_definition", "contact"],
                condition=Q(contact__isnull=False),
                name="crm_field_value_unique_contact_field",
            ),
            models.UniqueConstraint(
                fields=["field_definition", "company"],
                condition=Q(company__isnull=False),
                name="crm_field_value_unique_company_field",
            ),
        ]
        indexes = [
            models.Index(fields=["business_profile", "field_definition"], name="crm_field_value_biz_field_idx"),
            models.Index(fields=["field_definition", "exact_text"], name="crm_field_value_exact_text_idx"),
            models.Index(fields=["field_definition", "number_value"], name="crm_field_value_number_idx"),
            models.Index(fields=["field_definition", "date_value"], name="crm_field_value_date_idx"),
            models.Index(fields=["field_definition", "datetime_value"], name="crm_field_value_datetime_idx"),
        ]

    def save(self, *args, **kwargs):
        field_type = self.field_definition.field_type if self.field_definition_id else ""
        definition = self.field_definition if self.field_definition_id else None
        value = self.value_json
        self.search_text = ""
        self.exact_text = ""
        self.number_value = None
        self.boolean_value = None
        self.date_value = None
        self.datetime_value = None

        if value is None:
            super().save(*args, **kwargs)
            return

        if definition is not None:
            value = coerce_field_value(definition, value)
            self.value_json = value

        if field_type in {CrmFieldType.TEXT, CrmFieldType.LONG_TEXT, CrmFieldType.SELECT}:
            text = str(value).strip()
            self.search_text = text
            self.exact_text = text[:255]
        elif field_type == CrmFieldType.MULTI_SELECT:
            items = [str(item).strip() for item in (value or []) if str(item).strip()]
            self.search_text = " ".join(items)
            self.exact_text = ",".join(items)[:255]
        elif field_type == CrmFieldType.NUMBER:
            try:
                self.number_value = Decimal(str(value))
                self.exact_text = str(value)[:255]
            except (InvalidOperation, TypeError, ValueError):
                self.number_value = None
        elif field_type == CrmFieldType.BOOLEAN:
            self.boolean_value = bool(value)
            self.exact_text = "true" if self.boolean_value else "false"
            self.search_text = self.exact_text
        elif field_type == CrmFieldType.DATE:
            if isinstance(value, str):
                parsed_date = parse_date(value)
                if parsed_date is not None:
                    self.date_value = parsed_date
            elif isinstance(value, date) and not isinstance(value, datetime):
                self.date_value = value
            if self.date_value is not None:
                self.exact_text = self.date_value.isoformat()
                self.search_text = self.exact_text
        elif field_type == CrmFieldType.DATETIME:
            if isinstance(value, str):
                parsed_datetime = parse_datetime(value)
                if parsed_datetime is not None:
                    if timezone.is_naive(parsed_datetime):
                        parsed_datetime = timezone.make_aware(parsed_datetime, timezone.get_current_timezone())
                    self.datetime_value = parsed_datetime
            elif isinstance(value, datetime):
                self.datetime_value = value if timezone.is_aware(value) else timezone.make_aware(value, timezone.get_current_timezone())
            if self.datetime_value is not None:
                self.exact_text = self.datetime_value.isoformat()[:255]
                self.search_text = self.exact_text
        else:
            self.search_text = _flatten_to_text(value)
            self.exact_text = self.search_text[:255]

        super().save(*args, **kwargs)


def _flatten_to_text(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, dict):
        return " ".join(_flatten_to_text(item) for item in value.values() if _flatten_to_text(item))
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_to_text(item) for item in value if _flatten_to_text(item))
    return str(value).strip()


def coerce_field_value(definition: CrmFieldDefinition, value: Any) -> Any:
    if value is None:
        return None

    field_type = definition.field_type
    if field_type in {CrmFieldType.TEXT, CrmFieldType.LONG_TEXT}:
        return str(value).strip()
    if field_type == CrmFieldType.SELECT:
        selected = str(value).strip()
        options = set(_normalize_options(definition.options))
        if selected not in options:
            raise ValidationError({definition.key: "Value must be one of the allowed options."})
        return selected
    if field_type == CrmFieldType.MULTI_SELECT:
        items = value
        if isinstance(items, str):
            items = [part.strip() for part in items.split(",")]
        if not isinstance(items, (list, tuple, set)):
            raise ValidationError({definition.key: "Multi-select fields require a list of values."})
        normalized = [str(item).strip() for item in items if str(item).strip()]
        allowed = set(_normalize_options(definition.options))
        invalid = [item for item in normalized if item not in allowed]
        if invalid:
            raise ValidationError({definition.key: f"Unsupported options: {', '.join(invalid)}"})
        return normalized
    if field_type == CrmFieldType.NUMBER:
        try:
            return str(Decimal(str(value)))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError({definition.key: "Field must be a valid number."}) from exc
    if field_type == CrmFieldType.BOOLEAN:
        return _coerce_boolean(definition.key, value)
    if field_type == CrmFieldType.DATE:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value.isoformat()
        parsed_date = parse_date(str(value).strip())
        if parsed_date is None:
            raise ValidationError({definition.key: "Field must be a valid ISO date."})
        return parsed_date.isoformat()
    if field_type == CrmFieldType.DATETIME:
        if isinstance(value, datetime):
            parsed_datetime = value
        else:
            parsed_datetime = parse_datetime(str(value).strip().replace("Z", "+00:00"))
        if parsed_datetime is None:
            raise ValidationError({definition.key: "Field must be a valid ISO datetime."})
        if timezone.is_naive(parsed_datetime):
            parsed_datetime = timezone.make_aware(parsed_datetime, timezone.get_current_timezone())
        return parsed_datetime.isoformat()
    if field_type == CrmFieldType.OBJECT:
        if not isinstance(value, dict):
            raise ValidationError({definition.key: "Field must be an object."})
        return _coerce_schema_value(value, definition.schema, field_key=definition.key)
    if field_type == CrmFieldType.OBJECT_LIST:
        if not isinstance(value, list):
            raise ValidationError({definition.key: "Field must be a list of objects."})
        return [_coerce_schema_value(item, definition.schema, field_key=definition.key) for item in value]
    raise ValidationError({definition.key: "Unsupported field type."})


def _normalize_options(options: Any) -> list[str]:
    if not isinstance(options, (list, tuple)):
        return []
    seen: set[str] = set()
    normalized: list[str] = []
    for option in options:
        text = str(option).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _coerce_boolean(field_key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off"}:
        return False
    raise ValidationError({field_key: "Field must be a valid boolean."})


def _validate_schema_definition(schema: dict[str, Any]) -> None:
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise ValidationError({"schema": "Schema must define non-empty properties."})
    for key, raw_spec in properties.items():
        if not _normalize_field_key(str(key)):
            raise ValidationError({"schema": "Schema property keys must be non-empty."})
        spec = _normalize_schema_spec(raw_spec)
        if spec["type"] in {CrmFieldType.SELECT, CrmFieldType.MULTI_SELECT} and not _normalize_options(spec.get("options")):
            raise ValidationError({"schema": f"Schema property '{key}' requires options."})
        if spec["type"] in {CrmFieldType.OBJECT, CrmFieldType.OBJECT_LIST}:
            nested_schema = spec.get("schema")
            if not isinstance(nested_schema, dict):
                raise ValidationError({"schema": f"Schema property '{key}' requires a nested schema."})
            _validate_schema_definition(nested_schema)


def _coerce_schema_value(value: dict[str, Any], schema: dict[str, Any], *, field_key: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError({field_key: "Field must be an object."})
    properties = schema.get("properties") or {}
    required_keys = {
        str(key)
        for key, raw_spec in properties.items()
        if bool((_normalize_schema_spec(raw_spec)).get("required"))
    }
    missing = sorted(key for key in required_keys if value.get(key) in (None, "", [], {}))
    if missing:
        raise ValidationError({field_key: f"Missing required schema properties: {', '.join(missing)}"})

    normalized: dict[str, Any] = {}
    for key, raw in value.items():
        if key not in properties:
            raise ValidationError({field_key: f"Unknown schema property '{key}'."})
        spec = _normalize_schema_spec(properties[key])
        normalized[key] = _coerce_schema_property_value(key, spec, raw, parent_key=field_key)
    return normalized


def _coerce_schema_property_value(key: str, spec: dict[str, Any], value: Any, *, parent_key: str) -> Any:
    field_name = f"{parent_key}.{key}"
    field_type = spec["type"]
    options = spec.get("options") or []
    schema = spec.get("schema") or {}

    if field_type in {CrmFieldType.TEXT, CrmFieldType.LONG_TEXT}:
        return str(value).strip()
    if field_type == CrmFieldType.SELECT:
        option = str(value).strip()
        if option not in set(_normalize_options(options)):
            raise ValidationError({parent_key: f"Invalid option for '{key}'."})
        return option
    if field_type == CrmFieldType.MULTI_SELECT:
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",")]
        if not isinstance(value, (list, tuple, set)):
            raise ValidationError({parent_key: f"'{key}' must be a list."})
        selected = [str(item).strip() for item in value if str(item).strip()]
        invalid = [item for item in selected if item not in set(_normalize_options(options))]
        if invalid:
            raise ValidationError({parent_key: f"Invalid values for '{key}': {', '.join(invalid)}"})
        return selected
    if field_type == CrmFieldType.NUMBER:
        try:
            return str(Decimal(str(value)))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError({parent_key: f"'{key}' must be numeric."}) from exc
    if field_type == CrmFieldType.BOOLEAN:
        return _coerce_boolean(field_name, value)
    if field_type == CrmFieldType.DATE:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value.isoformat()
        parsed_date = parse_date(str(value).strip())
        if parsed_date is None:
            raise ValidationError({parent_key: f"'{key}' must be an ISO date."})
        return parsed_date.isoformat()
    if field_type == CrmFieldType.DATETIME:
        if isinstance(value, datetime):
            parsed_datetime = value
        else:
            parsed_datetime = parse_datetime(str(value).strip().replace("Z", "+00:00"))
        if parsed_datetime is None:
            raise ValidationError({parent_key: f"'{key}' must be an ISO datetime."})
        if timezone.is_naive(parsed_datetime):
            parsed_datetime = timezone.make_aware(parsed_datetime, timezone.get_current_timezone())
        return parsed_datetime.isoformat()
    if field_type == CrmFieldType.OBJECT:
        return _coerce_schema_value(value, schema, field_key=field_name)
    if field_type == CrmFieldType.OBJECT_LIST:
        if not isinstance(value, list):
            raise ValidationError({parent_key: f"'{key}' must be a list of objects."})
        return [_coerce_schema_value(item, schema, field_key=field_name) for item in value]
    raise ValidationError({parent_key: f"Unsupported schema type for '{key}'."})


def _normalize_schema_spec(raw_spec: Any) -> dict[str, Any]:
    if isinstance(raw_spec, str):
        return {"type": raw_spec}
    if not isinstance(raw_spec, dict):
        raise ValidationError({"schema": "Schema properties must be strings or objects."})
    field_type = raw_spec.get("type")
    if field_type not in set(CrmFieldType.values):
        raise ValidationError({"schema": f"Unsupported schema field type '{field_type}'."})
    return {
        "type": field_type,
        "required": bool(raw_spec.get("required")),
        "options": raw_spec.get("options") or [],
        "schema": raw_spec.get("schema") or {},
    }
