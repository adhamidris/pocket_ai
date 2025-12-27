from __future__ import annotations

import uuid

from django.conf import settings
from django.db import migrations, models
from pgvector.django import VectorField


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0031_alter_knowledgeauditevent_action"),
    ]

    operations = [
        migrations.CreateModel(
            name="KnowledgeUploadShadowChunk",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("chunk_index", models.PositiveIntegerField()),
                ("content", models.TextField()),
                ("token_count", models.PositiveIntegerField(default=0)),
                ("embedding", VectorField(dimensions=settings.EMBED_DIM, null=True, blank=True)),
                ("metadata", models.JSONField(default=dict, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "business_profile",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="knowledge_shadow_chunks",
                        to="accounts.businessprofile",
                    ),
                ),
                (
                    "upload",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="shadow_chunks",
                        to="accounts.knowledgeupload",
                    ),
                ),
            ],
            options={
                "db_table": "accounts_knowledge_upload_shadow_chunk",
                "ordering": ("upload_id", "chunk_index"),
            },
        ),
        migrations.AddIndex(
            model_name="knowledgeuploadshadowchunk",
            index=models.Index(fields=["upload", "chunk_index"], name="kn_shadow_chunk_window_idx"),
        ),
        migrations.AddIndex(
            model_name="knowledgeuploadshadowchunk",
            index=models.Index(fields=["business_profile", "chunk_index"], name="kn_shadow_biz_idx"),
        ),
        migrations.AddIndex(
            model_name="knowledgeuploadshadowchunk",
            index=models.Index(fields=["business_profile", "upload"], name="kn_shadow_biz_upload_idx"),
        ),
        migrations.AddConstraint(
            model_name="knowledgeuploadshadowchunk",
            constraint=models.UniqueConstraint(
                fields=("upload", "chunk_index"),
                name="knowledge_shadow_chunk_unique_index",
            ),
        ),
    ]
