"""Which prepaid accounts the worker's per-account passes must look at (#292).

Revision ID: 0068
Revises: 0067

The prepaid lifecycle, entitlement and low-balance/grace notification passes used to lock
and evaluate every prepaid account on every tick (every minute). Their outcome changes only
with the business date (grace, suspension and expiry are date-granular) or with the account's
money and products. So (design approved by the human 2026-11-09, docs/decisions/worker.md):

- `account_review` holds a prepaid account whose inputs changed. Triggers on every table
  those passes read mark the account, whichever code path wrote, in the writer's own
  transaction. `version` grows with each mark, so the worker deletes only the marks it has
  evaluated: a change made during a tick is evaluated on the next one.
- `account_review_sweep` (one row) records the business date of the last full sweep and
  the settings it used. A new date or other settings mean a full sweep. Kept in the
  database, so a worker restart needs no sweep of its own.
"""

from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None

# table -> how a row names its account: the account columns, or 'product' / 'command'
BY_ACCOUNT = {
    "payment": ["billing_account_id"],
    "balance_action": ["billing_account_id", "receiver_billing_account_id"],
    "balance_action_reversal": ["billing_account_id"],
    "bill_allocation": ["billing_account_id"],
    "customer_bill": ["billing_account_id"],
    "applied_customer_billing_rate": ["billing_account_id"],
    "funded_interval": ["billing_account_id"],
    "funding_consumption": ["billing_account_id"],
    "funding_hold": ["billing_account_id"],
    "funding_hold_source": ["billing_account_id"],
    "funding_source": ["billing_account_id"],
    "prepaid_command": ["billing_account_id"],
    "product": ["billing_account_id"],
    "refund": ["billing_account_id"],
    "refund_source": ["billing_account_id"],
    "usage_charge": ["billing_account_id"],
}
BY_PRODUCT = ["product_status_change", "product_order_item", "product_offering_change", "usage_bar"]


def upgrade() -> None:
    triggers = []
    for table, columns in BY_ACCOUNT.items():
        args = ", ".join(f"'{c}'" for c in columns)
        triggers.append(
            f"CREATE TRIGGER account_review AFTER INSERT OR UPDATE OR DELETE ON {table}"
            f" FOR EACH ROW EXECUTE FUNCTION account_review_by_column({args});"
        )
    for table in BY_PRODUCT:
        triggers.append(
            f"CREATE TRIGGER account_review AFTER INSERT OR UPDATE OR DELETE ON {table}"
            " FOR EACH ROW EXECUTE FUNCTION account_review_by_product();"
        )
    op.execute(
        """
        CREATE SEQUENCE account_review_version_seq;
        CREATE TABLE account_review (
            billing_account_id uuid PRIMARY KEY,
            version bigint NOT NULL
        );
        COMMENT ON TABLE account_review IS
            'Prepaid accounts whose money or products changed since the worker last evaluated them (lifecycle, entitlement and notification passes). Written by triggers; the worker deletes the versions it evaluated.';
        COMMENT ON COLUMN account_review.billing_account_id IS 'The prepaid account to evaluate.';
        COMMENT ON COLUMN account_review.version IS
            'Grows with every change (account_review_version_seq): the worker deletes a mark only at the version it read.';

        CREATE TABLE account_review_sweep (
            id boolean PRIMARY KEY DEFAULT true CHECK (id),
            swept_on date NOT NULL,
            settings text NOT NULL
        );
        COMMENT ON TABLE account_review_sweep IS
            'The last full sweep of the per-account passes (one row): a later business date or other settings start a new one.';
        COMMENT ON COLUMN account_review_sweep.id IS 'Always true: the table holds one row.';
        COMMENT ON COLUMN account_review_sweep.swept_on IS 'Business date (BILLING_TZ) of the last full sweep.';
        COMMENT ON COLUMN account_review_sweep.settings IS
            'The lifecycle and notification settings that sweep used, as text; a difference means a full sweep.';

        CREATE FUNCTION account_review_mark(acct uuid) RETURNS void LANGUAGE plpgsql AS $$
        BEGIN
            IF acct IS NOT NULL
               AND EXISTS (SELECT FROM billing_account WHERE id = acct AND rating_type = 'prepaid')
            THEN
                INSERT INTO account_review (billing_account_id, version)
                VALUES (acct, nextval('account_review_version_seq'))
                ON CONFLICT (billing_account_id)
                DO UPDATE SET version = nextval('account_review_version_seq');
            END IF;
        END $$;

        CREATE FUNCTION account_review_by_column() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            col text;
        BEGIN
            FOREACH col IN ARRAY TG_ARGV LOOP
                IF TG_OP <> 'DELETE' THEN
                    PERFORM account_review_mark((to_jsonb(NEW) ->> col)::uuid);
                END IF;
                IF TG_OP <> 'INSERT' THEN
                    PERFORM account_review_mark((to_jsonb(OLD) ->> col)::uuid);
                END IF;
            END LOOP;
            RETURN NULL;
        END $$;

        CREATE FUNCTION account_review_by_product() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP <> 'DELETE' THEN
                PERFORM account_review_mark(
                    (SELECT billing_account_id FROM product WHERE id = NEW.product_id));
            END IF;
            IF TG_OP <> 'INSERT' THEN
                PERFORM account_review_mark(
                    (SELECT billing_account_id FROM product WHERE id = OLD.product_id));
            END IF;
            RETURN NULL;
        END $$;

        CREATE FUNCTION account_review_by_command() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM account_review_mark(
                (SELECT billing_account_id FROM prepaid_command WHERE id = NEW.command_id));
            RETURN NULL;
        END $$;

        -- the account row itself (state, balance_zero_since, ...)
        CREATE FUNCTION account_review_by_account() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM account_review_mark(NEW.id);
            RETURN NULL;
        END $$;
        CREATE TRIGGER account_review AFTER UPDATE ON billing_account
            FOR EACH ROW EXECUTE FUNCTION account_review_by_account();
        CREATE TRIGGER account_review AFTER INSERT ON prepaid_funding_shortfall
            FOR EACH ROW EXECUTE FUNCTION account_review_by_command();
        """
        + "\n".join(triggers)
    )


def downgrade() -> None:
    drops = [f"DROP TRIGGER account_review ON {t};" for t in [*BY_ACCOUNT, *BY_PRODUCT]]
    op.execute(
        "\n".join(drops)
        + """
        DROP TRIGGER account_review ON prepaid_funding_shortfall;
        DROP TRIGGER account_review ON billing_account;
        DROP FUNCTION account_review_by_account();
        DROP FUNCTION account_review_by_command();
        DROP FUNCTION account_review_by_product();
        DROP FUNCTION account_review_by_column();
        DROP FUNCTION account_review_mark(uuid);
        DROP TABLE account_review_sweep;
        DROP TABLE account_review;
        DROP SEQUENCE account_review_version_seq;
        """
    )
