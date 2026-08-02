"""reconcile drift and new indexes

slot_locks.created_at was created nullable by e7a92c15d4f8 while the
model says NOT NULL - the one drift `alembic check` still reported.
Also indexes for the queries the last refactors introduced.

Revision ID: 9e5a7c31d208
Revises: 6b90e4d21f83
Create Date: 2026-08-02 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9e5a7c31d208'
down_revision: Union[str, Sequence[str], None] = '6b90e4d21f83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("UPDATE slot_locks SET created_at = now() WHERE created_at IS NULL")
    op.alter_column('slot_locks', 'created_at', nullable=False)
    op.create_index(
        'ix_booking_details_preferred_staff', 'booking_details', ['preferred_staff_id']
    )
    op.create_index('ix_staff_rosters_staff_start', 'staff_rosters', ['staff_id', 'start_time'])
    op.create_index('ix_staff_capabilities_service', 'staff_capabilities', ['service_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_staff_capabilities_service', table_name='staff_capabilities')
    op.drop_index('ix_staff_rosters_staff_start', table_name='staff_rosters')
    op.drop_index('ix_booking_details_preferred_staff', table_name='booking_details')
    op.alter_column('slot_locks', 'created_at', nullable=True)
