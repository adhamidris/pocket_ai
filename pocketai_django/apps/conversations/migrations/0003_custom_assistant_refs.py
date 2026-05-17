from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("assistants", "0001_initial"),
        ("conversations", "0002_tool_approval_connection"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="custom_assistant",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional Custom Assistant this chat session belongs to.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sessions",
                to="assistants.customassistant",
            ),
        ),
        migrations.AddField(
            model_name="memoryitem",
            name="custom_assistant",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="memory_items",
                to="assistants.customassistant",
            ),
        ),
        migrations.AddIndex(
            model_name="conversation",
            index=models.Index(fields=["custom_assistant", "last_activity_at"], name="conv_assistant_activity_idx"),
        ),
        migrations.AddIndex(
            model_name="memoryitem",
            index=models.Index(fields=["custom_assistant", "status", "updated_at"], name="mem_asst_status_time_idx"),
        ),
    ]
