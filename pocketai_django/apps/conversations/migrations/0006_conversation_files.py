from __future__ import annotations

import uuid

from django.conf import settings
from django.db import migrations, models
from pgvector.django import VectorField


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0047_oauthprovider_oauthstate"),
        ("conversations", "0005_conversationmessage_content_blocks"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConversationFile",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                (
                    "kind",
                    models.CharField(
                        max_length=24,
                        choices=[
                            ("upload", "Upload"),
                            ("artifact", "Artifact"),
                        ],
                        default="upload",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        max_length=24,
                        choices=[
                            ("processing", "Processing"),
                            ("ready", "Ready"),
                            ("failed", "Failed"),
                        ],
                        default="processing",
                    ),
                ),
                (
                    "sender",
                    models.CharField(
                        max_length=16,
                        choices=[
                            ("customer", "Customer"),
                            ("ai", "AI"),
                            ("system", "System"),
                        ],
                        default="customer",
                    ),
                ),
                ("filename", models.CharField(max_length=255)),
                ("content_type", models.CharField(max_length=120, blank=True, default="")),
                ("storage_path", models.CharField(max_length=512)),
                ("size_bytes", models.BigIntegerField(default=0)),
                ("checksum_sha256", models.CharField(max_length=128, blank=True, default="")),
                ("page_count", models.PositiveIntegerField(default=0)),
                ("metadata", models.JSONField(default=dict, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "business_profile",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="conversation_files",
                        to="accounts.businessprofile",
                    ),
                ),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="files",
                        to="conversations.conversation",
                    ),
                ),
            ],
            options={
                "db_table": "conversations_conversation_file",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="ConversationFileChunk",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("chunk_index", models.PositiveIntegerField()),
                ("content", models.TextField()),
                ("token_count", models.PositiveIntegerField(default=0)),
                ("embedding", VectorField(dimensions=settings.EMBED_DIM, null=True, blank=True)),
                ("metadata", models.JSONField(default=dict, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "business_profile",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="conversation_file_chunks",
                        to="accounts.businessprofile",
                    ),
                ),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="file_chunks",
                        to="conversations.conversation",
                    ),
                ),
                (
                    "conversation_file",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="chunks",
                        to="conversations.conversationfile",
                    ),
                ),
            ],
            options={
                "db_table": "conversations_conversation_file_chunk",
                "ordering": ("conversation_file_id", "chunk_index"),
            },
        ),
        migrations.AddIndex(
            model_name="conversationfile",
            index=models.Index(fields=["business_profile", "created_at"], name="conv_file_biz_created_idx"),
        ),
        migrations.AddIndex(
            model_name="conversationfile",
            index=models.Index(fields=["conversation", "created_at"], name="conv_file_conv_created_idx"),
        ),
        migrations.AddIndex(
            model_name="conversationfile",
            index=models.Index(fields=["conversation", "kind"], name="conv_file_conv_kind_idx"),
        ),
        migrations.AddIndex(
            model_name="conversationfilechunk",
            index=models.Index(fields=["conversation_file", "chunk_index"], name="conv_file_chunk_window_idx"),
        ),
        migrations.AddIndex(
            model_name="conversationfilechunk",
            index=models.Index(fields=["conversation", "chunk_index"], name="conv_file_chunk_conv_idx"),
        ),
        migrations.AddIndex(
            model_name="conversationfilechunk",
            index=models.Index(fields=["business_profile", "conversation"], name="conv_file_chunk_biz_conv_idx"),
        ),
        migrations.AddConstraint(
            model_name="conversationfilechunk",
            constraint=models.UniqueConstraint(
                fields=("conversation_file", "chunk_index"),
                name="conv_file_chunk_unique_index",
            ),
        ),
    ]

