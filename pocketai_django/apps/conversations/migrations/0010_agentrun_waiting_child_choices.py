from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0009_workflow_agent_sessions_and_checkpoints"),
    ]

    operations = [
        migrations.AlterField(
            model_name="agentrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("waiting_user", "Waiting for user"),
                    ("waiting_approval", "Waiting for approval"),
                    ("waiting_child", "Waiting for child run"),
                    ("waiting_external", "Waiting for external"),
                    ("paused", "Paused"),
                    ("completed", "Completed"),
                    ("failed", "Failed"),
                    ("cancelled", "Cancelled"),
                ],
                db_index=True,
                default="queued",
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="agentrunevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("progress", "Progress"),
                    ("needs_user", "Needs user"),
                    ("needs_approval", "Needs approval"),
                    ("needs_child", "Needs child run"),
                    ("result", "Result"),
                    ("error", "Error"),
                    ("paused", "Paused"),
                    ("cancelled", "Cancelled"),
                ],
                max_length=24,
            ),
        ),
    ]
