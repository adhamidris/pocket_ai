from django.db import migrations


DIRECT_TABLES = [
    ("accounts_knowledge_upload_chunk", "business_profile_id"),
    ("accounts_knowledge_upload_shadow_chunk", "business_profile_id"),
    ("accounts_knowledge_alias", "business_profile_id"),
    ("accounts_knowledge_entity", "business_profile_id"),
]

UPLOAD_JOIN_TABLES = [
    ("accounts_knowledge_upload_table", "upload_id"),
    ("accounts_knowledge_upload_page", "upload_id"),
    ("accounts_knowledge_upload_page_block", "upload_id"),
    ("accounts_knowledge_upload_issue", "upload_id"),
]

TABLE_JOIN_TABLES = [
    "accounts_knowledge_upload_table_row",
    "accounts_knowledge_upload_table_cell",
]


def _apply_policy(schema_editor, table: str, predicate: str) -> None:
    schema_editor.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    schema_editor.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    schema_editor.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    schema_editor.execute(
        f"CREATE POLICY tenant_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate})"
    )


def _drop_policy(schema_editor, table: str) -> None:
    schema_editor.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    schema_editor.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    schema_editor.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def enable_tenant_rls(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    tenant_bypass = "current_setting('app.tenant_bypass', true) = '1'"
    tenant_match = "current_setting('app.current_tenant', true)::uuid"

    for table, column in DIRECT_TABLES:
        predicate = f"({tenant_bypass} OR {column} = {tenant_match})"
        _apply_policy(schema_editor, table, predicate)

    for table, column in UPLOAD_JOIN_TABLES:
        predicate = (
            f"({tenant_bypass} OR EXISTS ("
            f"SELECT 1 FROM accounts_knowledge_upload u "
            f"WHERE u.id = {column} AND u.business_profile_id = {tenant_match}"
            f"))"
        )
        _apply_policy(schema_editor, table, predicate)

    for table in TABLE_JOIN_TABLES:
        predicate = (
            f"({tenant_bypass} OR EXISTS ("
            f"SELECT 1 FROM accounts_knowledge_upload_table t "
            f"JOIN accounts_knowledge_upload u ON u.id = t.upload_id "
            f"WHERE t.id = table_id AND u.business_profile_id = {tenant_match}"
            f"))"
        )
        _apply_policy(schema_editor, table, predicate)


def disable_tenant_rls(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    for table, _ in DIRECT_TABLES:
        _drop_policy(schema_editor, table)
    for table, _ in UPLOAD_JOIN_TABLES:
        _drop_policy(schema_editor, table)
    for table in TABLE_JOIN_TABLES:
        _drop_policy(schema_editor, table)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("accounts", "0032_knowledge_upload_shadow_chunk"),
    ]

    operations = [
        migrations.RunPython(enable_tenant_rls, disable_tenant_rls),
    ]
