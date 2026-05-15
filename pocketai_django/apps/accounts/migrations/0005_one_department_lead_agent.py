from __future__ import annotations

from django.db import migrations, models


def archive_duplicate_department_leads(apps, schema_editor):
    AgentProfile = apps.get_model("accounts", "AgentProfile")
    duplicates = (
        AgentProfile.objects.filter(
            department_id__isnull=False,
            agent_type="department_lead",
            status="active",
        )
        .order_by("department_id", "created_at", "id")
        .values_list("department_id", "id")
    )
    seen = set()
    archive_ids = []
    for department_id, agent_id in duplicates:
        if department_id in seen:
            archive_ids.append(agent_id)
        else:
            seen.add(department_id)
    if archive_ids:
        AgentProfile.objects.filter(id__in=archive_ids).update(status="archived")


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_agentprofile_agent_type_and_more"),
    ]

    operations = [
        migrations.RunPython(archive_duplicate_department_leads, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="agentprofile",
            constraint=models.UniqueConstraint(
                condition=models.Q(agent_type="department_lead", department__isnull=False, status="active"),
                fields=("department", "agent_type"),
                name="agent_unique_active_dept_lead",
            ),
        ),
    ]
