"""Normalize customer_lifecycle_stage_enum to UPPERCASE and fix default.

- Renames lowercase enum labels to uppercase (if present).
- Ensures customers.lifecycle_stage default is 'LEAD'.
This is idempotent and safe if values are already uppercase.
"""
from alembic import op

# revision identifiers, used by Alembic.
# AFTER
revision = "fclu_20251014a"  # any short unique string, 12–16 chars is perfect
down_revision = "d3a9b0f1e1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
DO $do$
BEGIN
  -- Rename values to uppercase if the lowercase exists.
  IF EXISTS (
    SELECT 1 FROM pg_enum e
    JOIN pg_type t ON t.oid = e.enumtypid
    WHERE t.typname = 'customer_lifecycle_stage_enum' AND e.enumlabel = 'lead'
  ) THEN
    EXECUTE 'ALTER TYPE customer_lifecycle_stage_enum RENAME VALUE ''lead'' TO ''LEAD''';
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_enum e
    JOIN pg_type t ON t.oid = e.enumtypid
    WHERE t.typname = 'customer_lifecycle_stage_enum' AND e.enumlabel = 'prospect'
  ) THEN
    EXECUTE 'ALTER TYPE customer_lifecycle_stage_enum RENAME VALUE ''prospect'' TO ''PROSPECT''';
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_enum e
    JOIN pg_type t ON t.oid = e.enumtypid
    WHERE t.typname = 'customer_lifecycle_stage_enum' AND e.enumlabel = 'active'
  ) THEN
    EXECUTE 'ALTER TYPE customer_lifecycle_stage_enum RENAME VALUE ''active'' TO ''ACTIVE''';
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_enum e
    JOIN pg_type t ON t.oid = e.enumtypid
    WHERE t.typname = 'customer_lifecycle_stage_enum' AND e.enumlabel = 'churn_risk'
  ) THEN
    EXECUTE 'ALTER TYPE customer_lifecycle_stage_enum RENAME VALUE ''churn_risk'' TO ''CHURN_RISK''';
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_enum e
    JOIN pg_type t ON t.oid = e.enumtypid
    WHERE t.typname = 'customer_lifecycle_stage_enum' AND e.enumlabel = 'former'
  ) THEN
    EXECUTE 'ALTER TYPE customer_lifecycle_stage_enum RENAME VALUE ''former'' TO ''FORMER''';
  END IF;
END
$do$;

-- Ensure the column default is UPPERCASE.
ALTER TABLE customers
  ALTER COLUMN lifecycle_stage SET DEFAULT 'LEAD'::customer_lifecycle_stage_enum;
        """
    )


def downgrade() -> None:
    # Best-effort reverse (keeps current default).
    op.execute(
        """
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='customer_lifecycle_stage_enum' AND e.enumlabel='LEAD')
     AND NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='customer_lifecycle_stage_enum' AND e.enumlabel='lead')
  THEN EXECUTE 'ALTER TYPE customer_lifecycle_stage_enum RENAME VALUE ''LEAD'' TO ''lead'''; END IF;

  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='customer_lifecycle_stage_enum' AND e.enumlabel='PROSPECT')
     AND NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='customer_lifecycle_stage_enum' AND e.enumlabel='prospect')
  THEN EXECUTE 'ALTER TYPE customer_lifecycle_stage_enum RENAME VALUE ''PROSPECT'' TO ''prospect'''; END IF;

  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='customer_lifecycle_stage_enum' AND e.enumlabel='ACTIVE')
     AND NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='customer_lifecycle_stage_enum' AND e.enumlabel='active')
  THEN EXECUTE 'ALTER TYPE customer_lifecycle_stage_enum RENAME VALUE ''ACTIVE'' TO ''active'''; END IF;

  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='customer_lifecycle_stage_enum' AND e.enumlabel='CHURN_RISK')
     AND NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='customer_lifecycle_stage_enum' AND e.enumlabel='churn_risk')
  THEN EXECUTE 'ALTER TYPE customer_lifecycle_stage_enum RENAME VALUE ''CHURN_RISK'' TO ''churn_risk'''; END IF;

  IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='customer_lifecycle_stage_enum' AND e.enumlabel='FORMER')
     AND NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid WHERE t.typname='customer_lifecycle_stage_enum' AND e.enumlabel='former')
  THEN EXECUTE 'ALTER TYPE customer_lifecycle_stage_enum RENAME VALUE ''FORMER'' TO ''former'''; END IF;
END
$do$;
        """
    )
