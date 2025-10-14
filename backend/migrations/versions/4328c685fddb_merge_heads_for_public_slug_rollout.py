"""merge heads for public_slug rollout

Revision ID: 4328c685fddb
Revises: fclu_20251014a, ff1a2b3c4d5e
Create Date: 2025-10-14 20:21:50.522961

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4328c685fddb'
down_revision: Union[str, Sequence[str], None] = ('fclu_20251014a', 'ff1a2b3c4d5e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
