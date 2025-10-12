"""add full chat infrastructure tables

Revision ID: c9b78fb0b6b7
Revises: a41674fb925b
Create Date: 2025-10-15 00:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.inspection import inspect

# revision identifiers, used by Alembic.
revision = "c9b78fb0b6b7"
down_revision = "a41674fb925b"
branch_labels = None
depends_on = None


CHAT_VISITOR_TYPE = sa.Enum(
    "anonymous",
    "known",
    name="chat_visitor_type_enum",
)

CHAT_CHANNEL = sa.Enum(
    "web_widget",
    "whatsapp",
    "messenger",
    "api",
    "email",
    "other",
    name="chat_channel_enum",
)

CHAT_PRESENCE_STATUS = sa.Enum(
    "live",
    "idle",
    "offline",
    name="chat_presence_status_enum",
)

CONVERSATION_PARTICIPANT_TYPE = sa.Enum(
    "agent",
    "teammate",
    "customer",
    "system",
    name="conversation_participant_type_enum",
)

CONVERSATION_MESSAGE_TYPE = sa.Enum(
    "customer",
    "agent",
    "system",
    "tool",
    "internal",
    name="conversation_message_type_enum",
)

CONVERSATION_MESSAGE_VISIBILITY = sa.Enum(
    "public",
    "internal",
    name="conversation_message_visibility_enum",
)

CONVERSATION_MESSAGE_CHANNEL = sa.Enum(
    "text",
    "file",
    "action",
    name="conversation_message_channel_enum",
)




def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    # Create enums if they do not yet exist
    for enum in (
        CHAT_VISITOR_TYPE,
        CHAT_CHANNEL,
        CHAT_PRESENCE_STATUS,
        CONVERSATION_PARTICIPANT_TYPE,
        CONVERSATION_MESSAGE_TYPE,
        CONVERSATION_MESSAGE_VISIBILITY,
        CONVERSATION_MESSAGE_CHANNEL,
    ):
        enum.create(bind, checkfirst=True)

    # Storage assets support (needed for message attachments)
    if not inspector.has_table("storage_assets"):
        op.create_table(
            "storage_assets",
            sa.Column(
                "business_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("businesses.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("content_type", sa.String(length=120), nullable=True),
            sa.Column("storage_path", sa.String(length=512), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("checksum_sha256", sa.String(length=128), nullable=True),
            sa.Column("metadata_json", postgresql.JSONB, nullable=True),
            sa.Column(
                "created_by_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_by_agent_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("agents.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.CheckConstraint(
                "size_bytes > 0", name="ck_storage_assets_size_positive"
            ),
            sa.CheckConstraint(
                "checksum_sha256 IS NULL OR char_length(checksum_sha256) = 64",
                name="ck_storage_assets_checksum_hex_length",
            ),
        )
        op.create_index(
            "ix_storage_assets_business",
            "storage_assets",
            ["business_id"],
            unique=False,
        )
        op.create_index(
            "uq_storage_assets_path",
            "storage_assets",
            ["storage_path"],
            unique=True,
        )

    # chat_visitors
    op.create_table(
        "chat_visitors",
        sa.Column(
            "business_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "visitor_type",
            sa.Enum(name="chat_visitor_type_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("session_token", sa.String(length=120), nullable=False),
        sa.Column(
            "channel",
            sa.Enum(name="chat_channel_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("locale", sa.String(length=16), nullable=True),
        sa.Column("landing_page", sa.String(length=255), nullable=True),
        sa.Column("utm_json", postgresql.JSONB, nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "current_session_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "current_session_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("welcome_template_key", sa.String(length=64), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "business_id",
            "session_token",
            name="uq_chat_visitors_business_session",
        ),
    )
    op.create_index(
        "ix_chat_visitors_business",
        "chat_visitors",
        ["business_id"],
        unique=False,
    )

    # device fingerprints
    op.create_table(
        "chat_device_fingerprints",
        sa.Column(
            "visitor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_visitors.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "fingerprint_hash",
            sa.String(length=120),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # presence pings
    op.create_table(
        "chat_presence_pings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "visitor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_visitors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pinged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(name="chat_presence_status_enum", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_chat_presence_pings_visitor",
        "chat_presence_pings",
        ["visitor_id", sa.text("pinged_at DESC")],
        unique=False,
    )

    # conversation participants
    op.create_table(
        "conversation_participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "participant_type",
            sa.Enum(name="conversation_participant_type_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("participant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_conversation_participants_conversation",
        "conversation_participants",
        ["conversation_id", "participant_type"],
        unique=False,
    )

    # conversation messages
    op.create_table(
        "conversation_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "author_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "author_customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "message_type",
            sa.Enum(name="conversation_message_type_enum", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "visibility",
            sa.Enum(name="conversation_message_visibility_enum", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "channel",
            sa.Enum(name="conversation_message_channel_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("payload_json", postgresql.JSONB, nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "body IS NOT NULL OR payload_json IS NOT NULL",
            name="ck_conversation_messages_body_or_payload",
        ),
    )
    op.create_index(
        "ix_conversation_messages_conversation",
        "conversation_messages",
        ["conversation_id", "sent_at"],
        unique=False,
    )

    # attachments
    op.create_table(
        "message_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversation_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "storage_asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("storage_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("caption", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # turn snapshots
    op.create_table(
        "conversation_turn_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversation_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_content", sa.Text(), nullable=True),
        sa.Column("completion_content", sa.Text(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # conversation summary
    op.create_table(
        "conversation_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ai_overview", sa.Text(), nullable=True),
        sa.Column("key_points_json", postgresql.JSONB, nullable=True),
        sa.Column("actions_taken_json", postgresql.JSONB, nullable=True),
        sa.Column("suggested_actions_json", postgresql.JSONB, nullable=True),
        sa.Column("last_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "conversation_id",
            name="uq_conversation_summaries_conversation",
        ),
    )

    # status logs
    op.create_table(
        "conversation_status_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_status",
            sa.Enum(name="conversation_status_enum", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "to_status",
            sa.Enum(name="conversation_status_enum", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "actor_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_conversation_status_logs_conversation",
        "conversation_status_logs",
        ["conversation_id", sa.text("created_at DESC")],
        unique=False,
    )

    # daily metrics
    op.create_table(
        "conversation_metrics_daily",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "business_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("live_conversations", sa.Integer(), nullable=False),
        sa.Column("new_conversations", sa.Integer(), nullable=False),
        sa.Column("escalations", sa.Integer(), nullable=False),
        sa.Column("avg_first_response_seconds", sa.Integer(), nullable=True),
        sa.Column("avg_resolution_seconds", sa.Integer(), nullable=True),
        sa.Column("csat_average", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "business_id",
            "metric_date",
            name="uq_conversation_metrics_daily_date",
        ),
        sa.CheckConstraint(
            "live_conversations >= 0 AND new_conversations >= 0 AND escalations >= 0",
            name="ck_conversation_metrics_daily_positive_counts",
        ),
    )

    # Conversation table updates
    op.create_index(
        "ix_conversations_business_updated",
        "conversations",
        ["business_id", sa.text("updated_at DESC")],
        unique=False,
    )
    op.create_check_constraint(
        "ck_conversations_resolution_time",
        "conversations",
        "resolution_time_seconds IS NULL OR resolution_time_seconds >= 0",
    )
    op.create_foreign_key(
        "fk_conversations_visitor_id_chat_visitors",
        "conversations",
        "chat_visitors",
        ["visitor_id"],
        ["id"],
        ondelete="SET NULL",
    )

    if inspector.has_table("cases"):
        conversation_fk_names = {
            fk["name"]
            for fk in inspector.get_foreign_keys("conversations")
        }
        if "fk_conversations_case_id_cases" not in conversation_fk_names:
            op.create_foreign_key(
                "fk_conversations_case_id_cases",
                "conversations",
                "cases",
                ["case_id"],
                ["id"],
                ondelete="SET NULL",
            )

        case_fk_names = {
            fk["name"] for fk in inspector.get_foreign_keys("cases")
        }
        if "fk_cases_origin_conversation_id_conversations" not in case_fk_names:
            op.create_foreign_key(
                "fk_cases_origin_conversation_id_conversations",
                "cases",
                "conversations",
                ["origin_conversation_id"],
                ["id"],
                ondelete="SET NULL",
            )



def downgrade() -> None:
    inspector = inspect(op.get_bind())

    if inspector.has_table("cases"):
        case_fk_names = {
            fk["name"] for fk in inspector.get_foreign_keys("cases")
        }
        if "fk_cases_origin_conversation_id_conversations" in case_fk_names:
            op.drop_constraint(
                "fk_cases_origin_conversation_id_conversations",
                "cases",
                type_="foreignkey",
            )

    conv_fk_names = {
        fk["name"] for fk in inspector.get_foreign_keys("conversations")
    }
    if "fk_conversations_case_id_cases" in conv_fk_names:
        op.drop_constraint(
            "fk_conversations_case_id_cases",
            "conversations",
            type_="foreignkey",
        )
    if "fk_conversations_visitor_id_chat_visitors" in conv_fk_names:
        op.drop_constraint(
            "fk_conversations_visitor_id_chat_visitors",
            "conversations",
            type_="foreignkey",
        )

    conv_check_names = {
        ck["name"] for ck in inspector.get_check_constraints("conversations")
    }
    if "ck_conversations_resolution_time" in conv_check_names:
        op.drop_constraint(
            "ck_conversations_resolution_time",
            "conversations",
            type_="check",
        )

    op.drop_index(
        "ix_conversations_business_updated",
        table_name="conversations",
    )

    op.drop_table("conversation_metrics_daily")
    op.drop_index(
        "ix_conversation_status_logs_conversation",
        table_name="conversation_status_logs",
    )
    op.drop_table("conversation_status_logs")
    op.drop_table("conversation_summaries")
    op.drop_table("conversation_turn_snapshots")
    op.drop_table("message_attachments")
    op.drop_index(
        "ix_conversation_messages_conversation",
        table_name="conversation_messages",
    )
    op.drop_table("conversation_messages")
    op.drop_index(
        "ix_conversation_participants_conversation",
        table_name="conversation_participants",
    )
    op.drop_table("conversation_participants")
    op.drop_index(
        "ix_chat_presence_pings_visitor",
        table_name="chat_presence_pings",
    )
    op.drop_table("chat_presence_pings")
    op.drop_table("chat_device_fingerprints")
    op.drop_index(
        "ix_chat_visitors_business",
        table_name="chat_visitors",
    )
    op.drop_table("chat_visitors")

    if inspector.has_table("storage_assets"):
        op.drop_index("uq_storage_assets_path", table_name="storage_assets")
        op.drop_index("ix_storage_assets_business", table_name="storage_assets")
        op.drop_table("storage_assets")

    for enum in reversed(
        (
            CONVERSATION_MESSAGE_CHANNEL,
            CONVERSATION_MESSAGE_VISIBILITY,
            CONVERSATION_MESSAGE_TYPE,
            CONVERSATION_PARTICIPANT_TYPE,
            CHAT_PRESENCE_STATUS,
            CHAT_CHANNEL,
            CHAT_VISITOR_TYPE,
        )
    ):
        enum.drop(bind, checkfirst=True)
