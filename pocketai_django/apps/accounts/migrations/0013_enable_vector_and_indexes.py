# apps/accounts/migrations/00xx_enable_vector_and_indexes.py
from django.conf import settings
from django.db import migrations, models

VECTOR_DIM = getattr(settings, "EMBED_DIM", 384)

EXTENSIONS_SQL = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
]

INDEX_SQL = [
    # ANN index on vector column (cosine ops). Use HNSW (pgvector >=0.5) or IVFFLAT.
    """
    CREATE INDEX IF NOT EXISTS accounts_chunk_embedding_hnsw
    ON accounts_knowledge_upload_chunk
    USING hnsw (embedding vector_cosine_ops)
    """,
    # Trigram GIN on content for fast, ranked keyword fallback
    """
    CREATE INDEX IF NOT EXISTS accounts_chunk_content_trgm
    ON accounts_knowledge_upload_chunk
    USING gin (content gin_trgm_ops)
    """,
]

def forwards(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for stmt in EXTENSIONS_SQL:
            cursor.execute(stmt)
    # If you’re migrating from JSON -> VectorField and have old data, either:
    # a) re-ingest/reenbed, or
    # b) write a USING cast. Re-ingesting is simpler and safer.
    # Indexes
    with schema_editor.connection.cursor() as cursor:
        for stmt in INDEX_SQL:
            cursor.execute(stmt)

def backwards(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS accounts_chunk_embedding_hnsw")
        cursor.execute("DROP INDEX IF EXISTS accounts_chunk_content_trgm")

class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0012_convert_embedding_to_vector"),
    ]
    operations = [
        migrations.RunPython(forwards, backwards),
    ]
