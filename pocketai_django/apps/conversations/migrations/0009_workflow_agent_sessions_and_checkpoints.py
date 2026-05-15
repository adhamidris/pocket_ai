from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


def migrate_workflow_threads_to_sessions(apps, schema_editor):
    Conversation = apps.get_model("conversations", "Conversation")
    AgentWorkflow = apps.get_model("conversations", "AgentWorkflow")
    db_alias = schema_editor.connection.alias

    for workflow in AgentWorkflow.objects.using(db_alias).exclude(conversation_id=None).iterator():
        Conversation.objects.using(db_alias).filter(id=workflow.conversation_id, workflow_id__isnull=True).update(
            workflow_id=workflow.id
        )

    for conversation in Conversation.objects.using(db_alias).filter(workflow_id__isnull=True).iterator():
        metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
        workflow_id = str(metadata.get("workflow_id") or metadata.get("workflowId") or "").strip()
        if not workflow_id:
            continue
        try:
            workflow_uuid = uuid.UUID(workflow_id)
        except (TypeError, ValueError):
            continue
        if AgentWorkflow.objects.using(db_alias).filter(id=workflow_uuid).exists():
            Conversation.objects.using(db_alias).filter(id=conversation.id).update(workflow_id=workflow_uuid)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("conversations", "0008_delete_operational_id_memories"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="workflow",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional Workflow Agent this chat session belongs to.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sessions",
                to="conversations.agentworkflow",
            ),
        ),
        migrations.AddIndex(
            model_name="conversation",
            index=models.Index(fields=["workflow", "last_activity_at"], name="conv_workflow_activity_idx"),
        ),
        migrations.CreateModel(
            name="AgentRunCheckpoint",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("approval", "Approval"), ("user_input", "User input"), ("child_run", "Child run"), ("external", "External")], db_index=True, max_length=24)),
                ("status", models.CharField(choices=[("open", "Open"), ("resolved", "Resolved"), ("expired", "Expired"), ("cancelled", "Cancelled")], db_index=True, default="open", max_length=24)),
                ("title", models.CharField(blank=True, default="", max_length=240)),
                ("prompt", models.TextField(blank=True, default="")),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("resolution", models.JSONField(blank=True, default=dict)),
                ("expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("business_profile", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="agent_run_checkpoints", to="accounts.businessprofile")),
                ("child_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="parent_checkpoints", to="conversations.agentrun")),
                ("conversation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="agent_run_checkpoints", to="conversations.conversation")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_agent_run_checkpoints", to=settings.AUTH_USER_MODEL)),
                ("resolved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="resolved_agent_run_checkpoints", to=settings.AUTH_USER_MODEL)),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="checkpoints", to="conversations.agentrun")),
                ("workflow", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="checkpoints", to="conversations.agentworkflow")),
            ],
            options={
                "db_table": "conversations_agent_run_checkpoint",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddIndex(
            model_name="agentruncheckpoint",
            index=models.Index(fields=["business_profile", "status", "updated_at"], name="checkpoint_biz_status_idx"),
        ),
        migrations.AddIndex(
            model_name="agentruncheckpoint",
            index=models.Index(fields=["workflow", "status", "updated_at"], name="checkpoint_wf_status_idx"),
        ),
        migrations.AddIndex(
            model_name="agentruncheckpoint",
            index=models.Index(fields=["run", "status", "created_at"], name="checkpoint_run_status_idx"),
        ),
        migrations.AddIndex(
            model_name="agentruncheckpoint",
            index=models.Index(fields=["status", "expires_at"], name="checkpoint_expiry_idx"),
        ),
        migrations.RunPython(migrate_workflow_threads_to_sessions, migrations.RunPython.noop),
    ]
