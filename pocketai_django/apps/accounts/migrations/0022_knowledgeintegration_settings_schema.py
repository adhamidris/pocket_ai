from django.db import migrations, models

import apps.accounts.models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0021_tablecell_trgm_indexes"),
    ]

    operations = [
        migrations.AlterField(
            model_name="knowledgeintegration",
            name="settings",
            field=models.JSONField(
                blank=True,
                default=apps.accounts.models.default_knowledge_integration_settings,
            ),
        ),
        migrations.AlterField(
            model_name="knowledgeupload",
            name="source_uid",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Identifier for deduping integration resources (e.g., drive_file:sheet_gid).",
                max_length=255,
            ),
        ),
    ]
