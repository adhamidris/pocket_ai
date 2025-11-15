from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import requests
from django.conf import settings
from django.db import connection
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import (
    IntegrationCredentialEventType,
    IntegrationResourceConfig,
    KnowledgeIntegration,
    KnowledgeIntegrationStatus,
    KnowledgeIntegrationType,
    KnowledgeSourceType,
    KnowledgeStatus,
    KnowledgeUpload,
    KnowledgeUploadFile,
)
from apps.services.integrations.google_drive import GoogleOAuthError, maybe_refresh_google_credentials
from apps.services.knowledge_ingestion import queue_ingestion_job

logger = logging.getLogger(__name__)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class IntegrationSyncError(RuntimeError):
    """Base error for integration sync failures."""


class IntegrationCredentialsError(IntegrationSyncError):
    """Raised when credentials are missing or invalid."""


@dataclass
class ResourceSyncOutcome:
    resource_id: str
    status: str
    upload_id: str | None = None
    bytes_written: int = 0
    job_id: str | None = None
    message: str = ""
    rows_ingested: int = 0


@dataclass
class IntegrationSyncResult:
    integration_id: str
    provider: str
    resources: list[ResourceSyncOutcome] = field(default_factory=list)
    status: str = "completed"
    message: str = ""
    rows_ingested: int = 0

    @property
    def success_count(self) -> int:
        return sum(1 for outcome in self.resources if outcome.status in {"success", "unchanged"})

    @property
    def failure_count(self) -> int:
        return sum(1 for outcome in self.resources if outcome.status == "failed")


@dataclass
class ExportedSheet:
    content: bytes
    filename: str
    content_type: str
    extension: str


