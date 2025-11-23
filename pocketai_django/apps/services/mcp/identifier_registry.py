from __future__ import annotations

import dataclasses
import hashlib
import re
import uuid
from typing import Iterable, Mapping, Sequence

from django.db.models import QuerySet
from django.utils import timezone

from apps.accounts.models import (
    BusinessProfile,
    IdentifierColumnMapping,
    IdentifierColumnMemory,
    IdentifierColumnStatus,
    IdentifierSchema,
    IdentifierSchemaSource,
    IdentifierSchemaStatus,
    KnowledgeUpload,
    KnowledgeUploadTable,
    _normalize_identifier_token,
)
from apps.conversations.models import Conversation, ConversationSender, IdentifierEvent, ConversationMessage
from apps.services.mcp.identifier_detection import ValueAwareIdentifierDetector


class IdentifierRegistryError(ValueError):
    """Raised when identifier registry operations fail validation."""


@dataclasses.dataclass(frozen=True)
class IdentifierGateDecision:
    status: str
    required_keys: tuple[str, ...]
    provided_keys: tuple[str, ...]
    provided_hashes: Mapping[str, str]
    blocked_uploads: tuple[str, ...]
    missing_by_upload: dict[str, tuple[str, ...]]
    hint: str | None = None
    match_policy: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "required_keys": list(self.required_keys),
            "provided_keys": list(self.provided_keys),
            "provided_hashes": dict(self.provided_hashes),
            "blocked_uploads": list(self.blocked_uploads),
            "missing_by_upload": {k: list(v) for k, v in self.missing_by_upload.items()},
            "hint": self.hint,
            "match_policy": self.match_policy,
        }


