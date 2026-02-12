from django.db import migrations


_FORWARD_SQL = [
    # Vector ANN index for primary retrieval table.
    """
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_am WHERE amname = 'hnsw') THEN
        EXECUTE '
          CREATE INDEX IF NOT EXISTS accounts_chunk_embedding_hnsw
          ON accounts_knowledge_upload_chunk
          USING hnsw (embedding vector_cosine_ops)
          WHERE embedding IS NOT NULL
        ';
      ELSIF EXISTS (SELECT 1 FROM pg_am WHERE amname = 'ivfflat') THEN
        EXECUTE '
          CREATE INDEX IF NOT EXISTS accounts_chunk_embedding_ann_idx
          ON accounts_knowledge_upload_chunk
          USING ivfflat (embedding vector_cosine_ops)
          WITH (lists = 256)
        ';
      END IF;
    END
    $$;
    """,
    # Vector ANN index for shadow chunks (used by evaluation / alternate retrieval paths).
    """
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_am WHERE amname = 'hnsw') THEN
        EXECUTE '
          CREATE INDEX IF NOT EXISTS kn_shadow_chunk_embedding_hnsw
          ON accounts_knowledge_upload_shadow_chunk
          USING hnsw (embedding vector_cosine_ops)
          WHERE embedding IS NOT NULL
        ';
      ELSIF EXISTS (SELECT 1 FROM pg_am WHERE amname = 'ivfflat') THEN
        EXECUTE '
          CREATE INDEX IF NOT EXISTS kn_shadow_chunk_embedding_ann_idx
          ON accounts_knowledge_upload_shadow_chunk
          USING ivfflat (embedding vector_cosine_ops)
          WITH (lists = 128)
        ';
      END IF;
    END
    $$;
    """,
    # Trigram fallback for fuzzy lexical matching on chunk content.
    """
    CREATE INDEX IF NOT EXISTS accounts_chunk_content_trgm_v2
    ON accounts_knowledge_upload_chunk
    USING gin (content gin_trgm_ops)
    """,
    # FTS index used by SearchVector/SearchQuery path.
    """
    CREATE INDEX IF NOT EXISTS accounts_chunk_content_fts
    ON accounts_knowledge_upload_chunk
    USING gin (to_tsvector('simple', coalesce(content, '')))
    """,
    # Metadata alias-string search acceleration.
    """
    CREATE INDEX IF NOT EXISTS accounts_chunk_alias_string_trgm
    ON accounts_knowledge_upload_chunk
    USING gin ((metadata ->> 'alias_string') gin_trgm_ops)
    WHERE metadata ? 'alias_string'
    """,
]


_REVERSE_SQL = [
    "DROP INDEX IF EXISTS accounts_chunk_alias_string_trgm",
    "DROP INDEX IF EXISTS accounts_chunk_content_fts",
    "DROP INDEX IF EXISTS accounts_chunk_content_trgm_v2",
    "DROP INDEX IF EXISTS kn_shadow_chunk_embedding_ann_idx",
    "DROP INDEX IF EXISTS kn_shadow_chunk_embedding_hnsw",
    "DROP INDEX IF EXISTS accounts_chunk_embedding_ann_idx",
    "DROP INDEX IF EXISTS accounts_chunk_embedding_hnsw",
]


def _run_sql_batch(schema_editor, statements: list[str]) -> None:
    with schema_editor.connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    _run_sql_batch(schema_editor, _FORWARD_SQL)


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    _run_sql_batch(schema_editor, _REVERSE_SQL)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("knowledge", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

