from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0045_agentprofile_mcp_default_approval_mode_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentprofile",
            name="mcp_gateway_mode",
            field=models.BooleanField(
                null=True,
                blank=True,
                help_text=(
                    "Optional override for MCP gateway mode. "
                    "When enabled, the agent uses a small gateway tool surface for external MCP tools "
                    "instead of inlining every remote tool schema into the LLM prompt."
                ),
            ),
        ),
    ]

