from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def populate_assistant_workflow_kind(apps, schema_editor):
    AssistantWorkflow = apps.get_model("conversations", "AssistantWorkflow")
    db_alias = schema_editor.connection.alias
    AssistantWorkflow.objects.using(db_alias).filter(trigger_type="manual").update(kind="custom_assistant")
    AssistantWorkflow.objects.using(db_alias).exclude(trigger_type="manual").update(kind="automation")


def restore_agent_workflow_kind(apps, schema_editor):
    AssistantWorkflow = apps.get_model("conversations", "AssistantWorkflow")
    db_alias = schema_editor.connection.alias
    AssistantWorkflow.objects.using(db_alias).all().update(kind="custom_assistant")


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_agentprofile_agent_type_and_more"),
        ("conversations", "0009_agentrun_waiting_child_choices"),
        ("integrations", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameModel(
            old_name="AgentWorkflow",
            new_name="AssistantWorkflow",
        ),
        migrations.RenameModel(
            old_name="AgentWorkflowDedupeKey",
            new_name="AssistantWorkflowDedupeKey",
        ),
        migrations.AlterModelTable(
            name="assistantworkflow",
            table="conversations_assistant_workflow",
        ),
        migrations.AlterModelTable(
            name="assistantworkflowdedupekey",
            table="conversations_assistant_workflow_dedupe_key",
        ),
        migrations.AddField(
            model_name="assistantworkflow",
            name="kind",
            field=models.CharField(
                choices=[
                    ("custom_assistant", "Custom assistant"),
                    ("automation", "Automation"),
                ],
                db_index=True,
                default="custom_assistant",
                max_length=32,
            ),
        ),
        migrations.RunPython(populate_assistant_workflow_kind, restore_agent_workflow_kind),
        migrations.RemoveIndex(
            model_name="assistantworkflow",
            name="workflow_biz_status_idx",
        ),
        migrations.RemoveIndex(
            model_name="assistantworkflow",
            name="workflow_agent_status_idx",
        ),
        migrations.RemoveIndex(
            model_name="assistantworkflow",
            name="workflow_due_idx",
        ),
        migrations.RemoveIndex(
            model_name="assistantworkflow",
            name="workflow_lease_idx",
        ),
        migrations.RemoveIndex(
            model_name="assistantworkflow",
            name="workflow_biz_created_idx",
        ),
        migrations.RemoveConstraint(
            model_name="assistantworkflow",
            name="workflow_unique_agent_name",
        ),
        migrations.RemoveIndex(
            model_name="assistantworkflowdedupekey",
            name="workflow_dedupe_created_idx",
        ),
        migrations.RemoveIndex(
            model_name="assistantworkflowdedupekey",
            name="wf_dedupe_biz_created_idx",
        ),
        migrations.RemoveConstraint(
            model_name="assistantworkflowdedupekey",
            name="workflow_dedupe_unique_key",
        ),
        migrations.AlterField(
            model_name="assistantworkflow",
            name="agent_profile",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="assistant_workflows",
                to="accounts.agentprofile",
            ),
        ),
        migrations.AlterField(
            model_name="assistantworkflow",
            name="business_profile",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="assistant_workflows",
                to="accounts.businessprofile",
            ),
        ),
        migrations.AlterField(
            model_name="assistantworkflow",
            name="conversation",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional destination conversation for automation run results.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assistant_workflows",
                to="conversations.conversation",
            ),
        ),
        migrations.AlterField(
            model_name="assistantworkflow",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="created_assistant_workflows",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="assistantworkflow",
            name="email_account",
            field=models.ForeignKey(
                blank=True,
                help_text="Email account used by email-inbox automations.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assistant_workflows",
                to="integrations.emailaccount",
            ),
        ),
        migrations.AlterField(
            model_name="assistantworkflow",
            name="instructions",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Assistant/automation contract: goal, success criteria, tool allowlist, constraints, output preferences.",
            ),
        ),
        migrations.AlterField(
            model_name="assistantworkflowdedupekey",
            name="business_profile",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="assistant_workflow_dedupe_keys",
                to="accounts.businessprofile",
            ),
        ),
        migrations.AlterField(
            model_name="agentrun",
            name="workflow_snapshot",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Immutable AssistantWorkflow snapshot used for this run.",
            ),
        ),
        migrations.AlterField(
            model_name="conversation",
            name="workflow",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional Custom Assistant this chat session belongs to.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sessions",
                to="conversations.assistantworkflow",
            ),
        ),
        migrations.AddIndex(
            model_name="assistantworkflow",
            index=models.Index(fields=["business_profile", "status"], name="asst_wf_biz_status_idx"),
        ),
        migrations.AddIndex(
            model_name="assistantworkflow",
            index=models.Index(fields=["business_profile", "kind", "status"], name="asst_wf_biz_kind_idx"),
        ),
        migrations.AddIndex(
            model_name="assistantworkflow",
            index=models.Index(fields=["agent_profile", "status"], name="asst_wf_agent_status_idx"),
        ),
        migrations.AddIndex(
            model_name="assistantworkflow",
            index=models.Index(fields=["status", "trigger_type", "next_trigger_at"], name="asst_wf_due_idx"),
        ),
        migrations.AddIndex(
            model_name="assistantworkflow",
            index=models.Index(fields=["status", "lease_expires_at"], name="asst_wf_lease_idx"),
        ),
        migrations.AddIndex(
            model_name="assistantworkflow",
            index=models.Index(fields=["business_profile", "created_at"], name="asst_wf_biz_created_idx"),
        ),
        migrations.AddConstraint(
            model_name="assistantworkflow",
            constraint=models.UniqueConstraint(fields=["agent_profile", "name"], name="asst_wf_unique_agent_name"),
        ),
        migrations.AddIndex(
            model_name="assistantworkflowdedupekey",
            index=models.Index(fields=["workflow", "created_at"], name="asst_wf_dedupe_created_idx"),
        ),
        migrations.AddIndex(
            model_name="assistantworkflowdedupekey",
            index=models.Index(fields=["business_profile", "created_at"], name="asst_wf_dedupe_biz_idx"),
        ),
        migrations.AddConstraint(
            model_name="assistantworkflowdedupekey",
            constraint=models.UniqueConstraint(fields=["workflow", "dedupe_key"], name="asst_wf_dedupe_unique_key"),
        ),
    ]
