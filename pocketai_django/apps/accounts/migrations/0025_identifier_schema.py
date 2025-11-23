from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0024_upload_display_name_trgm"),
    ]

    operations = [
        migrations.CreateModel(
            name="IdentifierSchema",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("key", models.CharField(help_text="Machine-friendly identifier key (e.g., email, phone, customer_id).", max_length=80)),
                ("display_name", models.CharField(help_text="Human-readable label shown in admin surfaces.", max_length=160)),
                ("status", models.CharField(choices=[("proposed", "Proposed"), ("active", "Active"), ("disabled", "Disabled")], default="proposed", help_text="Activation status for retrieval guardrails.", max_length=24)),
                ("source", models.CharField(choices=[("user", "User"), ("ai", "AI")], default="user", help_text="Whether this identifier was user-defined or proposed by AI.", max_length=16)),
                ("is_required", models.BooleanField(default=True, help_text="If true, retrieval must be scoped by this identifier when present.")),
                ("description", models.TextField(blank=True, default="")),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("business_profile", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="identifier_schemas", to="accounts.businessprofile")),
            ],
            options={
                "db_table": "accounts_identifier_schema",
                "ordering": ("-updated_at",),
            },
        ),
        migrations.CreateModel(
            name="IdentifierColumnMapping",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("sheet_name", models.CharField(blank=True, default="", max_length=255)),
                ("column_name", models.CharField(max_length=255)),
                ("column_normalized", models.CharField(db_index=True, max_length=255)),
                ("status", models.CharField(choices=[("proposed", "Proposed"), ("active", "Active"), ("disabled", "Disabled")], default="proposed", max_length=24)),
                ("source", models.CharField(choices=[("user", "User"), ("ai", "AI")], default="user", max_length=16)),
                ("confidence", models.FloatField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("business_profile", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="identifier_columns", to="accounts.businessprofile")),
                ("identifier", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="column_mappings", to="accounts.identifierschema")),
                ("upload", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="identifier_columns", to="accounts.knowledgeupload")),
            ],
            options={
                "db_table": "accounts_identifier_column",
                "ordering": ("-updated_at",),
            },
        ),
        migrations.AddIndex(
            model_name="identifierschema",
            index=models.Index(fields=["business_profile", "status"], name="identifier_schema_status_idx"),
        ),
        migrations.AddConstraint(
            model_name="identifierschema",
            constraint=models.UniqueConstraint(fields=("business_profile", "key"), name="identifier_schema_unique_key"),
        ),
        migrations.AddIndex(
            model_name="identifiercolumnmapping",
            index=models.Index(fields=["business_profile", "column_normalized"], name="identifier_column_norm_idx"),
        ),
        migrations.AddIndex(
            model_name="identifiercolumnmapping",
            index=models.Index(fields=["business_profile", "upload"], name="identifier_column_upload_idx"),
        ),
        migrations.AddConstraint(
            model_name="identifiercolumnmapping",
            constraint=models.UniqueConstraint(fields=("identifier", "upload", "column_normalized", "sheet_name"), name="identifier_column_unique_scope"),
        ),
    ]
