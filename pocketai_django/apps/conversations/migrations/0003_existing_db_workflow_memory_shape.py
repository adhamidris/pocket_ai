# Generated for pre-production AgentWorkflow/MemoryItem rebaseline compatibility.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0002_conversationtoolapproval_connection"),
        ("accounts", "0003_agentprofile_existing_db_workforce_shape"),
        ("integrations", "0001_initial"),
        ("crm", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE TABLE IF NOT EXISTS conversations_agent_workflow (
                id uuid NOT NULL PRIMARY KEY,
                name varchar(160) NOT NULL,
                description text NOT NULL DEFAULT '',
                status varchar(24) NOT NULL DEFAULT 'draft',
                visibility varchar(24) NOT NULL DEFAULT 'initiator',
                trigger_type varchar(24) NOT NULL DEFAULT 'manual',
                trigger_config jsonb NOT NULL DEFAULT '{}'::jsonb,
                source_config jsonb NOT NULL DEFAULT '{}'::jsonb,
                destination_config jsonb NOT NULL DEFAULT '{}'::jsonb,
                instructions jsonb NOT NULL DEFAULT '{}'::jsonb,
                state jsonb NOT NULL DEFAULT '{}'::jsonb,
                poll_interval_seconds integer NOT NULL DEFAULT 300,
                max_events_per_poll smallint NOT NULL DEFAULT 5,
                last_triggered_at timestamp with time zone NULL,
                last_polled_at timestamp with time zone NULL,
                next_trigger_at timestamp with time zone NULL,
                lease_expires_at timestamp with time zone NULL,
                error_count smallint NOT NULL DEFAULT 0,
                last_error text NOT NULL DEFAULT '',
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamp with time zone NOT NULL,
                updated_at timestamp with time zone NOT NULL,
                agent_profile_id uuid NOT NULL,
                business_profile_id uuid NOT NULL,
                created_by_id uuid NULL,
                email_account_id uuid NULL,
                conversation_id uuid NULL
            );

            CREATE TABLE IF NOT EXISTS conversations_agent_workflow_dedupe_key (
                id uuid NOT NULL PRIMARY KEY,
                dedupe_key varchar(255) NOT NULL,
                created_at timestamp with time zone NOT NULL,
                business_profile_id uuid NOT NULL,
                workflow_id uuid NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversations_memory_item (
                id uuid NOT NULL PRIMARY KEY,
                scope varchar(32) NOT NULL,
                kind varchar(32) NOT NULL,
                key varchar(160) NOT NULL DEFAULT '',
                content text NOT NULL DEFAULT '',
                payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                visibility varchar(16) NOT NULL DEFAULT 'shared',
                sensitivity varchar(16) NOT NULL DEFAULT 'normal',
                status varchar(24) NOT NULL DEFAULT 'active',
                source_type varchar(64) NOT NULL DEFAULT '',
                source_id uuid NULL,
                confidence double precision NOT NULL DEFAULT 1.0,
                reviewed_at timestamp with time zone NULL,
                expires_at timestamp with time zone NULL,
                created_at timestamp with time zone NOT NULL,
                updated_at timestamp with time zone NOT NULL,
                agent_profile_id uuid NULL,
                business_profile_id uuid NOT NULL,
                conversation_id uuid NULL,
                created_by_id uuid NULL,
                crm_company_id uuid NULL,
                crm_contact_id uuid NULL,
                reviewed_by_id uuid NULL,
                run_id uuid NULL,
                workflow_id uuid NULL
            );

            CREATE TABLE IF NOT EXISTS conversations_memory_audit_event (
                id uuid NOT NULL PRIMARY KEY,
                action varchar(32) NOT NULL,
                description text NOT NULL DEFAULT '',
                before jsonb NOT NULL DEFAULT '{}'::jsonb,
                after jsonb NOT NULL DEFAULT '{}'::jsonb,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                occurred_at timestamp with time zone NOT NULL,
                created_at timestamp with time zone NOT NULL,
                actor_user_id uuid NULL,
                business_profile_id uuid NOT NULL,
                memory_item_id uuid NOT NULL
            );

            ALTER TABLE conversations_agent_run
            ADD COLUMN IF NOT EXISTS workflow_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb;
            ALTER TABLE conversations_agent_run
            ADD COLUMN IF NOT EXISTS workflow_id uuid NULL;
            ALTER TABLE conversations_agent_run
            ADD COLUMN IF NOT EXISTS parent_run_id uuid NULL;
            ALTER TABLE conversations_agent_run
            ADD COLUMN IF NOT EXISTS delegated_by_agent_id uuid NULL;
            ALTER TABLE conversations_agent_run
            ADD COLUMN IF NOT EXISTS execution_conversation_id uuid NULL;

            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'conversations_agent_run'
                      AND column_name = 'run_spec_snapshot'
                ) THEN
                    UPDATE conversations_agent_run
                    SET workflow_snapshot = COALESCE(run_spec_snapshot, '{}'::jsonb)
                    WHERE workflow_snapshot = '{}'::jsonb;
                END IF;
            END $$;

            ALTER TABLE conversations_agent_request
            ADD COLUMN IF NOT EXISTS agent_run_id uuid NULL;
            ALTER TABLE conversations_agent_request
            ADD COLUMN IF NOT EXISTS conversation_id uuid NULL;

            CREATE UNIQUE INDEX IF NOT EXISTS workflow_unique_agent_name
            ON conversations_agent_workflow (agent_profile_id, name);
            CREATE INDEX IF NOT EXISTS workflow_biz_status_idx
            ON conversations_agent_workflow (business_profile_id, status);
            CREATE INDEX IF NOT EXISTS workflow_agent_status_idx
            ON conversations_agent_workflow (agent_profile_id, status);
            CREATE INDEX IF NOT EXISTS workflow_due_idx
            ON conversations_agent_workflow (status, trigger_type, next_trigger_at);
            CREATE INDEX IF NOT EXISTS workflow_lease_idx
            ON conversations_agent_workflow (status, lease_expires_at);
            CREATE INDEX IF NOT EXISTS workflow_biz_created_idx
            ON conversations_agent_workflow (business_profile_id, created_at);

            CREATE UNIQUE INDEX IF NOT EXISTS workflow_dedupe_unique_key
            ON conversations_agent_workflow_dedupe_key (workflow_id, dedupe_key);
            CREATE INDEX IF NOT EXISTS workflow_dedupe_created_idx
            ON conversations_agent_workflow_dedupe_key (workflow_id, created_at);
            CREATE INDEX IF NOT EXISTS wf_dedupe_biz_created_idx
            ON conversations_agent_workflow_dedupe_key (business_profile_id, created_at);

            CREATE INDEX IF NOT EXISTS run_workflow_created_idx
            ON conversations_agent_run (workflow_id, created_at);
            CREATE INDEX IF NOT EXISTS run_parent_created_idx
            ON conversations_agent_run (parent_run_id, created_at);

            CREATE INDEX IF NOT EXISTS mem_biz_status_vis_idx
            ON conversations_memory_item (business_profile_id, status, visibility);
            CREATE INDEX IF NOT EXISTS memory_biz_scope_status_idx
            ON conversations_memory_item (business_profile_id, scope, status);
            CREATE INDEX IF NOT EXISTS memory_agent_status_time_idx
            ON conversations_memory_item (agent_profile_id, status, updated_at);
            CREATE INDEX IF NOT EXISTS mem_wf_status_time_idx
            ON conversations_memory_item (workflow_id, status, updated_at);
            CREATE INDEX IF NOT EXISTS memory_run_created_idx
            ON conversations_memory_item (run_id, created_at);
            CREATE INDEX IF NOT EXISTS memory_conv_created_idx
            ON conversations_memory_item (conversation_id, created_at);
            CREATE INDEX IF NOT EXISTS memory_crm_contact_idx
            ON conversations_memory_item (crm_contact_id, status);
            CREATE INDEX IF NOT EXISTS memory_crm_company_idx
            ON conversations_memory_item (crm_company_id, status);

            CREATE INDEX IF NOT EXISTS memory_audit_item_time_idx
            ON conversations_memory_audit_event (memory_item_id, occurred_at);
            CREATE INDEX IF NOT EXISTS memory_audit_biz_time_idx
            ON conversations_memory_audit_event (business_profile_id, occurred_at);
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
