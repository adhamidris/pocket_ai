"""fix agents enums to lowercase and add missing public fields

Revision ID: d3a9b0f1e1a2
Revises: c9b78fb0b6b7
Create Date: 2025-10-13
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.inspection import inspect

# revision identifiers, used by Alembic.
revision: str = "d3a9b0f1e1a2"
down_revision: str | None = "c9b78fb0b6b7"
branch_labels = None
depends_on = None


def _rename_enums_to_lowercase() -> None:
    op.execute(
        """
DO $$
BEGIN
  -- agent_role_enum
  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='agent_role_enum' AND e.enumlabel='SALES') THEN
    ALTER TYPE agent_role_enum RENAME VALUE 'SALES' TO 'sales';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='agent_role_enum' AND e.enumlabel='SUPPORT') THEN
    ALTER TYPE agent_role_enum RENAME VALUE 'SUPPORT' TO 'support';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='agent_role_enum' AND e.enumlabel='RESEARCH') THEN
    ALTER TYPE agent_role_enum RENAME VALUE 'RESEARCH' TO 'research';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='agent_role_enum' AND e.enumlabel='SUCCESS') THEN
    ALTER TYPE agent_role_enum RENAME VALUE 'SUCCESS' TO 'success';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='agent_role_enum' AND e.enumlabel='MARKETING') THEN
    ALTER TYPE agent_role_enum RENAME VALUE 'MARKETING' TO 'marketing';
  END IF;

  -- agent_tone_enum
  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='agent_tone_enum' AND e.enumlabel='FRIENDLY') THEN
    ALTER TYPE agent_tone_enum RENAME VALUE 'FRIENDLY' TO 'friendly';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='agent_tone_enum' AND e.enumlabel='PROFESSIONAL') THEN
    ALTER TYPE agent_tone_enum RENAME VALUE 'PROFESSIONAL' TO 'professional';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='agent_tone_enum' AND e.enumlabel='CASUAL') THEN
    ALTER TYPE agent_tone_enum RENAME VALUE 'CASUAL' TO 'casual';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='agent_tone_enum' AND e.enumlabel='FORMAL') THEN
    ALTER TYPE agent_tone_enum RENAME VALUE 'FORMAL' TO 'formal';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='agent_tone_enum' AND e.enumlabel='EMPATHETIC') THEN
    ALTER TYPE agent_tone_enum RENAME VALUE 'EMPATHETIC' TO 'empathetic';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='agent_tone_enum' AND e.enumlabel='PLAYFUL') THEN
    ALTER TYPE agent_tone_enum RENAME VALUE 'PLAYFUL' TO 'playful';
  END IF;

  -- escalation_rule_enum
  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='escalation_rule_enum' AND e.enumlabel='NEVER') THEN
    ALTER TYPE escalation_rule_enum RENAME VALUE 'NEVER' TO 'never';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='escalation_rule_enum' AND e.enumlabel='ON_FALLBACK') THEN
    ALTER TYPE escalation_rule_enum RENAME VALUE 'ON_FALLBACK' TO 'on_fallback';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='escalation_rule_enum' AND e.enumlabel='ON_NEGATIVE_SENTIMENT') THEN
    ALTER TYPE escalation_rule_enum RENAME VALUE 'ON_NEGATIVE_SENTIMENT' TO 'on_negative_sentiment';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='escalation_rule_enum' AND e.enumlabel='ON_HIGH_VALUE') THEN
    ALTER TYPE escalation_rule_enum RENAME VALUE 'ON_HIGH_VALUE' TO 'on_high_value';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='escalation_rule_enum' AND e.enumlabel='ALWAYS') THEN
    ALTER TYPE escalation_rule_enum RENAME VALUE 'ALWAYS' TO 'always';
  END IF;
END $$;
"""
    )


def _create_status_enum(bind) -> None:
    sa.Enum("draft", "active", "inactive", name="agent_status_enum").create(bind, checkfirst=True)


def _add_missing_agent_columns(bind) -> None:
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("agents")}
    uqs = {uc["name"] for uc in insp.get_unique_constraints("agents")}

    if "status" not in cols:
        op.add_column(
            "agents",
            sa.Column(
                "status",
                postgresql.ENUM(name="agent_status_enum", create_type=False),
                nullable=False,
                server_default=sa.text("'draft'::agent_status_enum"),
            ),
        )

    if "public_slug" not in cols:
        op.add_column("agents", sa.Column("public_slug", sa.String(length=120), nullable=True))

    if "default_language" not in cols:
        op.add_column("agents", sa.Column("default_language", sa.String(length=16), nullable=True))

    if "welcome_message" not in cols:
        op.add_column("agents", sa.Column("welcome_message", sa.Text(), nullable=True))

    if "avatar_url" not in cols:
        op.add_column("agents", sa.Column("avatar_url", sa.String(length=255), nullable=True))

    if "public_link_enabled" not in cols:
        op.add_column(
            "agents",
            sa.Column("public_link_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )

    if "uq_agents_business_public_slug" not in uqs:
        op.create_unique_constraint("uq_agents_business_public_slug", "agents", ["business_id", "public_slug"])


def upgrade() -> None:
    bind = op.get_bind()
    _rename_enums_to_lowercase()
    _create_status_enum(bind)
    _add_missing_agent_columns(bind)


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("agents")}
    uqs = {uc["name"] for uc in insp.get_unique_constraints("agents")}

    if "uq_agents_business_public_slug" in uqs:
        op.drop_constraint("uq_agents_business_public_slug", "agents", type_="unique")

    for col in ("public_link_enabled", "avatar_url", "welcome_message", "default_language", "public_slug"):
        if col in cols:
            op.drop_column("agents", col)

    if "status" in cols:
        op.drop_column("agents", "status")

    try:
        sa.Enum(name="agent_status_enum").drop(bind, checkfirst=True)
    except Exception:
        pass
