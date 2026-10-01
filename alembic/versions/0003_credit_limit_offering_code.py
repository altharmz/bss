"""billing_account.credit_limit and product_offering.code

- billing_account.credit_limit: TMF666 BillingAccount.creditLimit, in the account's own
  currency (so no currency column of its own). Informational only: ordering never checks
  it. NULL = no limit recorded.
- product_offering.code: the offering code, exposed as TMF620
  externalIdentifier[{id, externalIdentifierType: "offeringCode", owner: "bss"}]. Unique;
  NULL for offerings created without one (UNIQUE allows any number of NULLs). The pattern
  is ours: upper case, so uniqueness never depends on case.

Both columns are nullable and have no default: adding them changes no existing row.

Written by hand - do not regenerate.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-01

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE billing_account ADD COLUMN credit_limit numeric(12,2)"
        " CONSTRAINT billing_account_credit_limit_not_negative CHECK (credit_limit >= 0)"
    )
    op.execute(
        "ALTER TABLE product_offering ADD COLUMN code text"
        " CONSTRAINT product_offering_code_format CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{0,31}$')"
        " CONSTRAINT product_offering_code_key UNIQUE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE product_offering DROP COLUMN code")
    op.execute("ALTER TABLE billing_account DROP COLUMN credit_limit")
