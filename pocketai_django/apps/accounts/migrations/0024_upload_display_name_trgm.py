from django.contrib.postgres.indexes import GinIndex
from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("accounts", "0023_secure_integration_credentials"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
CREATE INDEX CONCURRENTLY IF NOT EXISTS upload_display_name_trgm
ON accounts_knowledge_upload
USING gin (display_name gin_trgm_ops)
""",
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS upload_display_name_trgm",
                )
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="knowledgeupload",
                    index=GinIndex(
                        fields=["display_name"],
                        name="upload_display_name_trgm",
                        opclasses=["gin_trgm_ops"],
                    ),
                )
            ],
        ),
    ]
