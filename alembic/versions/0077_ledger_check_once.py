"""The balance ledger check runs once per account and transaction (#305).

Revision ID: 0077
Revises: 0076

`balance_ledger_bounds` is a deferred constraint trigger on eleven tables: at commit it
ran `balance_ledger_check` once per written row, so a prepaid transaction that writes a
payment, a hold, its sources and a bill ran the same whole-account check several times
(472k calls, 813 s over 8.5 h of the 100k load-test seed).

A check reads the account as the transaction has left it, so a second check of the same
account sees the same rows and is skipped: the trigger notes each checked account in the
transaction-local setting `bss.ledger_checked`. Any new write statement on a bounded table
empties the note first (a BEFORE ... FOR EACH STATEMENT trigger), so a row written after a
check - after `SET CONSTRAINTS balance_ledger_bounds IMMEDIATE`, as the transfer path
does - is checked again. A rolled-back savepoint takes its notes with it, which can only
mean an extra check. The check itself is unchanged.
"""

from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None

# The tables of 0045's BOUNDED, the ones carrying balance_ledger_bounds.
BOUNDED = (
    "balance_action", "balance_action_reversal", "funding_source", "funding_consumption",
    "payment", "customer_bill", "bill_allocation", "refund_source",
    "funding_hold", "funding_hold_source", "funding_conversion_source",
)  # fmt: skip

ONCE = r"""
CREATE OR REPLACE FUNCTION balance_ledger_bounds() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    checked text := coalesce(current_setting('bss.ledger_checked', true), '');
    accounts uuid[] := ARRAY[NEW.billing_account_id];
    acct uuid;
BEGIN
    IF TG_TABLE_NAME = 'balance_action' THEN
        -- a separate statement: NEW of the other tables has no such field
        accounts := accounts || NEW.receiver_billing_account_id;
    END IF;
    FOREACH acct IN ARRAY accounts LOOP
        CONTINUE WHEN acct IS NULL OR position(acct::text IN checked) > 0;
        PERFORM balance_ledger_check(acct);
        checked := checked || acct::text || ' ';
        PERFORM set_config('bss.ledger_checked', checked, true);
    END LOOP;
    RETURN NULL;
END $$;

CREATE FUNCTION balance_ledger_unchecked() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF coalesce(current_setting('bss.ledger_checked', true), '') <> '' THEN
        PERFORM set_config('bss.ledger_checked', '', true);
    END IF;
    RETURN NULL;
END $$;
"""

EVERY_ROW = r"""
CREATE OR REPLACE FUNCTION balance_ledger_bounds() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM balance_ledger_check(NEW.billing_account_id);
    IF TG_TABLE_NAME = 'balance_action' THEN
        PERFORM balance_ledger_check(NEW.receiver_billing_account_id);
    END IF;
    RETURN NULL;
END $$;
"""


def upgrade() -> None:
    op.execute(ONCE)
    for table in BOUNDED:
        op.execute(
            f"CREATE TRIGGER balance_ledger_unchecked BEFORE INSERT OR UPDATE ON {table}"
            " FOR EACH STATEMENT EXECUTE FUNCTION balance_ledger_unchecked()"
        )


def downgrade() -> None:
    for table in BOUNDED:
        op.execute(f"DROP TRIGGER balance_ledger_unchecked ON {table}")
    op.execute("DROP FUNCTION balance_ledger_unchecked()")
    op.execute(EVERY_ROW)
