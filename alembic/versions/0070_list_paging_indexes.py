"""Indexes for the TMF list endpoints: paging order and filters (#286).

Revision ID: 0070
Revises: 0069

Every TMF list pages `ORDER BY created_at, id` (app/repository/_sql.py `select_page`), and
none of the tables that grow with subscribers had that index, so every page read and
sorted the whole table. Decisions of #286 (approved by the human 2026-11-09):

- `(created_at, id)` on the tables that grow with subscribers. The catalog stays small and
  gets none.
- a btree for every equality filter on a reference column that had no index
  (`customer_bill.billing_account_id`, `refund.payment_id`) and for the name filters
  (`party.given_name`, `family_name`, `organization_name`, `customer.name`,
  `billing_account.name`); status/state filters get none (low selectivity).
- `usage_record (product_id, event_at DESC, id)`: productUsage lists are per product
  and ordered `event_at DESC, id`.

Plain CREATE INDEX in the migration's transaction: production data is small.
"""

from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None

PAGED = (
    "party",
    "customer",
    "billing_account",
    "product",
    "product_order",
    "cancel_product_order",
    "customer_bill",
    "payment",
    "refund",
)
FILTERS = (
    ("customer_bill", "billing_account_id"),
    ("refund", "payment_id"),
    ("party", "given_name"),
    ("party", "family_name"),
    ("party", "organization_name"),
    ("customer", "name"),
    ("billing_account", "name"),
)


def upgrade() -> None:
    statements = [f"CREATE INDEX {t}_created_at_id_idx ON {t} (created_at, id);" for t in PAGED]
    statements += [f"CREATE INDEX {t}_{c}_idx ON {t} ({c});" for t, c in FILTERS]
    statements.append(
        "CREATE INDEX usage_record_product_event_idx"
        " ON usage_record (product_id, event_at DESC, id);"
    )
    op.execute("\n".join(statements))


def downgrade() -> None:
    statements = ["DROP INDEX usage_record_product_event_idx;"]
    statements += [f"DROP INDEX {t}_{c}_idx;" for t, c in FILTERS]
    statements += [f"DROP INDEX {t}_created_at_id_idx;" for t in PAGED]
    op.execute("\n".join(statements))
