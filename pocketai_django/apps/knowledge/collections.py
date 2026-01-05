from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from core.tenancy import tenant_context

from apps.accounts.models import (
    BusinessProfile,
    KnowledgeCollection,
    KnowledgeCollectionLink,
    KnowledgeUpload,
    User,
)

logger = logging.getLogger(__name__)


class KnowledgeCollectionValidationError(ValueError):
    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class KnowledgeCollectionListItem:
    id: uuid.UUID
    name: str
    slug: str
    description: str
    visibility: str
    documents: int
    owner: str | None
    updated_at: datetime | None


def list_knowledge_collections(
    *,
    business_profile: BusinessProfile,
    limit: int = 200,
    offset: int = 0,
) -> tuple[KnowledgeCollectionListItem, ...]:
    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:  # pragma: no cover
        raise KnowledgeCollectionValidationError("limit must be an integer", field="limit") from exc
    if limit < 1 or limit > 500:
        raise KnowledgeCollectionValidationError("limit must be between 1 and 500", field="limit")

    try:
        offset = int(offset)
    except (TypeError, ValueError) as exc:  # pragma: no cover
        raise KnowledgeCollectionValidationError("offset must be an integer", field="offset") from exc
    if offset < 0 or offset > 50_000:
        raise KnowledgeCollectionValidationError("offset must be between 0 and 50000", field="offset")

    with tenant_context(business_profile.id):
        rows = (
            KnowledgeCollection.objects.filter(business_profile=business_profile)
            .select_related("created_by")
            .annotate(document_count=Count("links", distinct=True))
            .only(
                "id",
                "name",
                "slug",
                "description",
                "visibility",
                "updated_at",
                "created_by__email",
                "created_by__first_name",
                "created_by__last_name",
            )
            .order_by("name")[offset : offset + limit]
        )

        items: list[KnowledgeCollectionListItem] = []
        for row in rows:
            owner = None
            created_by = getattr(row, "created_by", None)
            if created_by:
                first = (getattr(created_by, "first_name", "") or "").strip()
                last = (getattr(created_by, "last_name", "") or "").strip()
                owner = " ".join(part for part in (first, last) if part) or getattr(created_by, "email", None)
            items.append(
                KnowledgeCollectionListItem(
                    id=row.id,
                    name=row.name,
                    slug=row.slug,
                    description=row.description or "",
                    visibility=row.visibility,
                    documents=int(getattr(row, "document_count", 0) or 0),
                    owner=owner,
                    updated_at=row.updated_at,
                )
            )

        return tuple(items)


