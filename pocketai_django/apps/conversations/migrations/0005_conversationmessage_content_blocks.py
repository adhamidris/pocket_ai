from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0004_conversation_tool_approval"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversationmessage",
            name="content_blocks",
            field=models.JSONField(blank=True, default=list),
        ),
    ]

