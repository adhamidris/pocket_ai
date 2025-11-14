from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0013_enable_vector_and_indexes"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="knowledgeuploadchunk",
            index=models.Index(fields=["upload", "chunk_index"], name="knowledge_chunk_window_idx"),
        ),
    ]
