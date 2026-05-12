# Generated for pre-production AgentProfile rebaseline compatibility.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_agentprofile_allowed_documents"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE accounts_agent_profile
            ADD COLUMN IF NOT EXISTS status varchar(24) NOT NULL DEFAULT 'active';

            ALTER TABLE accounts_agent_profile
            ADD COLUMN IF NOT EXISTS responsibilities jsonb NOT NULL DEFAULT '[]'::jsonb;

            ALTER TABLE accounts_agent_profile
            ADD COLUMN IF NOT EXISTS instructions text NOT NULL DEFAULT '';

            DO $$
            DECLARE
                constraint_name text;
            BEGIN
                FOR constraint_name IN
                    SELECT c.conname
                    FROM pg_constraint c
                    JOIN pg_attribute a
                      ON a.attrelid = c.conrelid
                     AND a.attnum = ANY(c.conkey)
                    WHERE c.conrelid = 'accounts_agent_profile'::regclass
                      AND c.contype = 'u'
                      AND a.attname = 'business_profile_id'
                      AND array_length(c.conkey, 1) = 1
                LOOP
                    EXECUTE format('ALTER TABLE accounts_agent_profile DROP CONSTRAINT IF EXISTS %I', constraint_name);
                END LOOP;
            END $$;

            CREATE INDEX IF NOT EXISTS agent_profile_status_idx
            ON accounts_agent_profile (status);

            CREATE INDEX IF NOT EXISTS agent_profile_business_idx
            ON accounts_agent_profile (business_profile_id);
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
