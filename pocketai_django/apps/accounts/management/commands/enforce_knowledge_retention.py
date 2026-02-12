from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Mapping

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.accounts.models import (
    KnowledgeAuditAction,
    KnowledgeStatus,
)
from apps.knowledge.models import (
    KnowledgeAuditEvent,
    KnowledgeUpload,
)
from apps.knowledge.documents import delete_document


logger = logging.getLogger(__name__)


def _parse_iso_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed = parse_datetime(text)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        try:
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        except Exception:
            return None
    return parsed


def _retention_expiry(upload: KnowledgeUpload) -> datetime | None:
    policy = upload.retention_policy if isinstance(upload.retention_policy, Mapping) else {}
    if not policy or policy.get("disabled") is True:
        return None

    expires_at = _parse_iso_datetime(policy.get("expires_at") or policy.get("expiresAt"))
    if expires_at:
        return expires_at

    ttl_raw = policy.get("ttl_days") or policy.get("ttlDays")
    try:
        ttl_days = int(ttl_raw) if ttl_raw is not None else None
    except (TypeError, ValueError):
        ttl_days = None
    if not ttl_days or ttl_days <= 0:
        return None

    base_field = str(policy.get("ttl_from") or policy.get("ttlFrom") or "created_at").strip().lower()
    if base_field == "updated_at":
        base_ts = upload.updated_at
    else:
        base_ts = upload.created_at
    return base_ts + timedelta(days=ttl_days)


def _retention_action(upload: KnowledgeUpload) -> str:
    policy = upload.retention_policy if isinstance(upload.retention_policy, Mapping) else {}
    action = str(policy.get("action") or "archive").strip().lower()
    if action not in {"archive", "delete"}:
        return "archive"
    return action


class Command(BaseCommand):
    help = "Enforce KnowledgeUpload.retention_policy expirations (archive/delete)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--business-id", type=str, default="", help="Optional BusinessProfile UUID to scope to.")
        parser.add_argument("--dry-run", action="store_true", help="Print changes without mutating data.")
        parser.add_argument("--limit", type=int, default=200, help="Max uploads to process in one run.")

    def handle(self, *args, **options):
        now = timezone.now()
        business_id = str(options.get("business_id") or "").strip()
        dry_run = bool(options.get("dry_run"))
        limit = int(options.get("limit") or 0)
        if limit <= 0:
            limit = 200

        uploads = KnowledgeUpload.objects.filter(is_active=True).exclude(status=KnowledgeStatus.ARCHIVED)
        if business_id:
            try:
                uploads = uploads.filter(business_profile_id=business_id)
            except Exception:
                self.stderr.write(self.style.ERROR("Invalid --business-id"))
                return

        processed = 0
        archived = 0
        deleted = 0

        for upload in uploads.order_by("created_at")[:limit]:
            expiry = _retention_expiry(upload)
            if not expiry or expiry > now:
                continue

            action = _retention_action(upload)
            processed += 1
            self.stdout.write(f"{upload.id} expired_at={expiry.isoformat()} action={action}")

            if dry_run:
                continue

            if action == "delete":
                try:
                    delete_document(business_profile=upload.business_profile, document_id=upload.id)
                    deleted += 1
                except Exception:
                    logger.exception("retention.delete_failed upload=%s business=%s", upload.id, upload.business_profile_id)
                continue

            with transaction.atomic():
                upload.status = KnowledgeStatus.ARCHIVED
                upload.is_active = False
                upload.save(update_fields=["status", "is_active", "updated_at"])
                archived += 1
                try:
                    KnowledgeAuditEvent.objects.create(
                        business_profile=upload.business_profile,
                        upload=upload,
                        upload_id_snapshot=upload.id,
                        action=KnowledgeAuditAction.ARCHIVED,
                        description="Archived by retention policy.",
                        metadata={
                            "trigger": "retention_policy",
                            "expires_at": expiry.isoformat(),
                            "action": action,
                        },
                    )
                except Exception:
                    logger.exception("retention.audit_failed upload=%s business=%s", upload.id, upload.business_profile_id)

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {processed} expired uploads (archived={archived}, deleted={deleted}, dry_run={dry_run})."
            )
        )
