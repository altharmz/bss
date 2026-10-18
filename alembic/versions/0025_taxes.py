"""Taxes (gap-analysis slice 7): a price's tax items are applied by billing.

- `applied_customer_billing_rate.applied_tax`: the ABR's tax lines (TMF678
  AppliedBillingTaxRate: taxCategory, taxRate in percent, taxAmount value), and
  `tax_amount`, their sum. `amount` stays the amount before tax. Existing ABRs have no
  lines: they were charged without taxes.
- `payment.tax_amount`, `refund.tax_amount`: TMF676 taxAmount as sent (0 when not sent);
  `amount` stays the total, the amount before tax is `amount - tax_amount`.
- A price's `tax` stays in `product_offering_price.details`, now read by billing. The
  upgrade refuses to run if a stored tax record would not pass the new rules, or if an
  offering already charged has a price with a tax: its charges would no longer match their
  price. Both can exist only where 0024 (not deployed) ran.

Revision ID: 0025
Revises: 0024
"""

from decimal import ROUND_HALF_EVEN, Decimal

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def _check_price_taxes() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, amount, currency, details->'tax' AS tax FROM product_offering_price"
            " WHERE details ? 'tax'"
        )
    ).fetchall()
    for price_id, amount, currency, tax in rows:
        categories = []
        for item in tax:
            rate = item.get("taxRate")
            category = item.get("taxCategory")
            if not isinstance(category, str) or not category:
                raise RuntimeError(f"price {price_id}: a tax item without taxCategory")
            if isinstance(rate, bool) or not isinstance(rate, int | float):
                raise RuntimeError(f"price {price_id}: tax item {category} without taxRate")
            if not 0 <= rate <= 100:
                raise RuntimeError(f"price {price_id}: taxRate {rate} not in 0..100")
            sent = item.get("taxAmount")
            if sent is not None:
                if amount is None:
                    raise RuntimeError(f"price {price_id}: taxAmount of {category} without price")
                due = (amount * Decimal(str(rate)) / 100).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_EVEN
                )
                if sent.get("unit") != currency or Decimal(str(sent.get("value"))) != due:
                    raise RuntimeError(f"price {price_id}: taxAmount of {category} is not {due}")
            categories.append(category)
        if len(set(categories)) != len(categories):
            raise RuntimeError(f"price {price_id}: a taxCategory twice")
    charged = conn.execute(
        sa.text(
            "SELECT p.id FROM product_offering_price p"
            " WHERE p.details ? 'tax' AND jsonb_array_length(p.details->'tax') > 0"
            " AND EXISTS (SELECT 1 FROM applied_customer_billing_rate a"
            "  JOIN product d ON d.id = a.product_id"
            "  WHERE d.product_offering_id = p.product_offering_id)"
        )
    ).fetchall()
    if charged:
        raise RuntimeError(
            f"prices {[str(r[0]) for r in charged]} carry a tax on an offering already charged"
        )


def upgrade() -> None:
    _check_price_taxes()
    op.execute("""
        ALTER TABLE applied_customer_billing_rate
            ADD COLUMN applied_tax jsonb NOT NULL DEFAULT '[]'
                CHECK (jsonb_typeof(applied_tax) = 'array'),
            ADD COLUMN tax_amount numeric(12,2) NOT NULL DEFAULT 0,
            ADD CONSTRAINT applied_customer_billing_rate_no_lines_no_tax
                CHECK (applied_tax <> '[]' OR tax_amount = 0);
        ALTER TABLE payment
            ADD COLUMN tax_amount numeric(12,2) NOT NULL DEFAULT 0,
            ADD CONSTRAINT payment_tax_within_amount
                CHECK (tax_amount >= 0 AND tax_amount <= amount);
        ALTER TABLE refund
            ADD COLUMN tax_amount numeric(12,2) NOT NULL DEFAULT 0,
            ADD CONSTRAINT refund_tax_within_amount
                CHECK (tax_amount >= 0 AND tax_amount <= amount);
        COMMENT ON COLUMN applied_customer_billing_rate.applied_tax IS
            'TMF678 appliedTax: [{taxCategory, taxRate (percent; 0 = exempt), taxAmount}],'
            ' one per tax item of the price, in its order.';
        COMMENT ON COLUMN applied_customer_billing_rate.tax_amount IS
            'Money. The sum of applied_tax amounts; amount + tax_amount = taxIncludedAmount.';
        COMMENT ON COLUMN payment.tax_amount IS
            'Money. TMF676 taxAmount as sent (0: not sent); amount - tax_amount is TMF amount.';
        COMMENT ON COLUMN refund.tax_amount IS
            'Money. TMF676 taxAmount as sent (0: not sent); amount - tax_amount is TMF amount.';
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE refund DROP COLUMN tax_amount;
        ALTER TABLE payment DROP COLUMN tax_amount;
        ALTER TABLE applied_customer_billing_rate DROP COLUMN tax_amount,
            DROP COLUMN applied_tax;
    """)
