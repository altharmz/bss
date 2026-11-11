"""Paging indexes for the Numbers, SIMs and Balances screens (#314).

Revision ID: 0073
Revises: 0072

These screens page on the server now (#314), `ORDER BY created_at, id` like every list
(0070), but the subscriber_number and prepaid_bucket tables, which grow with subscribers,
had no index for that order: each page sorted the whole table (on the 100k load-test
dataset, 80-120 ms for a first page of 50).
"""

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX subscriber_number_created_at_id_idx ON subscriber_number (created_at, id);
        CREATE INDEX prepaid_bucket_created_at_id_idx ON prepaid_bucket (created_at, id);
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX prepaid_bucket_created_at_id_idx;
        DROP INDEX subscriber_number_created_at_id_idx;
    """)
