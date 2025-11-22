# apps/accounts/migrations/00XX_convert_embedding_to_vector.py
from django.conf import settings
from django.db import migrations
from pgvector.django import VectorField

VECTOR_DIM = getattr(settings, "EMBED_DIM", 384)

def forwards(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        # Drop the column if it exists as JSONB
        cursor.execute("""
            ALTER TABLE accounts_knowledge_upload_chunk 
            DROP COLUMN IF EXISTS embedding CASCADE
        """)
        
        # Add it back as vector type
        cursor.execute(f"""
            ALTER TABLE accounts_knowledge_upload_chunk 
            ADD COLUMN embedding vector({VECTOR_DIM})
        """)

def backwards(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            ALTER TABLE accounts_knowledge_upload_chunk 
            DROP COLUMN IF EXISTS embedding CASCADE
        """)
        cursor.execute("""
            ALTER TABLE accounts_knowledge_upload_chunk 
            ADD COLUMN embedding JSONB
        """)

class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0011_layout_structured_models"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
