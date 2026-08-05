"""branch physical resources, staff leaves, desk-created bookings

Adds:
- locations.pedicure_chairs / manicure_tables / massage_beds (0 = untracked)
- bookings.staff_created (desk walk-in/phone bookings)
- staff_leaves table (a technician off across the whole chain for a time range)

Revision ID: b8d3f1e6c920
Revises: 9e5a7c31d208
Create Date: 2026-08-03 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b8d3f1e6c920'
down_revision: Union[str, Sequence[str], None] = '9e5a7c31d208'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'locations',
        sa.Column('pedicure_chairs', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'locations',
        sa.Column('manicure_tables', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'locations',
        sa.Column('massage_beds', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'bookings',
        sa.Column('staff_created', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        'staff_leaves',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('staff_id', sa.UUID(), nullable=False),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reason', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_staff_leaves_staff_time', 'staff_leaves', ['staff_id', 'start_time', 'end_time']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_staff_leaves_staff_time', table_name='staff_leaves')
    op.drop_table('staff_leaves')
    op.drop_column('bookings', 'staff_created')
    op.drop_column('locations', 'massage_beds')
    op.drop_column('locations', 'manicure_tables')
    op.drop_column('locations', 'pedicure_chairs')
