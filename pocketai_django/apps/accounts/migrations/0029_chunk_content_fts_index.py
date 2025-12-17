from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("accounts", "0028_identifier_column_required_override"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
CREATE INDEX CONCURRENTLY IF NOT EXISTS accounts_chunk_content_fts
ON accounts_knowledge_upload_chunk
USING gin (to_tsvector('simple', coalesce(content, '')))
""",
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS accounts_chunk_content_fts",
        )
    ]
