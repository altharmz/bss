"""Prefix search for the Subscribers screen (#314).

Revision ID: 0071
Revises: 0070

The screen pages on the server (no list counts, #312) and searches by prefix (decided
by the human 2026-11-10): a customer or account name (case-insensitive), a subscriber
number, or the short id (a prefix of the account or customer id, served by the primary
keys as a range). text_pattern_ops makes `LIKE 'prefix%'` use the indexes whatever the
database collation.
"""

from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX customer_name_prefix_idx ON customer (lower(name) text_pattern_ops);
        CREATE INDEX billing_account_name_prefix_idx
            ON billing_account (lower(name) text_pattern_ops);
        CREATE INDEX subscriber_number_value_prefix_idx
            ON subscriber_number (value text_pattern_ops)
            WHERE resource_specification_id = 'msisdn';
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX subscriber_number_value_prefix_idx;
        DROP INDEX billing_account_name_prefix_idx;
        DROP INDEX customer_name_prefix_idx;
    """)
