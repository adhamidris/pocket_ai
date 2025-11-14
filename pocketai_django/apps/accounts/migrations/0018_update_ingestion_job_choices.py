from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0017_phase4_indexes"),
    ]

    operations = [
        migrations.AlterField(
            model_name="knowledgeingestionjob",
            name="job_type",
            field=models.CharField(
                max_length=24,
                choices=[
                    ("ingest", "Initial Ingest"),
                    ("embed", "Embedding"),
                    ("rebuild", "Rebuild"),
                    ("delete", "Delete"),
                    ("sync", "Sync"),
                ],
            ),
        ),
        migrations.AlterField(
            model_name="knowledgeingestionjob",
            name="status",
            field=models.CharField(
                max_length=24,
                default="queued",
                choices=[
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("deferred", "Deferred"),
                    ("completed", "Completed"),
                    ("failed", "Failed"),
                    ("cancelled", "Cancelled"),
                ],
            ),
        ),
    ]
