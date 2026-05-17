# Generated for pre-production assistant profile simplification.

from django.db import migrations, models


def archive_duplicate_active_default_assistants(apps, schema_editor):
    AgentProfile = apps.get_model("accounts", "AgentProfile")
    active_business_ids = (
        AgentProfile.objects.filter(status="active")
        .values_list("business_profile_id", flat=True)
        .distinct()
    )
    for business_id in active_business_ids:
        active_agents = list(
            AgentProfile.objects.filter(business_profile_id=business_id, status="active")
            .order_by("created_at", "id")
            .values_list("id", flat=True)
        )
        if len(active_agents) <= 1:
            continue
        AgentProfile.objects.filter(id__in=active_agents[1:]).update(status="archived")


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_agentprofile_agent_type_and_more"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="agentprofile",
            name="agent_unique_active_main",
        ),
        migrations.RemoveIndex(
            model_name="agentprofile",
            name="agent_business_type_idx",
        ),
        migrations.RemoveIndex(
            model_name="agentprofile",
            name="agent_manager_status_idx",
        ),
        migrations.RemoveField(
            model_name="agentprofile",
            name="agent_type",
        ),
        migrations.RemoveField(
            model_name="agentprofile",
            name="allow_custom_kpi_weighting",
        ),
        migrations.RemoveField(
            model_name="agentprofile",
            name="can_manage_tasks",
        ),
        migrations.RemoveField(
            model_name="agentprofile",
            name="custom_kpis",
        ),
        migrations.RemoveField(
            model_name="agentprofile",
            name="escalation_rule",
        ),
        migrations.RemoveField(
            model_name="agentprofile",
            name="instructions",
        ),
        migrations.RemoveField(
            model_name="agentprofile",
            name="manager_agent",
        ),
        migrations.RemoveField(
            model_name="agentprofile",
            name="responsibilities",
        ),
        migrations.RemoveField(
            model_name="agentprofile",
            name="role",
        ),
        migrations.RemoveField(
            model_name="agentprofile",
            name="selected_kpis",
        ),
        migrations.RemoveField(
            model_name="agentprofile",
            name="traits",
        ),
        migrations.RunPython(archive_duplicate_active_default_assistants, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="agentprofile",
            index=models.Index(fields=["business_profile", "status"], name="agent_business_status_idx"),
        ),
        migrations.AddConstraint(
            model_name="agentprofile",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "active")),
                fields=("business_profile",),
                name="agent_unique_active_default",
            ),
        ),
    ]
