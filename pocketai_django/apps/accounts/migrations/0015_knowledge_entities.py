import uuid

import django.db.models.deletion
from django.contrib.postgres.indexes import GinIndex
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_knowledge_chunk_window_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="businessprofile",
            name="metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name="KnowledgeEntity",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("entity_type", models.CharField(blank=True, default="", max_length=120)),
                ("entity_name", models.CharField(blank=True, default="", max_length=255)),
                ("primary_label", models.CharField(blank=True, default="", max_length=255)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "business_profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="knowledge_entities",
                        to="accounts.businessprofile",
                    ),
                ),
                (
                    "chunk",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="entity_record",
                        to="accounts.knowledgeuploadchunk",
                    ),
                ),
                (
                    "upload",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="entities",
                        to="accounts.knowledgeupload",
                    ),
                ),
            ],
            options={
                "db_table": "accounts_knowledge_entity",
            },
        ),
        migrations.CreateModel(
            name="KnowledgeAlias",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("alias_raw", models.CharField(max_length=255)),
                ("alias_normalized", models.CharField(db_index=True, max_length=255)),
                ("alias_search_vector", models.CharField(blank=True, default="", max_length=255)),
                ("source", models.CharField(blank=True, default="", max_length=60)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "business_profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="knowledge_aliases",
                        to="accounts.businessprofile",
                    ),
                ),
                (
                    "entity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="aliases",
                        to="accounts.knowledgeentity",
                    ),
                ),
            ],
            options={
                "db_table": "accounts_knowledge_alias",
            },
        ),
        migrations.AddIndex(
            model_name="knowledgeentity",
            index=models.Index(fields=["business_profile", "entity_type"], name="knowledge_entity_type_idx"),
        ),
        migrations.AddIndex(
            model_name="knowledgeentity",
            index=models.Index(fields=["upload"], name="knowledge_entity_upload_idx"),
        ),
        migrations.AddIndex(
            model_name="knowledgealias",
            index=models.Index(fields=["business_profile", "alias_normalized"], name="kn_alias_biz_norm_idx"),
        ),
        migrations.AddIndex(
            model_name="knowledgealias",
            index=GinIndex(fields=["alias_search_vector"], name="knowledge_alias_search_gin", opclasses=["gin_trgm_ops"]),
        ),
        migrations.AddConstraint(
            model_name="knowledgealias",
            constraint=models.UniqueConstraint(
                fields=["entity", "alias_normalized"],
                name="knowledge_alias_unique_entity_alias",
            ),
        ),
    ]
