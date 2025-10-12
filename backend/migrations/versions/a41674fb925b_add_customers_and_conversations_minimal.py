"""add customers and conversations (minimal)

Revision ID: a41674fb925b
Revises: 7eabf356546b
Create Date: 2025-10-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'a41674fb925b'
down_revision = '7eabf356546b'
branch_labels = None
depends_on = None

def upgrade() -> None:
    bind = op.get_bind()

    # Enums (create if missing)
    sa.Enum('lead','prospect','active','churn_risk','former',
            name='customer_lifecycle_stage_enum').create(bind, checkfirst=True)
    sa.Enum('web','mobile','api','integration',
            name='conversation_source_enum').create(bind, checkfirst=True)
    sa.Enum('new','live','resolved','escalated','closed_without_resolution','expired',
            name='conversation_status_enum').create(bind, checkfirst=True)
    sa.Enum('resolved','escalated','customer_left','agent_ended','timeout','other',
            name='conversation_end_reason_enum').create(bind, checkfirst=True)

    # customers
    op.create_table(
        'customers',
        sa.Column('business_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('external_id', sa.String(length=120), nullable=True),
        sa.Column('full_name', sa.String(length=160), nullable=False),
        sa.Column('primary_email', sa.String(length=255), nullable=True),
        sa.Column('primary_phone', sa.String(length=64), nullable=True),
        sa.Column('country', sa.String(length=2), nullable=True),
        sa.Column('lifecycle_stage', postgresql.ENUM(name='customer_lifecycle_stage_enum', create_type=False),
                  nullable=False, server_default=sa.text("'lead'::customer_lifecycle_stage_enum")),
        sa.Column('satisfaction_score', sa.Numeric(5, 2), nullable=True),
        sa.Column('persona_tags', postgresql.ARRAY(sa.String(length=64)),
                  nullable=False, server_default=sa.text("ARRAY[]::text[]")),
        sa.Column('last_contact_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("primary_email IS NULL OR position('@' in primary_email) > 1", name='ck_customers_primary_email_format'),
        sa.CheckConstraint("country IS NULL OR char_length(country) = 2", name='ck_customers_country_code'),
        sa.CheckConstraint("satisfaction_score IS NULL OR (satisfaction_score >= 0 AND satisfaction_score <= 100)", name='ck_customers_satisfaction_score_range'),
        sa.UniqueConstraint('business_id', 'external_id', name='uq_customers_business_external_id'),
    )
    op.create_index('ix_customers_business_stage', 'customers', ['business_id', 'lifecycle_stage'], unique=False)

    # conversations (minimal)
    op.create_table(
        'conversations',
        sa.Column('business_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('visitor_id', postgresql.UUID(as_uuid=True), nullable=True),  # no FK to chat_visitors yet
        sa.Column('customer_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('customers.id', ondelete='SET NULL'), nullable=True),
        sa.Column('case_id', postgresql.UUID(as_uuid=True), nullable=True),     # no FK to cases yet
        sa.Column('primary_agent_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('agents.id', ondelete='SET NULL'), nullable=True),
        sa.Column('source', postgresql.ENUM(name='conversation_source_enum', create_type=False), nullable=False),
        sa.Column('status', postgresql.ENUM(name='conversation_status_enum', create_type=False), nullable=False),
        sa.Column('end_reason', postgresql.ENUM(name='conversation_end_reason_enum', create_type=False), nullable=True),
        sa.Column('first_response_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('first_response_latency_seconds', sa.Integer(), nullable=True),
        sa.Column('resolution_time_seconds', sa.Integer(), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('csat_score', sa.Float(), nullable=True),
        sa.Column('csat_comment', sa.Text(), nullable=True),
        sa.Column('satisfaction_recorded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('runtime_profile_version', sa.Integer(), nullable=True),
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("csat_score IS NULL OR (csat_score >= 0 AND csat_score <= 100)", name='ck_conversations_csat_range'),
        sa.CheckConstraint("first_response_latency_seconds IS NULL OR first_response_latency_seconds >= 0", name='ck_conversations_first_response_latency'),
    )
    op.create_index('ix_conversations_business_status', 'conversations', ['business_id', 'status'], unique=False)
    op.create_index('ix_conversations_customer', 'conversations', ['customer_id'], unique=False)
    op.create_index('ix_conversations_case', 'conversations', ['case_id'], unique=False)

def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index('ix_conversations_case', table_name='conversations')
    op.drop_index('ix_conversations_customer', table_name='conversations')
    op.drop_index('ix_conversations_business_status', table_name='conversations')
    op.drop_table('conversations')

    op.drop_index('ix_customers_business_stage', table_name='customers')
    op.drop_table('customers')

    for enum_name in [
        'conversation_end_reason_enum',
        'conversation_status_enum',
        'conversation_source_enum',
        'customer_lifecycle_stage_enum',
    ]:
        sa.Enum(name=enum_name).drop(bind, checkfirst=True)
