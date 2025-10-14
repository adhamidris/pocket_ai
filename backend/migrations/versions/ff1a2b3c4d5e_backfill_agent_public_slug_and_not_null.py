"""Backfill agents.public_slug and enforce NOT NULL + strict length constraint.

Revision ID: ff1a2b3c4d5e
Revises: c9b78fb0b6b7
Create Date: 2025-10-14
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text

# revision identifiers, used by Alembic.
revision = 'ff1a2b3c4d5e'
down_revision = 'c9b78fb0b6b7'
branch_labels = None
depends_on = None


def _slugify(value: str) -> str:
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)[:120]
    return slug


def upgrade() -> None:
    conn = op.get_bind()

    # 1) Collect existing non-null slugs per business (lowercased)
    existing_by_business: dict[str, set[str]] = {}
    rows = conn.execute(text("""
        SELECT business_id::text AS bid, lower(public_slug) AS slug
        FROM agents
        WHERE public_slug IS NOT NULL AND public_slug <> ''
    """)).fetchall()
    for r in rows:
        existing_by_business.setdefault(r.bid, set()).add(r.slug)

    # 2) Backfill missing/empty slugs with per-business-unique values
    agents = conn.execute(text("""
        SELECT id::text AS aid, business_id::text AS bid, name
        FROM agents
        WHERE public_slug IS NULL OR public_slug = ''
        ORDER BY business_id, created_at ASC, id ASC
    """)).fetchall()

    for a in agents:
        bid = a.bid
        existing = existing_by_business.setdefault(bid, set())

        base = _slugify(a.name) or "agent"
        candidate = base
        suffix = 2
        while candidate.lower() in existing:
            cand = f"{base}-{suffix}"
            if len(cand) > 120:
                cand = cand[:120]
            candidate = cand
            suffix += 1

        conn.execute(
            text("UPDATE agents SET public_slug = :slug WHERE id::text = :aid"),
            {"slug": candidate, "aid": a.aid},
        )
        existing.add(candidate.lower())

    # 3) Tighten check constraint to require 3..120 length (drop/recreate)
    #    Drop both possible names (with and without naming_convention prefix).
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_agents_public_slug_length")
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_agents_ck_agents_public_slug_length")
    # Create with a stable explicit name.
    op.execute(
        "ALTER TABLE agents "
        "ADD CONSTRAINT ck_agents_public_slug_length "
        "CHECK (char_length(public_slug) BETWEEN 3 AND 120)"
    )

    # 4) Set NOT NULL at DB level
    op.alter_column(
        'agents', 'public_slug',
        existing_type=sa.String(length=120),
        nullable=False,
    )


def downgrade() -> None:
    # 1) Allow NULLs again
    op.alter_column(
        'agents', 'public_slug',
        existing_type=sa.String(length=120),
        nullable=True,
    )

    # 2) Restore len constraint to allow NULLs (drop/recreate)
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_agents_public_slug_length")
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_agents_ck_agents_public_slug_length")
    op.execute(
        "ALTER TABLE agents "
        "ADD CONSTRAINT ck_agents_public_slug_length "
        "CHECK (public_slug IS NULL OR char_length(public_slug) BETWEEN 3 AND 120)"
    )
