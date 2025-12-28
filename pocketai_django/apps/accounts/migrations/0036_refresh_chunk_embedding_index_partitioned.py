from django.db import migrations


def refresh_partitioned_embedding_index(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
CREATE OR REPLACE PROCEDURE refresh_chunk_embedding_index()
LANGUAGE plpgsql
AS $$
DECLARE
    idx integer;
    index_name text;
BEGIN
    FOR idx IN 0..63 LOOP
        index_name := 'kn_chunk_emb_hnsw_' || idx::text;
        IF EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE schemaname = 'public' AND indexname = index_name
        ) THEN
            EXECUTE 'REINDEX INDEX CONCURRENTLY ' || quote_ident(index_name);
        ELSE
            EXECUTE
                'CREATE INDEX CONCURRENTLY ' || quote_ident(index_name) ||
                ' ON accounts_knowledge_upload_chunk_p' || idx::text ||
                ' USING hnsw (embedding vector_cosine_ops)';
        END IF;
    END LOOP;
END;
$$
"""
    )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("accounts", "0035_partition_knowledge_chunks"),
    ]

    operations = [
        migrations.RunPython(refresh_partitioned_embedding_index, migrations.RunPython.noop),
    ]
