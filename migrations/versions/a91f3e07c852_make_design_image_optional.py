"""make design image optional

Revision ID: a91f3e07c852
Revises: f2b8d61c9a37
Create Date: 2026-07-26 17:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a91f3e07c852'
down_revision: Union[str, Sequence[str], None] = 'f2b8d61c9a37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        'custom_designs', 'image_url', existing_type=sa.String(length=500), nullable=True
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'custom_designs', 'image_url', existing_type=sa.String(length=500), nullable=False
    )
