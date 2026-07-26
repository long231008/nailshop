"""add user names

Revision ID: f2b8d61c9a37
Revises: e7a92c15d4f8
Create Date: 2026-07-26 15:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2b8d61c9a37'
down_revision: Union[str, Sequence[str], None] = 'e7a92c15d4f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('first_name', sa.String(length=100), nullable=True))
    op.add_column('users', sa.Column('surname', sa.String(length=100), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'surname')
    op.drop_column('users', 'first_name')
