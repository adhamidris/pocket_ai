from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_remove_agentprofile_allowed_collections"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="agentprofile",
            name="agent_user_status_idx",
        ),
        migrations.RemoveIndex(
            model_name="agentprofile",
            name="agent_status_updated_idx",
        ),
        migrations.RemoveField(
            model_name="agentprofile",
            name="status",
        ),
    ]
