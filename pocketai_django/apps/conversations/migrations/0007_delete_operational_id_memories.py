from django.db import migrations


OPERATIONAL_ID_FIELDS = {
    "id",
    "draft_id",
    "draftid",
    "message_id",
    "messageid",
    "thread_id",
    "threadid",
    "event_id",
    "eventid",
}


def delete_operational_id_memories(apps, schema_editor):
    MemoryItem = apps.get_model("conversations", "MemoryItem")
    delete_ids = []
    queryset = MemoryItem.objects.filter(payload__extraction_method="rule_based").only("id", "key", "payload")
    for item in queryset.iterator(chunk_size=500):
        payload = item.payload if isinstance(item.payload, dict) else {}
        original_key = str(payload.get("original_key") or "").strip().replace("-", "_").lower()
        original_compact = original_key.replace("_", "")
        key = str(item.key or "").strip().replace("-", "_").lower()
        if original_key in OPERATIONAL_ID_FIELDS or original_compact in OPERATIONAL_ID_FIELDS:
            delete_ids.append(item.id)
            continue
        if any(key.endswith("_" + field) for field in OPERATIONAL_ID_FIELDS):
            delete_ids.append(item.id)
    if delete_ids:
        MemoryItem.objects.filter(id__in=delete_ids).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0006_remove_archived_agent_workflows"),
    ]

    operations = [
        migrations.RunPython(delete_operational_id_memories, migrations.RunPython.noop),
    ]
