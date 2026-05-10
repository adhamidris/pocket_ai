from __future__ import annotations

import uuid

from django.db import migrations


def _normalize_row_label(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return " ".join(text.replace("_", " ").replace("-", " ").split())


def _evidence_group_id_for_key(upload_id: object, key: str) -> str:
    return str(uuid.uuid5(uuid.UUID(str(upload_id)), key or "empty"))


def fix_table_row_evidence_groups(apps, schema_editor):
    KnowledgeUploadChunk = apps.get_model("knowledge", "KnowledgeUploadChunk")
    queryset = KnowledgeUploadChunk.objects.filter(
        metadata__is_table_chunk=True,
        metadata__table_chunk_role="row",
        metadata__table_id__isnull=False,
    ).only("id", "upload_id", "metadata")

    batch = []
    for chunk in queryset.iterator(chunk_size=500):
        metadata = dict(chunk.metadata or {})
        table_id = str(metadata.get("table_id") or "").strip()
        if not table_id:
            continue
        row_index = metadata.get("table_row_index")
        if row_index is not None:
            evidence_key = f"table_row:{table_id}:{row_index}"
        else:
            row_label = _normalize_row_label(metadata.get("row_label"))
            if row_label:
                evidence_key = f"table_row_label:{table_id}:{row_label}"
            else:
                evidence_key = f"table:{table_id}:row"

        evidence_group_id = _evidence_group_id_for_key(chunk.upload_id, evidence_key)
        if (
            metadata.get("evidence_key") == evidence_key
            and metadata.get("evidence_group_id") == evidence_group_id
        ):
            continue
        metadata["evidence_key"] = evidence_key
        metadata["evidence_group_id"] = evidence_group_id
        metadata.setdefault("representation", "table")
        metadata.setdefault("evidence_type", metadata.get("content_source") or "table_row")
        chunk.metadata = metadata
        batch.append(chunk)
        if len(batch) >= 500:
            KnowledgeUploadChunk.objects.bulk_update(batch, ["metadata"], batch_size=500)
            batch.clear()

    if batch:
        KnowledgeUploadChunk.objects.bulk_update(batch, ["metadata"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("knowledge", "0005_remove_knowledgecollection_business_profile_and_more"),
    ]

    operations = [
        migrations.RunPython(fix_table_row_evidence_groups, migrations.RunPython.noop),
    ]
