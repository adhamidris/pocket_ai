from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("agent_runs", "0003_initial"),
        ("conversations", "0003_custom_assistant_refs"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentrequest",
            name="agent_run",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional originating AgentRun (for background tasks that need another agent).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="agent_requests",
                to="agent_runs.agentrun",
            ),
        ),
        migrations.AddField(
            model_name="memoryitem",
            name="run",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="memory_items",
                to="agent_runs.agentrun",
            ),
        ),
        migrations.AddIndex(
            model_name="memoryitem",
            index=models.Index(fields=["run", "created_at"], name="memory_run_created_idx"),
        ),
    ]
