"""drop queue_tickets

The shop is appointment-only now: the walk-in QR queue is gone, so the
table behind it goes too.

Revision ID: 3d7f2c8a9e51
Revises: 8c2e4b9f1a70
Create Date: 2026-08-02 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3d7f2c8a9e51'
down_revision: Union[str, Sequence[str], None] = '8c2e4b9f1a70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index('ix_queue_tickets_branch_status', table_name='queue_tickets')
    op.drop_table('queue_tickets')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table('queue_tickets',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('ticket_number', sa.String(length=20), nullable=False),
    sa.Column('branch_id', sa.UUID(), nullable=False),
    sa.Column('customer_id', sa.UUID(), nullable=True),
    sa.Column('booking_id', sa.UUID(), nullable=True),
    sa.Column('ticket_type', sa.Enum('WALKIN', 'BOOKING', name='queue_ticket_type', native_enum=False, length=20), nullable=False),
    sa.Column('status', sa.Enum('WAITING', 'CALLED', 'IN_SERVICE', 'DONE', 'CANCELLED', name='queue_ticket_status', native_enum=False, length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ),
    sa.ForeignKeyConstraint(['branch_id'], ['locations.id'], ),
    sa.ForeignKeyConstraint(['customer_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ticket_number')
    )
    op.create_index('ix_queue_tickets_branch_status', 'queue_tickets', ['branch_id', 'status'])
