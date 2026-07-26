"""add slot locks and lock permission

Revision ID: e7a92c15d4f8
Revises: c4d1a0b7e921
Create Date: 2026-07-26 13:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e7a92c15d4f8'
down_revision: Union[str, Sequence[str], None] = 'c4d1a0b7e921'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'staff',
        sa.Column('can_lock_slots', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        'slot_locks',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('branch_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('staff_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reason', sa.String(length=255), nullable=True),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['branch_id'], ['locations.id']),
        sa.ForeignKeyConstraint(['staff_id'], ['staff.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_slot_locks_branch_time', 'slot_locks', ['branch_id', 'start_time', 'end_time']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_slot_locks_branch_time', table_name='slot_locks')
    op.drop_table('slot_locks')
    op.drop_column('staff', 'can_lock_slots')
