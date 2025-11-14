from django.contrib.postgres.indexes import GinIndex
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("accounts", "0020_business_feature_flags"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
CREATE INDEX CONCURRENTLY IF NOT EXISTS knowledge_cell_raw_text_trgm
ON accounts_knowledge_upload_table_cell
USING gin (raw_text gin_trgm_ops)
""",
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS knowledge_cell_raw_text_trgm",
                )
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="knowledgeuploadtablecell",
                    index=GinIndex(
                        fields=["raw_text"],
                        name="knowledge_cell_raw_text_trgm",
                        opclasses=["gin_trgm_ops"],
                    ),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
CREATE INDEX CONCURRENTLY IF NOT EXISTS knowledge_cell_column_key_idx
ON accounts_knowledge_upload_table_cell (column_key)
""",
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS knowledge_cell_column_key_idx",
                )
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="knowledgeuploadtablecell",
                    index=models.Index(
                        fields=["column_key"],
                        name="knowledge_cell_column_key_idx",
                    ),
                )
            ],
        ),
    ]
