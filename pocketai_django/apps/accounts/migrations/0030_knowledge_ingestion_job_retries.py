from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0029_chunk_content_fts_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="knowledgeingestionjob",
            name="attempt_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="knowledgeingestionjob",
            name="lease_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="knowledgeingestionjob",
            name="max_attempts",
            field=models.PositiveIntegerField(default=3),
        ),
        migrations.AddField(
            model_name="knowledgeingestionjob",
            name="run_after",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="knowledgeingestionjob",
            index=models.Index(fields=["status", "run_after"], name="knowledge_job_run_after_idx"),
        ),
        migrations.AddIndex(
            model_name="knowledgeingestionjob",
            index=models.Index(fields=["status", "lease_expires_at"], name="knowledge_job_lease_idx"),
        ),
    ]

