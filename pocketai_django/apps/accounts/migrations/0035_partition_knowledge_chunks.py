from __future__ import annotations

from django.conf import settings
from django.db import migrations, models


PARTITION_COUNT = 64
VECTOR_DIM = getattr(settings, "EMBED_DIM", 384)


def _recreate_partitioned_chunks(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return

    def exec_sql(statement: str) -> None:
        schema_editor.execute(statement)

    def rename_table(old: str, new: str, tables: set[str]) -> None:
        if old in tables and new not in tables:
            exec_sql(f"ALTER TABLE {old} RENAME TO {new}")

    def create_partitioned_table(table: str) -> None:
        exec_sql(
            f"""
CREATE TABLE IF NOT EXISTS {table} (
    id uuid NOT NULL,
    upload_id uuid NOT NULL REFERENCES accounts_knowledge_upload(id) ON DELETE CASCADE,
    business_profile_id uuid NOT NULL REFERENCES accounts_business_profile(id) ON DELETE CASCADE,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    token_count integer NOT NULL DEFAULT 0,
    embedding vector({VECTOR_DIM}),
    metadata jsonb NOT NULL DEFAULT '{{}}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
) PARTITION BY HASH (business_profile_id)
"""
        )

    def create_partitions(table: str, suffix: str) -> None:
        for idx in range(PARTITION_COUNT):
            exec_sql(
                f"""
CREATE TABLE IF NOT EXISTS {table}_{suffix}{idx}
PARTITION OF {table}
FOR VALUES WITH (MODULUS {PARTITION_COUNT}, REMAINDER {idx})
"""
            )

    def copy_data(source: str, target: str) -> None:
        exec_sql(
            f"""
INSERT INTO {target} (id, upload_id, business_profile_id, chunk_index, content, token_count, embedding, metadata, created_at, updated_at)
SELECT id, upload_id, business_profile_id, chunk_index, content, token_count, embedding, metadata, created_at, updated_at
FROM {source}
"""
        )

    def add_unique_constraint(table: str, name: str) -> None:
        exec_sql(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
        exec_sql(
            f"""
ALTER TABLE {table}
ADD CONSTRAINT {name}
UNIQUE (business_profile_id, upload_id, chunk_index)
"""
        )

    def create_indexes(table: str, prefix: str, suffix: str) -> None:
        exec_sql(
            f"""
DO $$
DECLARE
    idx integer;
BEGIN
    FOR idx IN 0..{PARTITION_COUNT - 1} LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS {table}_{suffix}%%s PARTITION OF {table} FOR VALUES WITH (MODULUS {PARTITION_COUNT}, REMAINDER %%s)',
            idx, idx
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS {prefix}_emb_hnsw_%%s ON {table}_{suffix}%%s USING hnsw (embedding vector_cosine_ops)',
            idx, idx
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS {prefix}_content_trgm_%%s ON {table}_{suffix}%%s USING gin (content gin_trgm_ops)',
            idx, idx
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS {prefix}_content_fts_%%s ON {table}_{suffix}%%s USING gin (to_tsvector(''simple'', coalesce(content, '''')))',
            idx, idx
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS {prefix}_alias_trgm_%%s ON {table}_{suffix}%%s USING gin ((metadata ->> ''alias_string'') gin_trgm_ops) WHERE metadata ? ''alias_string''',
            idx, idx
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS {prefix}_biz_upload_%%s ON {table}_{suffix}%%s (business_profile_id, upload_id)',
            idx, idx
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS {prefix}_biz_chunk_%%s ON {table}_{suffix}%%s (business_profile_id, chunk_index)',
            idx, idx
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS {prefix}_window_%%s ON {table}_{suffix}%%s (upload_id, chunk_index)',
            idx, idx
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS {prefix}_id_%%s ON {table}_{suffix}%%s (id)',
            idx, idx
        );
    END LOOP;
END $$;
"""
        )

    def apply_rls(table: str, column: str) -> None:
        exec_sql(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        exec_sql(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        exec_sql(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        exec_sql(
            f"""
CREATE POLICY tenant_isolation ON {table}
USING (current_setting('app.tenant_bypass', true) = '1' OR {column} = current_setting('app.current_tenant', true)::uuid)
WITH CHECK (current_setting('app.tenant_bypass', true) = '1' OR {column} = current_setting('app.current_tenant', true)::uuid)
"""
        )

    def drop_table(table: str) -> None:
        exec_sql(f"DROP TABLE IF EXISTS {table} CASCADE")

    tables = set(schema_editor.connection.introspection.table_names())
    rename_table("accounts_knowledge_upload_chunk", "accounts_knowledge_upload_chunk_legacy", tables)
    rename_table("accounts_knowledge_upload_shadow_chunk", "accounts_knowledge_upload_shadow_chunk_legacy", tables)

    create_partitioned_table("accounts_knowledge_upload_chunk")
    create_partitioned_table("accounts_knowledge_upload_shadow_chunk")

    create_partitions("accounts_knowledge_upload_chunk", "p")
    create_partitions("accounts_knowledge_upload_shadow_chunk", "p")

    tables = set(schema_editor.connection.introspection.table_names())
    if "accounts_knowledge_upload_chunk_legacy" in tables:
        copy_data("accounts_knowledge_upload_chunk_legacy", "accounts_knowledge_upload_chunk")
    if "accounts_knowledge_upload_shadow_chunk_legacy" in tables:
        copy_data("accounts_knowledge_upload_shadow_chunk_legacy", "accounts_knowledge_upload_shadow_chunk")

    drop_table("accounts_knowledge_upload_chunk_legacy")
    drop_table("accounts_knowledge_upload_shadow_chunk_legacy")

    add_unique_constraint("accounts_knowledge_upload_chunk", "knowledge_chunk_unique_index")
    add_unique_constraint("accounts_knowledge_upload_shadow_chunk", "knowledge_shadow_chunk_unique_index")

    create_indexes("accounts_knowledge_upload_chunk", "kn_chunk", "p")
    create_indexes("accounts_knowledge_upload_shadow_chunk", "kn_shadow", "p")

    apply_rls("accounts_knowledge_upload_chunk", "business_profile_id")
    apply_rls("accounts_knowledge_upload_shadow_chunk", "business_profile_id")


def _restore_chunk_tables(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("DROP TABLE IF EXISTS accounts_knowledge_upload_chunk CASCADE")
    schema_editor.execute("DROP TABLE IF EXISTS accounts_knowledge_upload_shadow_chunk CASCADE")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("accounts", "0034_knowledge_entity_chunk_id"),
    ]

    operations = [
        migrations.RunPython(_recreate_partitioned_chunks, _restore_chunk_tables),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="knowledgeuploadchunk",
                    name="knowledge_chunk_unique_index",
                ),
                migrations.AddConstraint(
                    model_name="knowledgeuploadchunk",
                    constraint=models.UniqueConstraint(
                        fields=["business_profile", "upload", "chunk_index"],
                        name="knowledge_chunk_unique_index",
                    ),
                ),
                migrations.RemoveConstraint(
                    model_name="knowledgeuploadshadowchunk",
                    name="knowledge_shadow_chunk_unique_index",
                ),
                migrations.AddConstraint(
                    model_name="knowledgeuploadshadowchunk",
                    constraint=models.UniqueConstraint(
                        fields=["business_profile", "upload", "chunk_index"],
                        name="knowledge_shadow_chunk_unique_index",
                    ),
                ),
            ],
        ),
    ]
