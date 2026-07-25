"""add approved_at to bookings

Revision ID: c4d1a0b7e921
Revises: 97fd9a2dce2c
Create Date: 2026-07-25 08:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d1a0b7e921'
down_revision: Union[str, Sequence[str], None] = '97fd9a2dce2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'bookings', sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('bookings', 'approved_at')
