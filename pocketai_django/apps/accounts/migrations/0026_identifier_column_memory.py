from __future__ import annotations

import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0025_identifier_schema"),
    ]

    operations = [
        migrations.CreateModel(
            name="IdentifierColumnMemory",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("identifier_key", models.CharField(max_length=80)),
                ("normalized_column", models.CharField(db_index=True, max_length=255)),
                ("pattern_signature", models.CharField(blank=True, default="", max_length=255)),
                ("last_confidence", models.FloatField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "business_profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="identifier_memories",
                        to="accounts.businessprofile",
                    ),
                ),
                (
                    "identifier_schema",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="identifier_memories",
                        to="accounts.identifierschema",
                    ),
                ),
            ],
            options={
                "db_table": "accounts_identifier_memory",
                "ordering": ("-updated_at",),
            },
        ),
        migrations.AddConstraint(
            model_name="identifiercolumnmemory",
            constraint=models.UniqueConstraint(
                fields=("business_profile", "normalized_column", "pattern_signature"),
                name="identifier_memory_unique_signature",
            ),
        ),
        migrations.AddIndex(
            model_name="identifiercolumnmemory",
            index=models.Index(
                fields=["business_profile", "normalized_column"],
                name="identifier_memory_column_idx",
            ),
        ),
    ]
