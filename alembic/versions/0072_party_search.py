"""Prefix search for the Parties screen and the account owner picker (#314).

Revision ID: 0072
Revises: 0071

Like the Subscribers search (0071): the screen pages on the server without counts (#312)
and finds a party by the start of its name, case-insensitive. An individual matches by
given name or "given family" (one expression) and by family name; an organization by its
name. Customers already have customer_name_prefix_idx (0071). text_pattern_ops makes
`LIKE 'prefix%'` use the indexes whatever the database collation.
"""

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX party_full_name_prefix_idx
            ON party ((lower(coalesce(given_name, '') || ' ' || family_name)) text_pattern_ops)
            WHERE party_type = 'Individual';
        CREATE INDEX party_family_name_prefix_idx
            ON party (lower(family_name) text_pattern_ops) WHERE party_type = 'Individual';
        CREATE INDEX party_organization_name_prefix_idx
            ON party (lower(organization_name) text_pattern_ops)
            WHERE party_type = 'Organization';
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX party_organization_name_prefix_idx;
        DROP INDEX party_family_name_prefix_idx;
        DROP INDEX party_full_name_prefix_idx;
    """)
