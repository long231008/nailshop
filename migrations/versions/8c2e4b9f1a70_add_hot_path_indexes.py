"""add hot-path indexes

The schema shipped with almost no secondary indexes; these cover the
columns the application actually filters and joins on.

Revision ID: 8c2e4b9f1a70
Revises: b64bebd58e35
Create Date: 2026-08-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8c2e4b9f1a70'
down_revision: Union[str, Sequence[str], None] = 'b64bebd58e35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index('ix_bookings_customer_id', 'bookings', ['customer_id'])
    op.create_index('ix_bookings_branch_date', 'bookings', ['branch_id', 'booking_date'])
    op.create_index('ix_booking_details_booking_id', 'booking_details', ['booking_id'])
    op.create_index('ix_booking_details_staff_start', 'booking_details', ['staff_id', 'start_time'])
    op.create_index('ix_payment_transactions_booking_id', 'payment_transactions', ['booking_id'])
    op.create_index('ix_queue_tickets_branch_status', 'queue_tickets', ['branch_id', 'status'])
    op.create_index(
        'ix_staff_day_assignments_branch_day', 'staff_day_assignments', ['branch_id', 'day']
    )
    op.create_index('ix_audit_logs_created_at', 'audit_logs', ['created_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_audit_logs_created_at', table_name='audit_logs')
    op.drop_index('ix_staff_day_assignments_branch_day', table_name='staff_day_assignments')
    op.drop_index('ix_queue_tickets_branch_status', table_name='queue_tickets')
    op.drop_index('ix_payment_transactions_booking_id', table_name='payment_transactions')
    op.drop_index('ix_booking_details_staff_start', table_name='booking_details')
    op.drop_index('ix_booking_details_booking_id', table_name='booking_details')
    op.drop_index('ix_bookings_branch_date', table_name='bookings')
    op.drop_index('ix_bookings_customer_id', table_name='bookings')
