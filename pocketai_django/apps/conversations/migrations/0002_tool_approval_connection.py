from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0001_initial"),
        ("mcp", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversationtoolapproval",
            name="connection",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tool_approvals",
                to="mcp.mcpconnection",
            ),
        ),
        migrations.AddIndex(
            model_name="conversationtoolapproval",
            index=models.Index(fields=["connection", "status"], name="conv_tool_approval_conn_idx"),
        ),
    ]
