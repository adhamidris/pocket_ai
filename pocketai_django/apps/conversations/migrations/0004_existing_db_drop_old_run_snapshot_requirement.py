# Generated for pre-production AgentRun legacy column compatibility.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0003_existing_db_workflow_memory_shape"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'conversations_agent_run'
                      AND column_name = 'run_spec_snapshot'
                ) THEN
                    ALTER TABLE conversations_agent_run
                    ALTER COLUMN run_spec_snapshot SET DEFAULT '{}'::jsonb;

                    ALTER TABLE conversations_agent_run
                    ALTER COLUMN run_spec_snapshot DROP NOT NULL;
                END IF;
            END $$;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