class IdentifierGuardrail:
    """
    Evaluate identifier requirements for MCP retrieval against a conversation.
    """

    def __init__(self, *, business_profile: BusinessProfile, provided_identifiers: Mapping[str, str]) -> None:
        self.business_profile = business_profile
        self.provided_identifiers, self.locked_identifier, self.identifier_conflict = self._normalize_provided(provided_identifiers)
        self.provided_hashes = {
            key: hash_identifier_value(value)
            for key, value in self.provided_identifiers.items()
        }
        self._mapping_index = self._build_mapping_index()

    @classmethod
    def from_conversation(cls, conversation: Conversation) -> "IdentifierGuardrail":
        identifiers, lock, conflict = cls._extract_identifiers(conversation)
        enriched = dict(identifiers)
        if lock:
            enriched["locked_identifier"] = lock
        if conflict:
            enriched["identifier_conflict"] = conflict
        return cls(
            business_profile=conversation.business_profile,
            provided_identifiers=enriched,
        )

    @staticmethod
    def _extract_identifiers(conversation: Conversation) -> dict[str, str]:
        """
        Collect customer identifiers from conversation metadata and linked customer record.
        """

        values: dict[str, str] = {}
        metadata = conversation.metadata if isinstance(conversation.metadata, Mapping) else {}
        locked_identifier = metadata.get("locked_identifier") if isinstance(metadata, Mapping) else None
        conflict = metadata.get("identifier_conflict") if isinstance(metadata, Mapping) else None

        meta_identifiers = metadata.get("customer_identifiers") or metadata.get("identifiers") or {}
        if isinstance(meta_identifiers, Mapping):
            for key, value in meta_identifiers.items():
                text = str(value).strip()
                if text:
                    normalized = _normalize_identifier_token(str(key))
                    values[normalized or str(key)] = text

        # Common fallbacks to capture prefilled portal identifiers.
        for key in ("email", "primary_email", "customer_email"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                values[_normalize_identifier_token("email")] = value.strip()
        for key in ("phone", "primary_phone", "customer_phone"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                values[_normalize_identifier_token("phone")] = value.strip()
        for key in ("customer_id", "customerId", "account_id", "accountId"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                values[_normalize_identifier_token("customer_id")] = value.strip()

        customer = conversation.customer
        if customer:
            if customer.primary_email:
                values[_normalize_identifier_token("email")] = customer.primary_email
            if customer.primary_phone:
                values[_normalize_identifier_token("phone")] = customer.primary_phone

        # Fallback: scan recent customer messages for identifiers if none captured yet.
        if not values:
            recent_msgs = (
                ConversationMessage.objects.filter(conversation=conversation, sender=ConversationSender.CUSTOMER)
                .order_by("-sent_at", "-created_at")[:4]
            )
            for msg in recent_msgs:
                body = msg.body or ""
                for email in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", body):
                    if _normalize_identifier_token("email") not in values:
                        values[_normalize_identifier_token("email")] = email
                for phone_raw in re.findall(r"(\+?\d[\d\s\-\(\)]{7,})", body):
                    digits = re.sub(r"\D", "", phone_raw)
                    if 10 <= len(digits) <= 15 and _normalize_identifier_token("phone") not in values:
                        values[_normalize_identifier_token("phone")] = phone_raw
                for match in re.finditer(r"(?i)(ticket|case|order|customer|account|user|external)\s*(?:id|number|no|#)?\s*[:\-]?\s*([A-Za-z0-9\-_]+)", body):
                    label = (match.group(1) or "").lower()
                    value = match.group(2) or ""
                    if not value:
                        continue
                    if label in {"ticket", "case", "order", "external"} and _normalize_identifier_token("external_id") not in values:
                        values[_normalize_identifier_token("external_id")] = value
                    elif label in {"customer", "account", "user"} and _normalize_identifier_token("customer_id") not in values:
                        values[_normalize_identifier_token("customer_id")] = value

            # Persist extracted identifiers back onto the conversation for subsequent turns.
            if values:
                existing_meta = metadata if isinstance(metadata, dict) else {}
                updated_meta = dict(existing_meta)
                updated_identifiers = dict(existing_meta.get("customer_identifiers") or {})
                for key, value in values.items():
                    if key not in updated_identifiers:
                        updated_identifiers[key] = value
                if updated_identifiers != existing_meta.get("customer_identifiers"):
                    updated_meta["customer_identifiers"] = updated_identifiers
                    Conversation.objects.filter(id=conversation.id).update(metadata=updated_meta)

        return values, locked_identifier, conflict

    @staticmethod
    def _normalize_provided(provided_identifiers: Mapping[str, str]) -> tuple[dict[str, str], dict | None, dict | None]:
        """
        Normalize identifiers and enforce session lock rules.
        """

        values: dict[str, str] = {}
        lock: dict | None = None
        conflict: dict | None = None

        # provided_identifiers is already normalized from _extract_identifiers; respect locked_identifier if present.
        if isinstance(provided_identifiers, Mapping):
            lock = provided_identifiers.get("locked_identifier") if isinstance(provided_identifiers.get("locked_identifier"), dict) else None
        if isinstance(provided_identifiers, Mapping):
            conflict = provided_identifiers.get("identifier_conflict") if isinstance(provided_identifiers.get("identifier_conflict"), dict) else None
        for key, value in (provided_identifiers or {}).items():
            if key in {"locked_identifier", "identifier_conflict"}:
                continue
            text = str(value).strip()
            if not text:
                continue
            normalized = _normalize_identifier_token(key) or key
            if lock and isinstance(lock, Mapping):
                locked_key = _normalize_identifier_token(lock.get("key") or lock.get("name") or "")
                locked_value = str(lock.get("value") or "").strip()
                if locked_key and normalized == locked_key:
                    values[locked_key] = locked_value
                    continue
            if normalized not in values:
                values[normalized] = text
        if lock and isinstance(lock, Mapping):
            locked_key = _normalize_identifier_token(lock.get("key") or lock.get("name") or "")
            locked_value = str(lock.get("value") or "").strip()
            if locked_key and locked_value:
                # Enforce lock: override provided set to locked only.
                values = {locked_key: locked_value}
        return values, lock, conflict

    @property
    def match_policy(self) -> str:
        from .identifier_registry import IdentifierRegistryService  # local import to avoid circularity during type-checking

        return IdentifierRegistryService.get_match_policy(self.business_profile)

    def snapshot(self) -> dict[str, object]:
        """Lightweight view for diagnostics."""

        active_uploads = sorted(k for k in self._mapping_index.keys() if k != "*")
        active_keys = sorted(self._all_required_keys())
        return {
            "provided_keys": sorted(self.provided_identifiers.keys()),
            "active_keys": active_keys,
            "uploads": active_uploads,
            "match_policy": self.match_policy,
            "provided_hashes": dict(self.provided_hashes),
        }

    def _build_mapping_index(self) -> dict[str, set[str]]:
        """
        Build a cache of required identifier keys keyed by upload_id (string).
        """

        mapping_index: dict[str, set[str]] = {}
        qs: QuerySet[IdentifierColumnMapping] = IdentifierColumnMapping.objects.select_related("identifier").filter(
            business_profile=self.business_profile,
            status=IdentifierColumnStatus.ACTIVE,
            identifier__status=IdentifierSchemaStatus.ACTIVE,
            identifier__is_required=True,
        )
        for mapping in qs:
            key = mapping.identifier.key
            upload_id = str(mapping.upload_id) if mapping.upload_id else "*"
            bucket = mapping_index.setdefault(upload_id, set())
            bucket.add(key)
        return mapping_index

    def _all_required_keys(self) -> set[str]:
        all_keys: set[str] = set()
        for keys in self._mapping_index.values():
            all_keys.update(keys)
        return all_keys

    def requirements_snapshot(self) -> dict[str, object]:
        """
        Summarize currently required/provided identifiers for prompt/context use.
        """

        required = tuple(sorted(self._all_required_keys()))
        provided = tuple(sorted(self.provided_identifiers.keys()))
        missing = tuple(sorted(set(required) - set(provided)))
        return {
            "match_policy": self.match_policy,
            "required": required,
            "provided": provided,
            "missing": missing,
            "locked_identifier": self.locked_identifier or {},
            "identifier_conflict": self.identifier_conflict or {},
        }

    def _required_keys_for_upload(self, upload_id: str | None) -> set[str]:
        keys: set[str] = set()
        keys.update(self._mapping_index.get("*", set()))
        if upload_id and upload_id in self._mapping_index:
            keys.update(self._mapping_index[upload_id])
        return keys

    def _hint(self, required_keys: Iterable[str]) -> str:
        keys = [key for key in required_keys if key]
        if not keys:
            return "Share a customer identifier to continue."
        formatted = ", ".join(sorted(keys))
        if self.match_policy == "and":
            return f"Share all of these identifiers to continue: {formatted}."
        return f"Share one of these identifiers to continue: {formatted}."

    def evaluate_snippets(self, snippets: Sequence[Mapping[str, object]]) -> IdentifierGateDecision:
        upload_ids = set()
        for snippet in snippets:
            upload_id = snippet.get("upload_id")
            if upload_id:
                upload_ids.add(str(upload_id))
        return self.require_for_uploads(upload_ids)

    def require_for_uploads(self, upload_ids: Iterable[str]) -> IdentifierGateDecision:
        if self.identifier_conflict and self.locked_identifier:
            locked_key = _normalize_identifier_token(self.locked_identifier.get("key") or "") or "identifier"
            hint = (
                f"Session is locked to the first {locked_key}. This session cannot switch identifiers."
            )
            upload_list = list(upload_ids)
            return IdentifierGateDecision(
                status="identifier_conflict",
                required_keys=tuple(sorted(self._all_required_keys())),
                provided_keys=tuple(sorted(self.provided_identifiers.keys())),
                provided_hashes=self.provided_hashes,
                blocked_uploads=tuple(sorted(upload_list)),
                missing_by_upload={uid: tuple(sorted(self._required_keys_for_upload(uid))) for uid in upload_list},
                hint=hint,
                match_policy=self.match_policy,
            )
        blocked: set[str] = set()
        missing_by_upload: dict[str, tuple[str, ...]] = {}
        required: set[str] = set()
        provided = set(self.provided_identifiers.keys())
        for upload_id in upload_ids:
            required_keys = self._required_keys_for_upload(upload_id)
            if not required_keys:
                continue
            required.update(required_keys)
            if self.match_policy == "and":
                missing = required_keys.difference(provided)
                if missing:
                    blocked.add(upload_id)
                    missing_by_upload[upload_id] = tuple(sorted(required_keys))
                    continue
            elif required_keys.isdisjoint(provided):
                blocked.add(upload_id)
                missing_by_upload[upload_id] = tuple(sorted(required_keys))
        if blocked:
            return IdentifierGateDecision(
                status="identifier_required",
                required_keys=tuple(sorted(required)),
                provided_keys=tuple(sorted(self.provided_identifiers.keys())),
                provided_hashes=self.provided_hashes,
                blocked_uploads=tuple(sorted(blocked)),
                missing_by_upload=missing_by_upload,
                hint=self._hint(required),
                match_policy=self.match_policy,
            )
        return IdentifierGateDecision(
            status="ok",
            required_keys=tuple(sorted(required)),
            provided_keys=tuple(sorted(self.provided_identifiers.keys())),
            provided_hashes=self.provided_hashes,
            blocked_uploads=tuple(),
            missing_by_upload={},
            hint=None,
            match_policy=self.match_policy,
        )

    def require_for_upload(self, upload_id: str | None) -> IdentifierGateDecision:
        if not upload_id:
            return IdentifierGateDecision(
                status="ok",
                required_keys=tuple(),
                provided_keys=tuple(sorted(self.provided_identifiers.keys())),
                blocked_uploads=tuple(),
                missing_by_upload={},
                hint=None,
            )
        return self.require_for_uploads((str(upload_id),))


class IdentifierRegistryService:
    """
    Lightweight CRUD helper for MCP identifier registry (schemas + column mappings).
    """

    MATCH_POLICY_KEY = "identifier_match_policy"
    HIGH_CONFIDENCE_THRESHOLD = 0.75

    @staticmethod
    def normalize_match_policy(value: str | None) -> str:
        if not value:
            return "or"
        lowered = str(value).strip().lower()
        return "and" if lowered == "and" else "or"

    @classmethod
    def set_match_policy(cls, *, business_profile: BusinessProfile, policy: str) -> str:
        normalized = cls.normalize_match_policy(policy)
        metadata = business_profile.metadata if isinstance(business_profile.metadata, dict) else {}
        current = metadata.get(cls.MATCH_POLICY_KEY)
        if current == normalized:
            return normalized
        updated = dict(metadata)
        updated[cls.MATCH_POLICY_KEY] = normalized
        business_profile.metadata = updated
        business_profile.save(update_fields=["metadata"])
        return normalized

    @classmethod
    def get_match_policy(cls, business_profile: BusinessProfile) -> str:
        metadata = business_profile.metadata if isinstance(business_profile.metadata, dict) else {}
        raw = metadata.get(cls.MATCH_POLICY_KEY)
        return cls.normalize_match_policy(raw)

    @staticmethod
    def _headers_from_upload(upload: KnowledgeUpload) -> list[str]:
        """
        Try to derive column headers from stored table schema for the upload.
        """

        table = (
            KnowledgeUploadTable.objects.filter(upload=upload)
            .only("column_schema")
            .order_by("-created_at")
            .first()
        )
        if not table or not isinstance(table.column_schema, list):
            return []
        headers: list[str] = []
        for entry in table.column_schema:
            if isinstance(entry, str) and entry.strip():
                headers.append(entry.strip())
            elif isinstance(entry, Mapping):
                # Some schemas may use dict entries with "label"/"name".
                label = entry.get("label") or entry.get("name")
                if isinstance(label, str) and label.strip():
                    headers.append(label.strip())
        return headers

    @staticmethod
    def serialize_schema(schema: IdentifierSchema) -> dict[str, object]:
        return {
            "id": str(schema.id),
            "business_id": str(schema.business_profile_id),
            "key": schema.key,
            "display_name": schema.display_name,
            "status": schema.status,
            "source": schema.source,
            "is_required": schema.is_required,
            "description": schema.description,
            "metadata": schema.metadata or {},
            "columns": [
                {
                    "id": str(col.id),
                    "upload_id": str(col.upload_id) if col.upload_id else None,
                    "sheet_name": col.sheet_name,
                    "column_name": col.column_name,
                    "column_normalized": col.column_normalized,
                    "status": col.status,
                    "source": col.source,
                    "confidence": col.confidence,
                    "metadata": col.metadata or {},
                    "created_at": col.created_at.isoformat(),
                    "updated_at": col.updated_at.isoformat(),
                }
                for col in schema.column_mappings.all()
            ],
            "created_at": schema.created_at.isoformat(),
            "updated_at": schema.updated_at.isoformat(),
        }

    @classmethod
    def list_registry(cls, *, business_profile: BusinessProfile) -> list[dict[str, object]]:
        schemas = IdentifierSchema.objects.filter(business_profile=business_profile).prefetch_related("column_mappings")
        return [cls.serialize_schema(schema) for schema in schemas]

    @classmethod
    def get_schema(cls, *, business_profile: BusinessProfile, schema_id: uuid.UUID) -> IdentifierSchema | None:
        return IdentifierSchema.objects.filter(business_profile=business_profile, id=schema_id).prefetch_related("column_mappings").first()

    @classmethod
    def create_schema(
        cls,
        *,
        business_profile: BusinessProfile,
        key: str,
        display_name: str | None = None,
        source: str | None = None,
        status: str | None = None,
        is_required: bool = True,
        description: str = "",
        metadata: Mapping[str, object] | None = None,
        columns: Sequence[Mapping[str, object]] | None = None,
    ) -> IdentifierSchema:
        normalized_key = _normalize_identifier_token(key)
        if not normalized_key:
            raise IdentifierRegistryError("Identifier key is required.")
        source_value = source or IdentifierSchemaSource.USER
        if source_value not in IdentifierSchemaSource.values:
            raise IdentifierRegistryError("Invalid source; must be one of user|ai.")
        status_value = status or IdentifierSchemaStatus.PROPOSED
        if status_value not in IdentifierSchemaStatus.values:
            raise IdentifierRegistryError("Invalid status; must be proposed|active|disabled.")
        schema = IdentifierSchema.objects.create(
            business_profile=business_profile,
            key=normalized_key,
            display_name=display_name or key,
            source=source_value,
            status=status_value,
            is_required=is_required,
            description=description,
            metadata=metadata or {},
        )
        if columns:
            cls.add_columns(schema=schema, columns=columns)
        return schema

    @classmethod
    def approve_schema(cls, schema: IdentifierSchema) -> IdentifierSchema:
        schema.status = IdentifierSchemaStatus.ACTIVE
        schema.save(update_fields=["status", "updated_at"])
        # Promote proposed column mappings alongside the schema to avoid UI confusion.
        schema.column_mappings.filter(status=IdentifierColumnStatus.PROPOSED).update(
            status=IdentifierColumnStatus.ACTIVE,
            updated_at=timezone.now(),
        )
        cls._remember_active_mappings(schema)
        return schema

    @classmethod
    def reject_schema(cls, schema: IdentifierSchema) -> IdentifierSchema:
        """
        Mark a schema as disabled and disable its column mappings.
        """

        schema.status = IdentifierSchemaStatus.DISABLED
        schema.save(update_fields=["status", "updated_at"])
        schema.column_mappings.exclude(status=IdentifierColumnStatus.DISABLED).update(
            status=IdentifierColumnStatus.DISABLED,
            updated_at=timezone.now(),
        )
        return schema

    @classmethod
    def propose_from_headers(
        cls,
        *,
        business_profile: BusinessProfile,
        headers: Sequence[str] | None,
        upload: KnowledgeUpload | None = None,
        auto_promote: bool = False,
        match_policy: str | None = None,
        model_assist: bool = False,
    ) -> list[IdentifierSchema]:
        header_list: list[str] = []
        for item in headers or []:
            if isinstance(item, str) and item.strip():
                header_list.append(item.strip())
        if not header_list:
            if upload:
                header_list = cls._headers_from_upload(upload)
            if not header_list:
                # Fall back to common identifier keys so admins can propose without typing headers.
                header_list = list(ValueAwareIdentifierDetector.HEADER_ALIASES.keys())

        if match_policy:
            cls.set_match_policy(business_profile=business_profile, policy=match_policy)

        detector = ValueAwareIdentifierDetector(model_assist=model_assist)
        proposals = detector.detect(upload=upload, headers=header_list)
        if not proposals:
            return []

        memory_by_sig, memory_by_column = cls._load_memory(business_profile)
        enriched: list[dict[str, object]] = []
        for proposal in proposals:
            column_norm = _normalize_identifier_token(proposal["column_name"]) or proposal["column_name"]
            proposal["column_normalized"] = column_norm
            enriched.append(
                cls._apply_memory_bias(
                    proposal=proposal,
                    memory_by_sig=memory_by_sig,
                    memory_by_column=memory_by_column,
                )
            )
        proposals = enriched

        created_or_updated: list[IdentifierSchema] = []
        for proposal in proposals:
            key = proposal["key"]
            display_name = proposal["display_name"]
            confidence = proposal.get("confidence")
            schema, _created = IdentifierSchema.objects.get_or_create(
                business_profile=business_profile,
                key=key,
                defaults={
                    "display_name": display_name,
                    "status": IdentifierSchemaStatus.ACTIVE if auto_promote else IdentifierSchemaStatus.PROPOSED,
                    "source": IdentifierSchemaSource.AI,
                    "is_required": True,
                    "description": "Auto-detected from upload headers.",
                },
            )
            if not _created and auto_promote and schema.status != IdentifierSchemaStatus.ACTIVE:
                schema.status = IdentifierSchemaStatus.ACTIVE
                schema.save(update_fields=["status", "updated_at"])

            should_promote = auto_promote or (
                schema.status == IdentifierSchemaStatus.ACTIVE
                and (confidence or 0.0) >= cls.HIGH_CONFIDENCE_THRESHOLD
            )
            column_status = IdentifierColumnStatus.ACTIVE if should_promote else IdentifierColumnStatus.PROPOSED
            if upload:
                column_norm = proposal.get("column_normalized") or _normalize_identifier_token(proposal["column_name"]) or proposal["column_name"]
                metadata = {
                    "detected": True,
                    "detection_source": proposal.get("source"),
                    "diagnostics": proposal.get("diagnostics") or {},
                    "memory_applied": bool(proposal.get("diagnostics", {}).get("memory_match")),
                }
                mapping, _created_mapping = IdentifierColumnMapping.objects.update_or_create(
                    business_profile=business_profile,
                    identifier=schema,
                    upload=upload,
                    column_normalized=column_norm,
                    defaults={
                        "column_name": proposal["column_name"],
                        "sheet_name": proposal.get("sheet_name") or "",
                        "status": column_status,
                        "source": IdentifierSchemaSource.AI,
                        "confidence": confidence,
                        "metadata": metadata,
                    },
                )
                if schema.status == IdentifierSchemaStatus.ACTIVE and should_promote:
                    schema.column_mappings.filter(
                        upload=upload,
                        column_normalized=column_norm,
                        status=IdentifierColumnStatus.PROPOSED,
                    ).update(status=IdentifierColumnStatus.ACTIVE, updated_at=timezone.now())
                    mapping.status = IdentifierColumnStatus.ACTIVE
                if mapping.status == IdentifierColumnStatus.ACTIVE:
                    cls._remember_mapping(mapping=mapping, proposal=proposal)
            created_or_updated.append(schema)

        if upload and proposals:
            detection_payload = [
                {
                    "key": proposal.get("key"),
                    "column_name": proposal.get("column_name"),
                    "sheet_name": proposal.get("sheet_name"),
                    "confidence": proposal.get("confidence"),
                    "source": proposal.get("source"),
                    "diagnostics": proposal.get("diagnostics") or {},
                }
                for proposal in proposals
            ]
            cls.record_event(
                business_profile=business_profile,
                decision={
                    "status": "ok",
                    "required_keys": [],
                    "provided_keys": [],
                    "provided_hashes": {},
                    "blocked_uploads": [],
                    "missing_by_upload": {},
                    "match_policy": cls.get_match_policy(business_profile),
                },
                tool="identifier_detection",
                upload_ids=[str(upload.id)],
                metadata={
                    "detection": detection_payload,
                    "headers": header_list,
                    "model_assist": model_assist,
                },
            )
        return created_or_updated

    @classmethod
    def add_columns(cls, *, schema: IdentifierSchema, columns: Sequence[Mapping[str, object]]) -> IdentifierSchema:
        uploads: dict[str, KnowledgeUpload] = {}
        for column in columns:
            column_name = str(column.get("column_name") or column.get("name") or "").strip()
            if not column_name:
                raise IdentifierRegistryError("column_name is required for each column entry.")
            upload_id = column.get("upload_id") or column.get("uploadId")
            upload_obj: KnowledgeUpload | None = None
            if upload_id:
                try:
                    upload_uuid = upload_id if isinstance(upload_id, uuid.UUID) else uuid.UUID(str(upload_id))
                except (TypeError, ValueError) as exc:
                    raise IdentifierRegistryError("upload_id must be a valid UUID.") from exc
                if str(upload_uuid) in uploads:
                    upload_obj = uploads[str(upload_uuid)]
                else:
                    upload_obj = KnowledgeUpload.objects.filter(id=upload_uuid, business_profile=schema.business_profile).first()
                    if not upload_obj:
                        raise IdentifierRegistryError("Upload not found for this business.")
                    uploads[str(upload_uuid)] = upload_obj
            status_value = column.get("status") or IdentifierColumnStatus.PROPOSED
            if status_value not in IdentifierColumnStatus.values:
                raise IdentifierRegistryError("Invalid column status; must be proposed|active|disabled.")
            source_value = column.get("source") or schema.source
            if source_value not in IdentifierSchemaSource.values:
                raise IdentifierRegistryError("Invalid column source; must be user|ai.")
            sheet_name = str(column.get("sheet_name") or column.get("sheetName") or "").strip()
            mapping, _created = IdentifierColumnMapping.objects.update_or_create(
                business_profile=schema.business_profile,
                identifier=schema,
                upload=upload_obj,
                column_normalized=_normalize_identifier_token(column_name) or column_name,
                sheet_name=sheet_name,
                defaults={
                    "column_name": column_name,
                    "status": status_value,
                    "source": source_value,
                    "confidence": column.get("confidence"),
                    "metadata": column.get("metadata") or {},
                },
            )
            if mapping.status == IdentifierColumnStatus.ACTIVE:
                cls._remember_mapping(mapping=mapping, proposal=None)
        # Refresh columns for serialization
        schema.refresh_from_db()
        schema.column_mappings.all()  # prime cache
        return schema

    @staticmethod
    def _pattern_signature(normalized_column: str, diagnostics: Mapping[str, object] | None) -> str:
        diag = diagnostics if isinstance(diagnostics, Mapping) else {}
        parts: list[str] = []
        for key in ("unique_ratio", "email_ratio", "phone_ratio", "prefix_ratio", "mixed_id_ratio", "numeric_id_ratio"):
            value = diag.get(key)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            parts.append(f"{key}={round(number, 3)}")
        source_hint = diag.get("match_type") or diag.get("source")
        if isinstance(source_hint, str) and source_hint:
            parts.append(f"src={source_hint}")
        signature = "|".join(parts) if parts else ""
        if not signature:
            return "none"
        return signature[:250]

    @classmethod
    def _load_memory(cls, business_profile: BusinessProfile) -> tuple[dict[tuple[str, str], IdentifierColumnMemory], dict[str, IdentifierColumnMemory]]:
        memories = IdentifierColumnMemory.objects.filter(business_profile=business_profile)
        by_signature: dict[tuple[str, str], IdentifierColumnMemory] = {}
        by_column: dict[str, IdentifierColumnMemory] = {}
        for memory in memories:
            key = (memory.normalized_column, memory.pattern_signature or "none")
            by_signature[key] = memory
            if memory.normalized_column not in by_column:
                by_column[memory.normalized_column] = memory
        return by_signature, by_column

    @classmethod
    def _apply_memory_bias(
        cls,
        *,
        proposal: dict[str, object],
        memory_by_sig: Mapping[tuple[str, str], IdentifierColumnMemory],
        memory_by_column: Mapping[str, IdentifierColumnMemory],
    ) -> dict[str, object]:
        normalized = proposal.get("column_normalized") or _normalize_identifier_token(proposal.get("column_name") or "") or proposal.get("column_name") or ""
        diagnostics = proposal.get("diagnostics") if isinstance(proposal.get("diagnostics"), Mapping) else {}
        signature = cls._pattern_signature(normalized, diagnostics)
        memory = memory_by_sig.get((normalized, signature)) or memory_by_column.get(normalized)
        if not memory:
            return proposal

        new_confidence = proposal.get("confidence") or 0.0
        new_confidence = max(new_confidence, (memory.last_confidence or 0.0))
        new_confidence = min(1.0, new_confidence + 0.15)

        updated_diag = dict(diagnostics or {})
        updated_diag["memory_match"] = True
        updated_diag["memory_identifier"] = memory.identifier_key

        return {
            **proposal,
            "key": memory.identifier_key,
            "display_name": memory.identifier_schema.display_name if memory.identifier_schema else proposal.get("display_name") or memory.identifier_key,
            "confidence": new_confidence,
            "diagnostics": updated_diag,
        }

    @classmethod
    def _remember_mapping(cls, *, mapping: IdentifierColumnMapping, proposal: Mapping[str, object] | None) -> None:
        diagnostics = {}
        if isinstance(proposal, Mapping):
            diagnostics = proposal.get("diagnostics") or {}
        elif isinstance(mapping.metadata, Mapping):
            diagnostics = mapping.metadata.get("diagnostics") or {}
        signature = cls._pattern_signature(mapping.column_normalized, diagnostics)
        metadata = {"source": mapping.source}
        if diagnostics:
            metadata["diagnostics"] = diagnostics
        try:
            IdentifierColumnMemory.objects.update_or_create(
                business_profile=mapping.business_profile,
                normalized_column=mapping.column_normalized,
                pattern_signature=signature,
                defaults={
                    "identifier_schema": mapping.identifier,
                    "identifier_key": mapping.identifier.key,
                    "last_confidence": mapping.confidence,
                    "metadata": metadata,
                },
            )
        except Exception:
            # Memory should never block the flow.
            return

    @classmethod
    def _remember_active_mappings(cls, schema: IdentifierSchema) -> None:
        for mapping in schema.column_mappings.filter(status=IdentifierColumnStatus.ACTIVE):
            cls._remember_mapping(mapping=mapping, proposal=None)

    @staticmethod
    def record_event(
        *,
        business_profile: BusinessProfile,
        decision,
        tool: str,
        conversation: Conversation | None = None,
        upload_ids: Sequence[str] | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        if not decision:
            return
        if isinstance(decision, Mapping):
            payload = dict(decision)
        else:
            payload = getattr(decision, "as_dict", lambda: None)()
        if not payload:
            return
        upload_id = None
        uploads = list(upload_ids or [])
        if uploads:
            upload_id = uploads[0]
        metadata_payload: dict[str, object] = {"upload_ids": uploads}
        if isinstance(metadata, Mapping):
            metadata_payload.update(metadata)
        try:
            IdentifierEvent.objects.create(
                business_profile=business_profile,
                conversation=conversation,
                upload_id=upload_id,
                tool=tool,
                status=payload.get("status") or "ok",
                match_policy=payload.get("match_policy") or "or",
                required_keys=payload.get("required_keys") or [],
                provided_keys=payload.get("provided_keys") or [],
                provided_hashes=payload.get("provided_hashes") or {},
                blocked_uploads=payload.get("blocked_uploads") or [],
                missing_by_upload=payload.get("missing_by_upload") or {},
                metadata=metadata_payload,
            )
        except Exception:
            # Observability should never break the request flow.
            return


class IdentifierDetector:
    """
    Header-only heuristic detector for common identifiers.
    """

    COMMON_KEYS = {
        "email": {"email", "e-mail", "mail", "primary_email", "work_email", "contact_email", "applicant_email"},
        "phone": {"phone", "mobile", "cell", "contact_number", "phone_number", "tel", "mobile_number"},
        "customer_id": {"customer_id", "customerid", "cust_id", "custid", "account_id", "accountid", "user_id", "userid", "id", "applicant_id"},
        "external_id": {"external_id", "externalid", "reference", "ref_id", "refid", "ticket_id", "case_id", "order_id", "incident_id"},
    }

    DISPLAY_NAMES = {
        "email": "Email",
        "phone": "Phone",
        "customer_id": "Customer ID",
        "external_id": "External ID",
    }

    def detect(self, headers: Sequence[str]) -> list[dict[str, object]]:
        proposals: list[dict[str, object]] = []
        seen_keys: set[str] = set()
        for header in headers:
            raw = (header or "").strip()
            if not raw:
                continue
            normalized = _normalize_identifier_token(raw)
            if not normalized:
                continue
            match = self._match_key(normalized, raw)
            if not match:
                continue
            key, confidence = match
            if key in seen_keys:
                continue
            seen_keys.add(key)
            proposals.append(
                {
                    "key": key,
                    "display_name": self.DISPLAY_NAMES.get(key, key.title()),
                    "column_name": raw,
                    "confidence": confidence,
                }
            )
        return proposals[:4]

    def _match_key(self, normalized: str, raw: str) -> tuple[str, float] | None:
        # Exact/alias match
        for key, aliases in self.COMMON_KEYS.items():
            if normalized in aliases or raw.lower() in aliases:
                return key, 0.9

        # Pattern-based hints
        lowered = raw.lower()
        if "email" in lowered or re.search(r"mail", lowered):
            return "email", 0.7
        if "phone" in lowered or re.search(r"(cell|mobile|tel)", lowered):
            return "phone", 0.7
        if re.search(r"(customer|account|user).*id", lowered):
            return "customer_id", 0.65
        if re.search(r"(ticket|case|order|incident).*id", lowered):
            return "external_id", 0.7
        if lowered.endswith("id") or lowered == "id":
            return "customer_id", 0.6
        return None


def hash_identifier_value(value: str) -> str:
    """
    Hash PII (email/phone/id) before persisting in diagnostics.
    """

    text = (value or "").strip().lower()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
