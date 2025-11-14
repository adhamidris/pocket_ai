from django.contrib.postgres.indexes import GinIndex
from django.db import migrations, models
import django.db.models.deletion


def forward_fill_chunk_business(apps, schema_editor):
    schema_editor.execute(
        """
        UPDATE accounts_knowledge_upload_chunk AS c
        SET business_profile_id = u.business_profile_id
        FROM accounts_knowledge_upload AS u
        WHERE c.upload_id = u.id AND c.business_profile_id IS NULL
        """
    )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("accounts", "0016_alter_knowledgeuploadchunk_embedding"),
    ]

    business_index = models.Index(fields=["business_profile", "chunk_index"], name="kn_chunk_biz_idx")
    business_upload_index = models.Index(fields=["business_profile", "upload"], name="kn_chunk_biz_upload_idx")
    alias_trgm_index = models.Index(
        fields=["alias_normalized"],
        name="kn_alias_norm_len_idx",
        condition=models.Q(alias_normalized__regex=r".{5,}"),
    )
    alias_gin_index = GinIndex(
        name="kn_alias_norm_trgm",
        fields=["alias_normalized"],
        opclasses=["gin_trgm_ops"],
    )

    operations = [
        migrations.AddField(
            model_name="knowledgeuploadchunk",
            name="business_profile",
            field=models.ForeignKey(
                related_name="knowledge_chunks",
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="accounts.businessprofile",
            ),
        ),
        migrations.RunPython(forward_fill_chunk_business, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="knowledgeuploadchunk",
            name="business_profile",
            field=models.ForeignKey(
                related_name="knowledge_chunks",
                null=False,
                on_delete=django.db.models.deletion.CASCADE,
                to="accounts.businessprofile",
            ),
        ),
        migrations.RunSQL(
            sql="DROP INDEX IF EXISTS accounts_chunk_embedding_hnsw",
            reverse_sql="""
                CREATE INDEX IF NOT EXISTS accounts_chunk_embedding_hnsw
                ON accounts_knowledge_upload_chunk
                USING hnsw (embedding vector_cosine_ops)
            """,
        ),
        migrations.RunSQL(
            sql="DROP INDEX IF EXISTS accounts_chunk_content_trgm",
            reverse_sql="CREATE INDEX IF NOT EXISTS accounts_chunk_content_trgm ON accounts_knowledge_upload_chunk USING gin (content gin_trgm_ops)",
        ),
        migrations.RunSQL(
            sql="""
CREATE INDEX CONCURRENTLY IF NOT EXISTS accounts_chunk_embedding_ann_idx
ON accounts_knowledge_upload_chunk
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 256)
""",
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS accounts_chunk_embedding_ann_idx",
        ),
        migrations.RunSQL(
            sql="""
CREATE INDEX CONCURRENTLY IF NOT EXISTS accounts_chunk_content_trgm_v2
ON accounts_knowledge_upload_chunk
USING gin (content gin_trgm_ops)
""",
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS accounts_chunk_content_trgm_v2",
        ),
        migrations.RunSQL(
            sql="""
CREATE INDEX CONCURRENTLY IF NOT EXISTS accounts_chunk_alias_string_trgm
ON accounts_knowledge_upload_chunk
USING gin ((metadata ->> 'alias_string') gin_trgm_ops)
WHERE metadata ? 'alias_string'
""",
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS accounts_chunk_alias_string_trgm",
        ),
        migrations.RunSQL(
            sql="""
CREATE INDEX CONCURRENTLY IF NOT EXISTS kn_chunk_biz_idx
ON accounts_knowledge_upload_chunk (business_profile_id, chunk_index)
""",
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS kn_chunk_biz_idx",
            state_operations=[
                migrations.AddIndex(
                    model_name="knowledgeuploadchunk",
                    index=business_index,
                )
            ],
        ),
        migrations.RunSQL(
            sql="""
CREATE INDEX CONCURRENTLY IF NOT EXISTS kn_chunk_biz_upload_idx
ON accounts_knowledge_upload_chunk (business_profile_id, upload_id)
""",
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS kn_chunk_biz_upload_idx",
            state_operations=[
                migrations.AddIndex(
                    model_name="knowledgeuploadchunk",
                    index=business_upload_index,
                )
            ],
        ),
        migrations.RunSQL(
            sql="""
CREATE INDEX CONCURRENTLY IF NOT EXISTS kn_alias_norm_trgm
ON accounts_knowledge_alias
USING gin (alias_normalized gin_trgm_ops)
""",
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS kn_alias_norm_trgm",
            state_operations=[
                migrations.AddIndex(
                    model_name="knowledgealias",
                    index=alias_gin_index,
                )
            ],
        ),
        migrations.RunSQL(
            sql="""
CREATE INDEX CONCURRENTLY IF NOT EXISTS kn_alias_norm_len_idx
ON accounts_knowledge_alias (alias_normalized)
WHERE length(alias_normalized) >= 5
""",
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS kn_alias_norm_len_idx",
            state_operations=[
                migrations.AddIndex(
                    model_name="knowledgealias",
                    index=alias_trgm_index,
                )
            ],
        ),
        migrations.RunSQL(
            sql="""
CREATE OR REPLACE PROCEDURE refresh_chunk_embedding_index()
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = 'accounts_chunk_embedding_ann_idx') THEN
        EXECUTE 'REINDEX INDEX CONCURRENTLY accounts_chunk_embedding_ann_idx';
    ELSE
        EXECUTE 'CREATE INDEX CONCURRENTLY accounts_chunk_embedding_ann_idx ON accounts_knowledge_upload_chunk USING ivfflat (embedding vector_cosine_ops) WITH (lists = 256)';
    END IF;
END;
$$
""",
            reverse_sql="DROP PROCEDURE IF EXISTS refresh_chunk_embedding_index()",
        ),
    ]
