from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0026_identifier_column_memory"),
    ]

    operations = [
        migrations.AlterField(
            model_name="knowledgeuploadpage",
            name="content_type",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
    ]

