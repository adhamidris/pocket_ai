from django.db import migrations, models
from django.utils import timezone


def delete_archived_agent_workflows(apps, schema_editor):
    AgentWorkflow = apps.get_model("conversations", "AgentWorkflow")
    AgentRun = apps.get_model("conversations", "AgentRun")
    Conversation = apps.get_model("conversations", "Conversation")

    dedicated_thread_ids = []
    archived_workflow_ids = []
    archived_workflows = AgentWorkflow.objects.filter(status="archived").values(
        "id",
        "business_profile_id",
        "conversation_id",
    )
    for workflow in archived_workflows:
        archived_workflow_ids.append(workflow["id"])
        conversation_id = workflow.get("conversation_id")
        if not conversation_id:
            continue
        if AgentWorkflow.objects.filter(conversation_id=conversation_id).exclude(id=workflow["id"]).exists():
            continue
        row = Conversation.objects.filter(id=conversation_id).values("business_profile_id", "metadata").first()
        if not row or row["business_profile_id"] != workflow["business_profile_id"]:
            continue
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else {}
        metadata_workflow_id = str(metadata.get("workflow_id") or metadata.get("workflowId") or "").strip()
        metadata_type = str(metadata.get("type") or metadata.get("purpose") or "").strip().lower()
        if metadata_workflow_id == str(workflow["id"]) or metadata_type == "workflow_thread":
            dedicated_thread_ids.append(conversation_id)

    if archived_workflow_ids:
        AgentRun.objects.filter(
            workflow_id__in=archived_workflow_ids,
            status__in=["queued", "running", "waiting_user", "waiting_approval", "waiting_external", "paused"],
        ).update(
            status="cancelled",
            finished_at=timezone.now(),
            lease_expires_at=None,
            run_after=None,
            error_detail="Workflow deleted",
            updated_at=timezone.now(),
        )
    AgentWorkflow.objects.filter(status="archived").delete()
    if dedicated_thread_ids:
        Conversation.objects.filter(id__in=dedicated_thread_ids).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0006_workflow_policy_notifications"),
    ]

    operations = [
        migrations.RunPython(delete_archived_agent_workflows, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="agentworkflow",
            name="status",
            field=models.CharField(
                choices=[("draft", "Draft"), ("active", "Active"), ("paused", "Paused")],
                db_index=True,
                default="draft",
                max_length=24,
            ),
        ),
    ]
