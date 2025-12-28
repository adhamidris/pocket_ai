from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0033_tenant_row_level_security"),
    ]

    operations = [
        migrations.RenameField(
            model_name="knowledgeentity",
            old_name="chunk",
            new_name="chunk_id",
        ),
        migrations.AlterField(
            model_name="knowledgeentity",
            name="chunk_id",
            field=models.UUIDField(null=True, blank=True, db_index=True),
        ),
    ]
