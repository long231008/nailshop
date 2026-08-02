"""preferred staff instead of pins

Naming a technician is demoted from a commitment to a preference: the
wish moves to booking_details.preferred_staff_id, the staff_requested
flag goes away, and existing PIN day-assignments become plain AUTO rows
(the pin system is deleted).

Revision ID: 6b90e4d21f83
Revises: 3d7f2c8a9e51
Create Date: 2026-08-02 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '6b90e4d21f83'
down_revision: Union[str, Sequence[str], None] = '3d7f2c8a9e51'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'booking_details',
        sa.Column('preferred_staff_id', sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        'fk_booking_details_preferred_staff',
        'booking_details',
        'staff',
        ['preferred_staff_id'],
        ['id'],
    )
    op.execute(
        "UPDATE booking_details SET preferred_staff_id = staff_id "
        "WHERE staff_requested = true"
    )
    op.drop_column('booking_details', 'staff_requested')
    op.execute("UPDATE staff_day_assignments SET source = 'AUTO' WHERE source = 'PIN'")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'booking_details',
        sa.Column('staff_requested', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        "UPDATE booking_details SET staff_requested = true "
        "WHERE preferred_staff_id IS NOT NULL"
    )
    op.alter_column('booking_details', 'staff_requested', server_default=None)
    op.drop_constraint('fk_booking_details_preferred_staff', 'booking_details', type_='foreignkey')
    op.drop_column('booking_details', 'preferred_staff_id')
    # PIN rows cannot be reconstructed - they stay AUTO.