class IntegrationSyncService:
    """Downloads configured integration resources and queues ingestion jobs."""

    def __init__(self, *, storage_root: Path | None = None):
        root = Path(storage_root or getattr(settings, "MEDIA_ROOT", settings.BASE_DIR / "var" / "media"))
        root.mkdir(parents=True, exist_ok=True)
        self.storage_root = root

    def sync_integrations(
        self,
        integrations: Iterable[KnowledgeIntegration],
        *,
        resource_ids: Sequence[str] | None = None,
        ignore_schedule: bool = False,
    ) -> list[IntegrationSyncResult]:
        results: list[IntegrationSyncResult] = []
        for integration in integrations:
            if not ignore_schedule and not resource_ids and not integration.due_for_sync():
                logger.info(
                    "integration_sync_skipped integration=%s business=%s reason=not_due",
                    integration.id,
                    integration.business_profile_id,
                )
                results.append(
                    IntegrationSyncResult(
                        integration_id=str(integration.id),
                        provider=integration.integration_type,
                        status="skipped",
                        message="scheduled_later",
                    )
                )
                continue
            try:
                result = self.sync_integration(integration, resource_ids=resource_ids)
            except IntegrationCredentialsError as exc:
                integration.register_credential_failure(reason=str(exc))
                integration.sync_error = str(exc)
                if integration.status != KnowledgeIntegrationStatus.DISCONNECTED:
                    integration.status = KnowledgeIntegrationStatus.ERROR
                integration.record_sync_schedule(started_at=timezone.now(), status="error")
                integration.save(
                    update_fields=[
                        "status",
                        "sync_error",
                        "credential_error_count",
                        "metadata",
                        "updated_at",
                    ]
                )
                results.append(
                    IntegrationSyncResult(
                        integration_id=str(integration.id),
                        provider=integration.integration_type,
                        resources=[
                            ResourceSyncOutcome(
                                resource_id="*",
                                status="failed",
                                message=str(exc),
                            )
                        ],
                    )
                )
                continue
            except IntegrationSyncError as exc:
                logger.error(
                    "integration_sync_failed integration=%s business=%s error=%s",
                    integration.id,
                    integration.business_profile_id,
                    exc,
                )
                integration.status = KnowledgeIntegrationStatus.ERROR
                integration.sync_error = str(exc)
                integration.record_sync_schedule(started_at=timezone.now(), status="error")
                integration.save(update_fields=["status", "sync_error", "metadata", "updated_at"])
                results.append(
                    IntegrationSyncResult(
                        integration_id=str(integration.id),
                        provider=integration.integration_type,
                        resources=[
                            ResourceSyncOutcome(
                                resource_id="*",
                                status="failed",
                                message=str(exc),
                            )
                        ],
                    )
                )
                continue
            results.append(result)
        return results

    def sync_integration(
        self,
        integration: KnowledgeIntegration,
        *,
        resource_ids: Sequence[str] | None = None,
    ) -> IntegrationSyncResult:
        with self._integration_lock(integration) as locked:
            if not locked:
                logger.info(
                    "integration_sync_skipped integration=%s business=%s reason=locked",
                    integration.id,
                    integration.business_profile_id,
                )
                return IntegrationSyncResult(
                    integration_id=str(integration.id),
                    provider=integration.integration_type,
                    status="skipped",
                    message="locked",
                )
            return self._run_sync(integration, resource_ids=resource_ids)

    def _run_sync(
        self,
        integration: KnowledgeIntegration,
        *,
        resource_ids: Sequence[str] | None = None,
    ) -> IntegrationSyncResult:
        filtered_resources = self._select_resources(integration, resource_ids)
        if not filtered_resources:
            logger.info(
                "integration_sync_skipped integration=%s business=%s reason=no_resources",
                integration.id,
                integration.business_profile_id,
            )
            timestamp = timezone.now()
            integration.last_synced_at = timestamp
            integration.status = KnowledgeIntegrationStatus.CONNECTED
            integration.sync_error = ""
            integration.record_sync_schedule(started_at=timestamp, status="success")
            integration.save(update_fields=["last_synced_at", "status", "sync_error", "metadata", "updated_at"])
            return IntegrationSyncResult(
                integration_id=str(integration.id),
                provider=integration.integration_type,
                status="completed",
            )

        if integration.credentials_need_rotation():
            reason = "Integration credentials expired; reconnect required."
            integration.status = KnowledgeIntegrationStatus.DISCONNECTED
            integration.sync_error = reason
            integration.log_credential_event(
                IntegrationCredentialEventType.ROTATION_REQUIRED,
                metadata={"reason": reason},
            )
            raise IntegrationCredentialsError(reason)

        integration.status = KnowledgeIntegrationStatus.SYNCING
        integration.sync_error = ""
        integration.save(update_fields=["status", "sync_error", "updated_at"])

        started_at = timezone.now()
        started_perf = time.perf_counter()
        adapter = self._build_adapter(integration)
        now = started_at
        outcomes: list[ResourceSyncOutcome] = []
        resource_status_updates: dict[str, dict[str, object]] = {}
        total_bytes = 0

        grouped_resources = self._group_resources(filtered_resources)
        total_rows = 0
        for resource_group in grouped_resources:
            batch_exports = self._try_batch_export(adapter, resource_group)
            for resource in resource_group:
                resource_id = self._resource_id(resource)
                try:
                    exported = batch_exports.get(resource_id) if batch_exports else None
                    if exported is None:
                        exported = adapter.export_resource(resource)
                    upload, bytes_written, job_id, changed, row_count = self._save_and_queue(
                        integration,
                        resource,
                        exported,
                        synced_at=now,
                    )
                    total_bytes += bytes_written
                    total_rows += row_count
                    status_label = "success" if changed else "unchanged"
                    message = "" if changed else "No changes detected."
                    outcomes.append(
                        ResourceSyncOutcome(
                            resource_id=resource_id,
                            status=status_label,
                            upload_id=str(upload.id),
                            bytes_written=bytes_written,
                            job_id=str(job_id) if job_id else None,
                            message=message,
                            rows_ingested=row_count,
                        )
                    )
                    resource_status_updates[resource_id] = {
                        "last_synced_at": now.isoformat(),
                        "last_sync_status": status_label,
                        "last_sync_bytes": bytes_written,
                        "rows_ingested": row_count,
                        "last_sync_error": None,
                        "stale_since": None,
                        "stale_reason": None,
                    }
                except IntegrationSyncError as exc:
                    logger.warning(
                        "integration_resource_failed integration=%s resource=%s error=%s",
                        integration.id,
                        resource_id,
                        exc,
                    )
                    message = str(exc)
                    outcomes.append(
                        ResourceSyncOutcome(
                            resource_id=resource_id,
                            status="failed",
                            message=message,
                        )
                    )
                    stale = self._is_missing_resource_error(message)
                    resource_status_updates[resource_id] = {
                        "last_synced_at": now.isoformat(),
                        "last_sync_status": "failed",
                        "last_sync_error": message,
                        "last_sync_bytes": 0,
                        "rows_ingested": 0,
                        "stale_since": now.isoformat() if stale else None,
                        "stale_reason": message if stale else None,
                    }
                    if stale:
                        self._mark_resource_stale(integration, resource_id, reason=message)
                except Exception as exc:  # pragma: no cover - defensive programming
                    logger.exception(
                        "integration_resource_exception integration=%s resource=%s",
                        integration.id,
                        resource_id,
                    )
                    message = str(exc)
                    outcomes.append(
                        ResourceSyncOutcome(
                            resource_id=resource_id,
                            status="failed",
                            message=message,
                        )
                    )
                    resource_status_updates[resource_id] = {
                        "last_synced_at": now.isoformat(),
                        "last_sync_status": "failed",
                        "last_sync_error": message,
                        "last_sync_bytes": 0,
                        "rows_ingested": 0,
                    }

        self._update_resource_statuses(integration, resource_status_updates)

        integration.last_synced_at = now
        if any(outcome.status == "failed" for outcome in outcomes):
            integration.status = KnowledgeIntegrationStatus.ERROR
            integration.sync_error = "; ".join(out.message for out in outcomes if out.status == "failed" and out.message)
        else:
            integration.status = KnowledgeIntegrationStatus.CONNECTED
            integration.sync_error = ""
            integration.reset_credential_failures()
        duration_ms = int((time.perf_counter() - started_perf) * 1000)
        metadata = dict(integration.metadata or {})
        metadata.setdefault("sync_stats", {})
        success_total = sum(1 for outcome in outcomes if outcome.status in {"success", "unchanged"})
        metadata["sync_stats"].update(
            {
                "last_run_at": now.isoformat(),
                "resources_attempted": len(filtered_resources),
                "success_count": success_total,
                "failure_count": sum(1 for outcome in outcomes if outcome.status == "failed"),
                "bytes_written": total_bytes,
                "rows_ingested": total_rows,
                "duration_ms": duration_ms,
            }
        )
        integration.metadata = metadata
        integration.record_sync_schedule(started_at=started_at, duration_ms=duration_ms, status=integration.status)
        integration.save(
            update_fields=[
                "last_synced_at",
                "status",
                "sync_error",
                "updated_at",
                "settings",
                "metadata",
                "credential_error_count",
            ]
        )

        logger.info(
            "integration_sync_summary integration=%s business=%s provider=%s resources=%s success=%s failed=%s bytes=%s duration_ms=%s",
            integration.id,
            integration.business_profile_id,
            integration.integration_type,
            len(filtered_resources),
            metadata["sync_stats"].get("success_count"),
            metadata["sync_stats"].get("failure_count"),
            total_bytes,
            duration_ms,
        )

        result_status = "completed" if integration.status == KnowledgeIntegrationStatus.CONNECTED else "error"
        return IntegrationSyncResult(
            integration_id=str(integration.id),
            provider=integration.integration_type,
            resources=outcomes,
            status=result_status,
            message=integration.sync_error or "",
            rows_ingested=total_rows,
        )

    def _select_resources(
        self,
        integration: KnowledgeIntegration,
        resource_ids: Sequence[str] | None,
    ) -> list[IntegrationResourceConfig]:
        resources = integration.resource_configs
        if not resource_ids:
            return resources
        wanted = {str(rid) for rid in resource_ids}
        return [resource for resource in resources if self._resource_id(resource) in wanted]

    def _build_adapter(self, integration: KnowledgeIntegration):
        if integration.integration_type == KnowledgeIntegrationType.GOOGLE_DRIVE:
            try:
                maybe_refresh_google_credentials(integration)
            except GoogleOAuthError as exc:
                raise IntegrationCredentialsError(f"Google token refresh failed: {exc}") from exc
            return GoogleSheetsExporter(integration)
        raise IntegrationSyncError(f"Integration type '{integration.integration_type}' is not supported yet.")

    def _save_and_queue(
        self,
        integration: KnowledgeIntegration,
        resource: IntegrationResourceConfig,
        exported: ExportedSheet,
        *,
        synced_at: datetime,
    ) -> tuple[KnowledgeUpload, int, str | None, bool]:
        owner = integration.created_by or integration.business_profile.user
        sheet_name_raw = (resource.get("sheet_name") or "").strip()
        drive_name_raw = (resource.get("drive_file_name") or "").strip()
        # Prefer a composite label so integrated sheets are uniquely identifiable
        # (e.g., "Playbooks – Sheet1" instead of just "Sheet1").
        if sheet_name_raw and drive_name_raw and sheet_name_raw.lower() not in {drive_name_raw.lower()}:
            display_label = f"{drive_name_raw} – {sheet_name_raw}"
        else:
            display_label = sheet_name_raw or drive_name_raw or "Synced Sheet"

        upload, created = KnowledgeUpload.objects.get_or_create(
            integration=integration,
            source_uid=self._resource_id(resource),
            defaults={
                "business_profile": integration.business_profile,
                "user": owner,
                "display_name": display_label[:255],
                "source_name": (drive_name_raw or "Google Sheet")[:255],
                "source_type": KnowledgeSourceType.INTEGRATION,
                "source_uid": self._resource_id(resource),
                "external_reference": resource.get("drive_file_id", ""),
                "visibility": resource.get("visibility") or integration.get_default_visibility(),
                "status": KnowledgeStatus.PENDING,
            },
        )

        upload.integration = integration
        upload.user = owner
        upload.display_name = (display_label or upload.display_name or "Synced Sheet")[:255]
        upload.source_name = (drive_name_raw or upload.source_name or "Google Sheet")[:255]
        upload.source_type = KnowledgeSourceType.INTEGRATION
        upload.source_uid = self._resource_id(resource)
        upload.external_reference = resource.get("drive_file_id", "")
        upload.visibility = resource.get("visibility") or integration.get_default_visibility()
        content_bytes = exported.content
        size_bytes = len(content_bytes)
        checksum = hashlib.sha256(content_bytes).hexdigest()
        try:
            existing_file = upload.file_detail
        except KnowledgeUploadFile.DoesNotExist:
            existing_file = None
        existing_checksum = existing_file.checksum_sha256 if existing_file else ""
        changed = checksum != existing_checksum
        if created:
            upload.version = upload.version or 1
        elif changed:
            upload.version = (upload.version or 1) + 1
        if changed:
            storage_path = self._write_export_file(integration, resource, exported, synced_at)
        else:
            storage_path = existing_file.storage_path if existing_file else self._write_export_file(
                integration, resource, exported, synced_at
            )
        upload.size_bytes = size_bytes
        if changed:
            upload.status = KnowledgeStatus.PENDING
        elif upload.status in {KnowledgeStatus.FAILED, KnowledgeStatus.PENDING}:
            upload.status = KnowledgeStatus.READY
        upload.last_synced_at = synced_at
        upload.ingestion_error = ""
        row_count = self._estimate_row_count(exported) if changed else 0

        normalized_privacy = self._normalize_column_privacy(resource.get("column_privacy"))
        table_privacy_payload = self._build_table_privacy_metadata(
            integration,
            normalized_privacy,
            resource_id=self._resource_id(resource),
        )

        metadata = dict(upload.metadata or {})
        # Ensure a stable, descriptive public label for RAG/LLM prompts.
        existing_public = (metadata.get("public_label") or "").strip()
        if not existing_public:
            metadata["public_label"] = display_label[:255]
            metadata.setdefault("display_label", display_label[:255])
        metadata["integration_resource"] = {
            "resource_id": self._resource_id(resource),
            "drive_file_id": resource.get("drive_file_id"),
            "drive_file_name": resource.get("drive_file_name"),
            "sheet_gid": resource.get("sheet_gid"),
            "sheet_name": resource.get("sheet_name"),
            "sheet_label": display_label[:255],
            "sync_frequency": resource.get("sync_frequency"),
            "visibility": resource.get("visibility"),
            "metadata": resource.get("metadata") or {},
            "row_count": row_count or (metadata.get("integration_resource") or {}).get("row_count"),
        }
        metadata.setdefault("integration_sync", {})
        metadata["integration_sync"].update(
            {
                "last_synced_at": synced_at.isoformat(),
                "provider": integration.integration_type,
            }
        )
        table_privacy = dict(metadata.get("table_privacy") or {})
        table_privacy.update(table_privacy_payload)
        metadata["table_privacy"] = table_privacy
        metadata["integration_sync"].pop("stale", None)
        metadata["integration_sync"].pop("stale_reason", None)
        metadata["integration_sync"].pop("stale_since", None)
        upload.metadata = metadata

        ingestion_metadata = dict(upload.ingestion_metadata or {})
        ingestion_metadata["column_privacy"] = normalized_privacy
        ingestion_metadata["integration_sync"] = {
            "resource_id": self._resource_id(resource),
            "sync_frequency": resource.get("sync_frequency"),
        }
        upload.ingestion_metadata = ingestion_metadata

        upload.save()

        # Derive a readable storage filename that carries both file and sheet names
        # to aid downstream diagnostics and table titles.
        base_for_filename = drive_name_raw
        if sheet_name_raw and sheet_name_raw.lower() not in {drive_name_raw.lower()}:
            base_for_filename = f"{drive_name_raw}-{sheet_name_raw}".strip("- ")
        if not base_for_filename:
            base_for_filename = upload.display_name or str(upload.id)

        KnowledgeUploadFile.objects.update_or_create(
            upload=upload,
            defaults={
                "filename": f"{slugify(base_for_filename) or upload.id}.{exported.extension}",
                "content_type": exported.content_type,
                "storage_path": str(storage_path),
                "size_bytes": size_bytes,
                "checksum_sha256": checksum,
                "metadata": {
                    "integration_resource_id": self._resource_id(resource),
                    "drive_file_id": resource.get("drive_file_id"),
                    "sheet_gid": resource.get("sheet_gid"),
                },
            },
        )

        job = queue_ingestion_job(upload, trigger="integration_sync", force=True) if changed else None
        job_id = getattr(job, "id", None) if job else None
        bytes_written = size_bytes if changed else 0
        return upload, bytes_written, job_id, changed, row_count

    def _write_export_file(
        self,
        integration: KnowledgeIntegration,
        resource: IntegrationResourceConfig,
        exported: ExportedSheet,
        synced_at: datetime,
    ) -> str:
        timestamp = synced_at.strftime("%Y%m%d%H%M%S")
        safe_name = slugify(resource.get("sheet_name") or self._resource_id(resource)) or "sheet"
        relative_path = (
            Path("integrations")
            / str(integration.business_profile_id)
            / str(integration.id)
            / self._resource_id(resource)
            / f"{timestamp}_{safe_name}.{exported.extension}"
        )
        absolute_path = (self.storage_root / relative_path).resolve()
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_bytes(exported.content)
        return relative_path.as_posix()

    def _estimate_row_count(self, exported: ExportedSheet) -> int:
        extension = (exported.extension or "").lower()
        if extension not in {"csv", "tsv"}:
            return 0
        content = exported.content or b""
        if not content:
            return 0
        lines = content.count(b"\n")
        if not lines and content:
            return 1
        return max(lines, 0)

    def _update_resource_statuses(
        self,
        integration: KnowledgeIntegration,
        status_updates: dict[str, dict[str, object]],
    ) -> None:
        if not status_updates:
            return
        resources = integration.resource_configs
        updated_resources: list[IntegrationResourceConfig] = []
        for resource in resources:
            rid = self._resource_id(resource)
            payload = dict(resource)
            if rid in status_updates:
                for key, value in status_updates[rid].items():
                    if value is None:
                        payload.pop(key, None)
                    else:
                        payload[key] = value
            updated_resources.append(payload)
        integration.set_resource_configs(updated_resources)

    def _resource_id(self, resource: IntegrationResourceConfig) -> str:
        rid = resource.get("resource_id")
        if rid:
            return str(rid)
        drive_file_id = resource.get("drive_file_id") or "file"
        sheet_gid = resource.get("sheet_gid") or "sheet"
        return f"{drive_file_id}:{sheet_gid}"

    def _group_resources(self, resources: Sequence[IntegrationResourceConfig]) -> list[list[IntegrationResourceConfig]]:
        grouped: dict[str, list[IntegrationResourceConfig]] = {}
        for resource in resources:
            drive_id = resource.get("drive_file_id") or self._resource_id(resource)
            grouped.setdefault(str(drive_id), []).append(resource)
        return list(grouped.values())

    def _try_batch_export(self, adapter, resources: list[IntegrationResourceConfig]) -> dict[str, ExportedSheet]:
        if len(resources) <= 1:
            return {}
        supports_batch = getattr(adapter, "supports_batch_export", None)
        if not callable(supports_batch) or not supports_batch():
            return {}
        try:
            result = adapter.export_batch(resources)
        except IntegrationSyncError as exc:
            drive_id = resources[0].get("drive_file_id") or "unknown"
            logger.warning(
                "integration_batch_export_failed integration=%s drive_file=%s error=%s",
                adapter.integration.id if hasattr(adapter, "integration") else "unknown",
                drive_id,
                exc,
            )
            return {}
        if not isinstance(result, dict):
            return {}
        return result

    def _normalize_column_privacy(self, column_privacy: Mapping[str, Any] | None) -> dict[str, list[str]]:
        column_privacy = column_privacy or {}

        def _clean(values: Any) -> list[str]:
            if isinstance(values, str):
                values = [part.strip() for part in values.split(",")]
            cleaned: list[str] = []
            if isinstance(values, (list, tuple, set)):
                for value in values:
                    text = str(value or "").strip()
                    if text:
                        cleaned.append(text)
            return cleaned

        shared = _clean(column_privacy.get("shared_columns") or column_privacy.get("sharedColumns"))
        internal = _clean(column_privacy.get("internal_only_columns") or column_privacy.get("internalOnlyColumns"))
        excluded = _clean(column_privacy.get("excluded_columns") or column_privacy.get("excludedColumns"))

        return {
            "shared_columns": shared,
            "internal_only_columns": internal,
            "excluded_columns": excluded,
        }

    def _build_table_privacy_metadata(
        self,
        integration: KnowledgeIntegration,
        column_privacy: dict[str, list[str]],
        *,
        resource_id: str,
    ) -> dict[str, Any]:
        shared = column_privacy.get("shared_columns") or []
        internal = column_privacy.get("internal_only_columns") or []
        excluded = column_privacy.get("excluded_columns") or []
        policy = integration.business_profile.table_privacy_policy() if integration.business_profile_id else {}
        require_masking = bool(policy.get("masking_required"))
        required_lookup: dict[str, str] = {}
        for value in policy.get("required_columns") or []:
            canonical = self._canonical_column_name(value)
            if canonical:
                required_lookup[canonical] = value
        has_privacy_values = bool(shared or internal or excluded)
        if not has_privacy_values:
            if require_masking or required_lookup:
                raise IntegrationSyncError(
                    f"Business privacy policy requires masking columns before syncing resource {resource_id}."
                )
            return {}
        masked_canonical = {self._canonical_column_name(value) for value in [*internal, *excluded] if self._canonical_column_name(value)}
        if require_masking and not masked_canonical:
            raise IntegrationSyncError(
                f"Business privacy policy requires masking columns before syncing resource {resource_id}."
            )
        missing = [label for key, label in required_lookup.items() if key not in masked_canonical]
        if missing:
            detail = ", ".join(sorted(missing))
            raise IntegrationSyncError(
                f"Resource {resource_id} is missing masking for required columns: {detail}."
            )
        sensitive = sorted({*internal, *excluded}, key=lambda value: value.lower())
        table_privacy = {
            "source": "integration_resource",
            "sensitive_columns": sensitive,
            "shared_columns": shared,
            "internal_only_columns": internal,
            "excluded_columns": excluded,
        }
        if policy.get("policy_version"):
            table_privacy["policy_version"] = policy.get("policy_version")
        return table_privacy

    @staticmethod
    def _canonical_column_name(value: str | None) -> str:
        if not isinstance(value, str):
            return ""
        return " ".join(value.split()).strip().lower()

    def _mark_resource_stale(self, integration: KnowledgeIntegration, resource_id: str, *, reason: str) -> None:
        uploads = list(
            KnowledgeUpload.objects.filter(integration=integration, source_uid=resource_id).only(
                "id", "metadata", "ingestion_error", "status"
            )
        )
        if not uploads:
            return
        timestamp = timezone.now().isoformat()
        logger.warning(
            "integration_resource_marked_stale integration=%s business=%s resource=%s reason=%s",
            integration.id,
            integration.business_profile_id,
            resource_id,
            reason,
        )
        for upload in uploads:
            metadata = dict(upload.metadata or {})
            metadata.setdefault("integration_sync", {})
            sync_meta = metadata["integration_sync"]
            sync_meta["stale"] = True
            sync_meta["stale_reason"] = reason
            sync_meta["stale_since"] = timestamp
            upload.metadata = metadata
            upload.ingestion_error = reason
            upload.status = KnowledgeStatus.FAILED
            upload.save(update_fields=["metadata", "ingestion_error", "status", "updated_at"])

    def _is_missing_resource_error(self, message: str) -> bool:
        lowered = (message or "").lower()
        keywords = ("not found", "missing", "deleted", "gone", "no longer", "permission")
        return any(token in lowered for token in keywords)

    @contextlib.contextmanager
    def _integration_lock(self, integration: KnowledgeIntegration):
        if connection.vendor != "postgresql":
            yield True
            return
        key = self._lock_key(integration)
        acquired = False
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
            row = cursor.fetchone()
            acquired = bool(row[0]) if row else False
        try:
            yield acquired
        finally:
            if acquired:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [key])

    def _lock_key(self, integration: KnowledgeIntegration) -> int:
        identifier = integration.id
        if isinstance(identifier, uuid.UUID):
            value = identifier.int
        else:
            value = uuid.UUID(str(identifier)).int
        return value & ((1 << 63) - 1)


