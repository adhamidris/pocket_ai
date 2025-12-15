from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0027_alter_knowledgeuploadpage_content_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="identifiercolumnmapping",
            name="is_required",
            field=models.BooleanField(
                blank=True,
                default=None,
                help_text=(
                    "Override for identifier.is_required. When true, queries for this upload must be scoped by this "
                    "identifier; when false, it is optional for this upload."
                ),
                null=True,
            ),
        ),
    ]

