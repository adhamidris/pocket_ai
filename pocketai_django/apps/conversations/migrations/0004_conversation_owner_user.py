from __future__ import annotations

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_conversation_owner_user(apps, schema_editor):
    Conversation = apps.get_model("conversations", "Conversation")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split(".", 1))

    updated_from_metadata = 0
    updated_from_business = 0
    unresolved = []

    qs = Conversation.objects.filter(owner_user__isnull=True).select_related("business_profile")
    for conversation in qs.iterator(chunk_size=200):
        owner_user_id = None
        metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
        actor_user_id = (
            metadata.get("actor_user_id")
            or metadata.get("actorUserId")
            or metadata.get("owner_user_id")
            or metadata.get("ownerUserId")
        )
        if actor_user_id:
            try:
                actor_uuid = uuid.UUID(str(actor_user_id))
            except (TypeError, ValueError, AttributeError):
                actor_uuid = None
            if actor_uuid is not None:
                owner_user_id = (
                    User.objects.filter(id=actor_uuid)
                    .values_list("id", flat=True)
                    .first()
                )
                if owner_user_id:
                    updated_from_metadata += 1

        if owner_user_id is None:
            owner_user_id = getattr(conversation.business_profile, "user_id", None)
            if owner_user_id is not None:
                updated_from_business += 1

        if owner_user_id is None:
            unresolved.append(str(conversation.id))
            continue

        Conversation.objects.filter(pk=conversation.pk).update(owner_user_id=owner_user_id)

    if unresolved:
        raise RuntimeError(
            "Unable to backfill owner_user for conversations: " + ", ".join(unresolved[:20])
        )

    if getattr(schema_editor, "connection", None) is not None:
        print(
            "[conversations.0004] backfilled owner_user "
            f"(from_metadata={updated_from_metadata}, from_business={updated_from_business})"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0003_remove_identifier_event"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="owner_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="owned_conversations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(backfill_conversation_owner_user, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="conversation",
            name="owner_user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="owned_conversations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddIndex(
            model_name="conversation",
            index=models.Index(
                fields=["owner_user", "business_profile", "last_activity_at"],
                name="conv_owner_biz_activity_idx",
            ),
        ),
    ]