def create_knowledge_collection(
    *,
    business_profile: BusinessProfile,
    created_by: User,
    name: str,
    description: str = "",
    visibility: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> KnowledgeCollection:
    name = (name or "").strip()
    if not name:
        raise KnowledgeCollectionValidationError("name is required.", field="name")
    if len(name) > 160:
        raise KnowledgeCollectionValidationError("name must be 160 characters or fewer.", field="name")
    description = (description or "").strip()

    with tenant_context(business_profile.id):
        collection = KnowledgeCollection(
            business_profile=business_profile,
            created_by=created_by,
            name=name,
            description=description,
        )
        if visibility:
            collection.visibility = str(visibility).strip()
        if metadata:
            collection.metadata = dict(metadata)
        collection.save()
        return collection


def update_knowledge_collection(
    *,
    business_profile: BusinessProfile,
    collection_id: uuid.UUID,
    name: str | None = None,
    description: str | None = None,
    visibility: str | None = None,
) -> KnowledgeCollection:
    update_fields: list[str] = []
    with tenant_context(business_profile.id):
        collection = KnowledgeCollection.objects.filter(business_profile=business_profile, id=collection_id).first()
        if not collection:
            raise KnowledgeCollection.DoesNotExist

        if name is not None:
            name = (name or "").strip()
            if not name:
                raise KnowledgeCollectionValidationError("name is required.", field="name")
            if len(name) > 160:
                raise KnowledgeCollectionValidationError("name must be 160 characters or fewer.", field="name")
            collection.name = name
            collection.slug = ""  # regenerate on save
            update_fields.extend(["name", "slug"])

        if description is not None:
            collection.description = (description or "").strip()
            update_fields.append("description")

        if visibility is not None:
            collection.visibility = str(visibility).strip()
            update_fields.append("visibility")

        if not update_fields:
            raise KnowledgeCollectionValidationError("No changes provided.")

        collection.save(update_fields=update_fields + ["updated_at"])
        return collection


def delete_knowledge_collection(
    *,
    business_profile: BusinessProfile,
    collection_id: uuid.UUID,
) -> None:
    with tenant_context(business_profile.id):
        deleted, _ = KnowledgeCollection.objects.filter(business_profile=business_profile, id=collection_id).delete()
        if not deleted:
            raise KnowledgeCollection.DoesNotExist


def set_upload_collections(
    *,
    business_profile: BusinessProfile,
    upload_id: uuid.UUID,
    collection_ids: Iterable[uuid.UUID],
    added_by: User | None = None,
) -> tuple[KnowledgeCollection, ...]:
    requested: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for raw in collection_ids:
        try:
            value = raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
        except (TypeError, ValueError) as exc:
            raise KnowledgeCollectionValidationError("collectionIds must contain valid UUIDs.", field="collectionIds") from exc
        if value in seen:
            continue
        requested.append(value)
        seen.add(value)

    with tenant_context(business_profile.id):
        upload = (
            KnowledgeUpload.objects.filter(business_profile=business_profile, id=upload_id)
            .only("id", "chunk_count", "business_profile_id")
            .first()
        )
        if not upload:
            raise KnowledgeUpload.DoesNotExist

        collections = list(
            KnowledgeCollection.objects.filter(business_profile=business_profile, id__in=requested).only(
                "id",
                "name",
                "slug",
                "visibility",
            )
        )
        found_ids = {collection.id for collection in collections}
        missing = [str(collection_id) for collection_id in requested if collection_id not in found_ids]
        if missing:
            raise KnowledgeCollectionValidationError(
                f"Unknown collection ids: {', '.join(missing)}",
                field="collectionIds",
            )

        with transaction.atomic():
            links = list(
                KnowledgeCollectionLink.objects.filter(upload=upload, collection__business_profile=business_profile).only(
                    "id",
                    "collection_id",
                    "position",
                )
            )
            existing_by_collection = {link.collection_id: link for link in links}
            requested_set = set(requested)

            to_remove = [collection_id for collection_id in existing_by_collection if collection_id not in requested_set]
            if to_remove:
                KnowledgeCollectionLink.objects.filter(upload=upload, collection_id__in=to_remove).delete()

            to_create: list[KnowledgeCollectionLink] = []
            to_update: list[KnowledgeCollectionLink] = []
            for position, collection_id in enumerate(requested):
                existing = existing_by_collection.get(collection_id)
                if existing is None:
                    to_create.append(
                        KnowledgeCollectionLink(
                            collection_id=collection_id,
                            upload=upload,
                            position=position,
                            added_by=added_by,
                        )
                    )
                elif existing.position != position:
                    existing.position = position
                    to_update.append(existing)

            if to_create:
                KnowledgeCollectionLink.objects.bulk_create(to_create)
            if to_update:
                KnowledgeCollectionLink.objects.bulk_update(to_update, ["position"])

            def _on_commit() -> None:
                try:
                    from apps.rag.ai_orchestrator import KnowledgeSearchService
                    from apps.rag.azure_ai_search import AzureAISearchConfig, update_upload_collections
                except Exception:
                    return

                config = AzureAISearchConfig.from_settings()
                if not config:
                    return
                try:
                    update_upload_collections(
                        config=config,
                        upload_id=upload.id,
                        chunk_count=int(getattr(upload, "chunk_count", 0) or 0),
                        collection_ids=tuple(requested),
                        updated_at=timezone.now(),
                    )
                except Exception as exc:
                    logger.warning(
                        "azure_search.collection_update_failed business=%s upload=%s error=%s",
                        business_profile.id,
                        upload.id,
                        str(exc)[:250],
                    )
                    return
                try:
                    KnowledgeSearchService.invalidate_result_cache(business_profile.id)
                except Exception:
                    return

            try:
                transaction.on_commit(_on_commit)
            except Exception:  # pragma: no cover
                pass

        ordered_lookup = {collection.id: collection for collection in collections}
        return tuple(ordered_lookup[collection_id] for collection_id in requested)
