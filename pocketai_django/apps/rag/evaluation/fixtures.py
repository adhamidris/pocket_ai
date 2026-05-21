from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from django.contrib.auth import get_user_model

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    KnowledgeSourceType,
    KnowledgeStatus,
    RegistrationSession,
    User,
)
from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadFile
from apps.knowledge.ingestion.jobs import queue_ingestion_job
from apps.rag.evaluation.datasets import GoldenFixture, GoldenSet
from apps.rag.observability.logging import rag_log


class EvaluationFixtureMixin:

    @staticmethod
    def _fixture_content_type(fixture: GoldenFixture) -> str:
        if fixture.source_type == "csv" or fixture.filename.lower().endswith(".csv"):
            return "text/csv"
        if fixture.source_type == "tsv" or fixture.filename.lower().endswith(".tsv"):
            return "text/tab-separated-values"
        if fixture.source_type == "pdf" or fixture.filename.lower().endswith(".pdf"):
            return "application/pdf"
        return "application/json"

    def _fixture_name_for_upload_id(self, upload_id: uuid.UUID) -> str:
        cached = self._upload_fixture_cache.get(upload_id)
        if cached is not None:
            return cached
        try:
            upload = KnowledgeUpload.objects.only("id", "metadata").get(id=upload_id)
        except KnowledgeUpload.DoesNotExist:
            self._upload_fixture_cache[upload_id] = ""
            return ""
        metadata = upload.metadata if isinstance(upload.metadata, dict) else {}
        fixture_name = str(metadata.get("rag_eval_fixture") or "")
        self._upload_fixture_cache[upload_id] = fixture_name
        return fixture_name
    
    def _prepare_business(self, golden_set: GoldenSet) -> BusinessProfile:
        user = self._ensure_eval_user(slug=golden_set.slug)
        session = self._ensure_registration_session(user)
        business, created = BusinessProfile.objects.get_or_create(
            slug=golden_set.business_slug,
            defaults={
                "user": user,
                "registration_session": session,
                "name": f"{golden_set.industry.title()} Eval",
                "industry": golden_set.industry,
                "status": "active",
                "metadata": {"rag_eval": True},
            },
        )
        if created:
            AgentProfile.objects.get_or_create(
                business_profile=business,
                defaults={
                    "user": user,
                    "name": f"{golden_set.slug.title()} Eval Agent",
                    "slug": f"{golden_set.slug}-eval",
                    "role": "Evaluation Assistant",
                    "traits": ["deterministic", "observability"],
                },
            )
        return business

    @staticmethod
    def _ensure_eval_user(slug: str) -> User:
        user_model = get_user_model()
        email = f"rag-eval+{slug}@pocketai.local"
        user, _ = user_model.objects.get_or_create(
            email=email,
            defaults={
                "first_name": "RAG",
                "last_name": "Eval",
                "is_staff": False,
                "is_active": True,
            },
        )
        return user

    @staticmethod
    def _ensure_registration_session(user: User) -> RegistrationSession:
        session = (
            RegistrationSession.objects.filter(user=user)
            .order_by("-created_at")
            .first()
        )
        if session:
            return session
        return RegistrationSession.objects.create(user=user, total_steps=4, steps_completed=4, is_complete=True)

    def _ingest_fixtures(
        self,
        golden_set: GoldenSet,
        *,
        business: BusinessProfile,
        force: bool,
    ) -> None:
        user = business.user
        queued = 0
        for fixture in golden_set.fixtures:
            upload = self._stage_fixture_upload(
                fixture,
                business=business,
                user=user,
            )
            job = queue_ingestion_job(upload, trigger=f"rag_eval:{golden_set.slug}", force=force)
            if job:
                queued += 1
        if queued:
            rag_log(
                "eval.ingest",
                {"queued": queued},
                context={"business": business.id},
                indent=1,
            )
        processed = 0
        while True:
            result = self.ingestion_service.process_next_job()
            if not result:
                break
            processed += 1
        if processed:
            rag_log(
                "eval.ingest_completed",
                {"completed": processed},
                context={"business": business.id},
                indent=1,
            )

    def _stage_fixture_upload(
        self,
        fixture: GoldenFixture,
        *,
        business: BusinessProfile,
        user: User,
    ) -> KnowledgeUpload:
        content_type = self._fixture_content_type(fixture)
        source_uid = f"rag-eval::{business.slug}::{fixture.name}"
        relative_path = Path("rag_eval") / business.slug / fixture.filename
        absolute = self.media_root / relative_path
        absolute.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(fixture.absolute_path, absolute)

        upload, _ = KnowledgeUpload.objects.get_or_create(
            business_profile=business,
            source_uid=source_uid,
            defaults={
                "user": user,
                "display_name": fixture.description or fixture.name.replace("_", " ").title(),
                "source_name": fixture.filename,
                "source_type": KnowledgeSourceType.FILE,
                "status": KnowledgeStatus.PENDING,
                "metadata": {"rag_eval_fixture": fixture.name},
            },
        )
        upload.display_name = fixture.description or upload.display_name
        upload.source_name = fixture.filename
        upload.status = KnowledgeStatus.PENDING
        meta = dict(upload.metadata or {})
        meta["rag_eval_fixture"] = fixture.name
        upload.metadata = meta
        upload.save(update_fields=["display_name", "source_name", "status", "metadata", "updated_at"])

        file_detail, _ = KnowledgeUploadFile.objects.get_or_create(
            upload=upload,
            defaults={
                "filename": fixture.filename,
                "content_type": content_type,
                "storage_path": str(relative_path),
                "size_bytes": absolute.stat().st_size,
            },
        )
        file_detail.filename = fixture.filename
        file_detail.content_type = content_type
        file_detail.storage_path = str(relative_path)
        file_detail.size_bytes = absolute.stat().st_size
        file_detail.save(update_fields=["filename", "content_type", "storage_path", "size_bytes", "updated_at"])
        return upload