class GoogleSheetsExporter:
    def __init__(self, integration: KnowledgeIntegration):
        self.integration = integration

    def export_resource(self, resource: IntegrationResourceConfig) -> ExportedSheet:
        access_token = self._access_token()
        drive_file_id = resource.get("drive_file_id")
        sheet_gid = resource.get("sheet_gid")
        if not drive_file_id or not sheet_gid:
            raise IntegrationSyncError("Resource is missing drive_file_id or sheet_gid.")
        export_format = str((resource.get("metadata") or {}).get("export_format") or "csv").lower()
        if export_format not in {"csv", "xlsx"}:
            export_format = "csv"
        if export_format == "xlsx":
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            extension = "xlsx"
        else:
            mime = "text/csv"
            extension = "csv"
        url = f"https://docs.google.com/spreadsheets/d/{drive_file_id}/export?format={export_format}&gid={sheet_gid}"
        headers = {"Authorization": f"Bearer {access_token}"}
        response = self._request_with_backoff(url, headers=headers, timeout=60)
        if response.status_code == 401:
            raise IntegrationCredentialsError("Google rejected the access token (401).")
        if not response.ok:
            raise IntegrationSyncError(
                f"Google export failed with status {response.status_code}: {response.text[:200]}"
            )
        filename = slugify(resource.get("sheet_name") or resource.get("resource_id") or sheet_gid) or "sheet"
        content_type = response.headers.get("Content-Type") or mime
        return ExportedSheet(
            content=response.content,
            filename=filename,
            content_type=content_type,
            extension=extension,
        )

    def supports_batch_export(self) -> bool:
        return True

    def export_batch(self, resources: list[IntegrationResourceConfig]) -> dict[str, ExportedSheet]:
        if not resources:
            return {}
        drive_file_id = resources[0].get("drive_file_id")
        if not drive_file_id:
            return {}
        sheet_names = [str(res.get("sheet_name") or "").strip() for res in resources if res.get("sheet_name")]
        if len(sheet_names) <= 1:
            return {}
        access_token = self._access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        params: list[tuple[str, str]] = [("ranges", name) for name in sheet_names]
        params.append(("majorDimension", "ROWS"))
        url = f"{GOOGLE_SHEETS_ENDPOINT}/{drive_file_id}/values:batchGet"
        response = self._request_with_backoff(url, headers=headers, params=params, timeout=40)
        if response.status_code == 401:
            raise IntegrationCredentialsError("Google rejected the access token (401).")
        if not response.ok:
            raise IntegrationSyncError(
                f"Google batch export failed with status {response.status_code}: {response.text[:200]}"
            )
        payload = response.json()
        value_ranges = payload.get("valueRanges") or []
        resource_lookup = {
            str(res.get("sheet_name") or "").strip().lower(): res for res in resources if res.get("sheet_name")
        }
        exports: dict[str, ExportedSheet] = {}
        for value_range in value_ranges:
            raw_range = value_range.get("range") or ""
            sheet_label = raw_range.split("!")[0].strip("'\"") or raw_range
            lookup_key = sheet_label.strip().lower()
            resource = resource_lookup.get(lookup_key)
            if not resource:
                continue
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            for row in value_range.get("values") or []:
                writer.writerow(row)
            content = buffer.getvalue().encode("utf-8")
            buffer.close()
            exports[self._resource_id(resource)] = ExportedSheet(
                content=content,
                filename=slugify(resource.get("sheet_name") or self._resource_id(resource)) or "sheet",
                content_type="text/csv",
                extension="csv",
            )
        if exports:
            logger.info(
                "google_batch_export integration=%s drive_file=%s sheets=%s",
                self.integration.id,
                drive_file_id,
                len(exports),
            )
        return exports

    def _access_token(self) -> str:
        credentials = self.integration.credentials or {}
        token = credentials.get("access_token")
        if not token:
            raise IntegrationCredentialsError("Google integration is missing an access token.")
        return str(token)

    def _request_with_backoff(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: list[tuple[str, str]] | dict[str, str] | None = None,
        timeout: int = 60,
        max_attempts: int = 3,
    ) -> requests.Response:
        delay = 1.0
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.get(url, headers=headers, params=params, timeout=timeout)
            except requests.RequestException as exc:
                last_error = str(exc)
                logger.warning(
                    "google_api_request_failed integration=%s attempt=%s error=%s",
                    self.integration.id,
                    attempt,
                    exc,
                )
            else:
                if response.status_code in RETRYABLE_STATUS_CODES:
                    logger.warning(
                        "google_api_rate_limited integration=%s status=%s attempt=%s",
                        self.integration.id,
                        response.status_code,
                        attempt,
                    )
                    last_error = f"{response.status_code}:{response.text[:200]}"
                else:
                    return response
            if attempt < max_attempts:
                time.sleep(delay)
                delay = min(delay * 2, 30)
        raise IntegrationSyncError(f"Google API request failed after retries: {last_error}")

    def _resource_id(self, resource: IntegrationResourceConfig) -> str:
        rid = resource.get("resource_id")
        if rid:
            return str(rid)
        drive_file_id = resource.get("drive_file_id") or "file"
        sheet_gid = resource.get("sheet_gid") or "sheet"
        return f"{drive_file_id}:{sheet_gid}"
