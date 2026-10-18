"""TMF666 attributes: every attribute of the account resources and of the billing cycle
specification (gap-analysis slice 5).

- `billing_account.description`, `billing_account.account_type`: as on the financial,
  party and settlement accounts (columns there since 0013/0014).
- `details` on `billing_account`, `financial_account`, `party_account`: the attributes
  that are documents to us (contact, taxExemption, paymentPlan, defaultPaymentMethod,
  paymentStatus, accountRelationship, a party account's billStructure) and the
  relatedParty list as sent, validated against the vendored spec. The owning customer or
  party stays in its column; billing never reads `details`.
- `billing_cycle_specification.details`: its validFor, a record only.

Revision ID: 0023
Revises: 0022
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

DETAILS = """ADD COLUMN details jsonb NOT NULL DEFAULT '{}'
                CHECK (jsonb_typeof(details) = 'object')"""
TABLES = ("billing_account", "financial_account", "party_account", "billing_cycle_specification")


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE billing_account
            ADD COLUMN description text,
            ADD COLUMN account_type text CHECK (account_type <> ''),
            {DETAILS};
        ALTER TABLE financial_account {DETAILS};
        ALTER TABLE party_account {DETAILS};
        ALTER TABLE billing_cycle_specification {DETAILS};
        COMMENT ON COLUMN billing_account.description IS 'TMF666 description.';
        COMMENT ON COLUMN billing_account.account_type IS 'TMF666 accountType, a record only.';
        COMMENT ON COLUMN billing_account.details IS
            'TMF666 descriptive attributes (contact, paymentPlan, relatedParty as sent, ...),'
            ' validated against the spec; billing never reads them.';
        COMMENT ON COLUMN financial_account.details IS
            'TMF666 descriptive attributes (contact, taxExemption, relatedParty as sent),'
            ' validated against the spec.';
        COMMENT ON COLUMN party_account.details IS
            'TMF666 descriptive attributes (contact, billStructure, relatedParty as sent, ...),'
            ' validated against the spec.';
        COMMENT ON COLUMN billing_cycle_specification.details IS
            'TMF666 validFor, a record only.';
    """)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} DROP COLUMN details")
    op.execute("ALTER TABLE billing_account DROP COLUMN description, DROP COLUMN account_type")
