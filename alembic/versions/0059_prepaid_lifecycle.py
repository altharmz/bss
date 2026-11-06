"""Prepaid line lifecycle: grace, suspension, deactivation and number aging (issue #188).

- `prepaid_lifecycle_setting`: the one row of durations (grace, suspension, number aging, the
  pay-as-you-go zero-balance trigger), edited by operators; transitions not yet taken follow it.
- `prepaid_lifecycle_event`: one row per product, action and business date the worker took (or
  tried): the idempotency guard and the audit.
- `product.lifecycle_suspended`: the suspension was taken by the lifecycle (a subset of
  `manually_suspended`, which stays "the status timeline says suspended"); unlike an
  operator's suspension it does not block renewal.
- `product_status_change.cause`, `number_assignment.aging_days`,
  `billing_account.balance_zero_since`.

Revision ID: 0059
Revises: 0058
"""

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE prepaid_lifecycle_setting (
            id boolean PRIMARY KEY DEFAULT true CHECK (id),
            grace_days integer NOT NULL DEFAULT 3 CHECK (grace_days BETWEEN 0 AND 365),
            suspension_days integer NOT NULL DEFAULT 30 CHECK (suspension_days BETWEEN 0 AND 3650),
            number_aging_days integer NOT NULL DEFAULT 90
                CHECK (number_aging_days BETWEEN 0 AND 3650),
            payg_zero_balance_days integer NOT NULL DEFAULT 7
                CHECK (payg_zero_balance_days BETWEEN 0 AND 365),
            updated_at timestamptz
        );
        INSERT INTO prepaid_lifecycle_setting (id) VALUES (true);
        COMMENT ON TABLE prepaid_lifecycle_setting IS
            'The prepaid lifecycle durations (one row). Transitions are derived from them on every worker tick, so a change applies to every transition not yet taken.';
        COMMENT ON COLUMN prepaid_lifecycle_setting.id IS 'Always true: the table holds one row.';
        COMMENT ON COLUMN prepaid_lifecycle_setting.grace_days IS
            'Days the service stays on after the plan expired unpaid (the grace period).';
        COMMENT ON COLUMN prepaid_lifecycle_setting.suspension_days IS
            'Days the line stays suspended on the network before it is deactivated.';
        COMMENT ON COLUMN prepaid_lifecycle_setting.number_aging_days IS
            'Days a deactivated line''s number is held (TMF639 quarantined) before it can be assigned again.';
        COMMENT ON COLUMN prepaid_lifecycle_setting.payg_zero_balance_days IS
            'Days a pay-as-you-go account may hold a zero balance before the grace period starts.';
        COMMENT ON COLUMN prepaid_lifecycle_setting.updated_at IS
            'Last change by an operator (injected clock); null while the defaults stand.';

        CREATE TABLE prepaid_lifecycle_event (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id uuid NOT NULL REFERENCES product(id),
            billing_account_id uuid NOT NULL REFERENCES billing_account(id),
            action text NOT NULL CHECK (action IN ('suspend', 'resume', 'deactivate')),
            business_date date NOT NULL CHECK (isfinite(business_date)),
            product_order_id uuid REFERENCES product_order(id),
            outcome text NOT NULL CHECK (outcome IN ('placed', 'rejected')),
            reason text,
            created_at timestamptz NOT NULL,
            UNIQUE (product_id, action, business_date)
        );
        CREATE INDEX prepaid_lifecycle_event_account_idx
            ON prepaid_lifecycle_event (billing_account_id, created_at);
        COMMENT ON TABLE prepaid_lifecycle_event IS
            'A lifecycle transition the worker took or tried for a product on a business date (one per product, action and date): the order it placed and whether the order engine accepted it.';
        COMMENT ON COLUMN prepaid_lifecycle_event.id IS 'Event UUID.';
        COMMENT ON COLUMN prepaid_lifecycle_event.product_id IS 'The line the transition is for.';
        COMMENT ON COLUMN prepaid_lifecycle_event.billing_account_id IS 'The product''s prepaid account.';
        COMMENT ON COLUMN prepaid_lifecycle_event.action IS
            'suspend (network suspend), resume (network restore) or deactivate (the product ends, network deactivate).';
        COMMENT ON COLUMN prepaid_lifecycle_event.business_date IS
            'The business date of the attempt; one attempt per action and date, a refused one is tried again the next date.';
        COMMENT ON COLUMN prepaid_lifecycle_event.product_order_id IS
            'The internal TMF622 order placed (null when none could be stored).';
        COMMENT ON COLUMN prepaid_lifecycle_event.outcome IS
            'placed: the order engine accepted the order; rejected: it refused it (reason).';
        COMMENT ON COLUMN prepaid_lifecycle_event.reason IS 'Why the order was rejected.';
        COMMENT ON COLUMN prepaid_lifecycle_event.created_at IS 'Instant from the injected clock.';

        ALTER TABLE product ADD COLUMN lifecycle_suspended boolean NOT NULL DEFAULT false,
            ADD CONSTRAINT product_lifecycle_suspended_check
                CHECK (NOT lifecycle_suspended OR manually_suspended);
        COMMENT ON COLUMN product.lifecycle_suspended IS
            'The status timeline''s suspension was taken by the prepaid lifecycle (manually_suspended is then true as well); it does not block renewal.';

        ALTER TABLE product_status_change
            ADD COLUMN cause text NOT NULL DEFAULT 'manual' CHECK (cause IN ('manual', 'lifecycle'));
        COMMENT ON COLUMN product_status_change.cause IS
            'manual (an operator or client order) or lifecycle (the worker''s grace/suspension policy).';

        ALTER TABLE number_assignment
            ADD COLUMN aging_days integer NOT NULL DEFAULT 0 CHECK (aging_days >= 0);
        COMMENT ON COLUMN number_assignment.aging_days IS
            'Aging days set when the prepaid lifecycle deactivated the line: the quarantine after released_on is the larger of this and the quarantine setting.';

        ALTER TABLE billing_account ADD COLUMN balance_zero_since date;
        COMMENT ON COLUMN billing_account.balance_zero_since IS
            'First business date of the current run of a zero spendable balance on a prepaid account with a pay-as-you-go line; the worker keeps it, null when the balance is above zero.';
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM prepaid_lifecycle_event)
               OR EXISTS (SELECT FROM product WHERE lifecycle_suspended)
               OR EXISTS (SELECT FROM product_status_change WHERE cause = 'lifecycle')
               OR EXISTS (SELECT FROM number_assignment WHERE aging_days > 0) THEN
                RAISE EXCEPTION 'lifecycle events, suspensions and aging must be retained at revision 0059';
            END IF;
        END $$;
        ALTER TABLE billing_account DROP COLUMN balance_zero_since;
        ALTER TABLE number_assignment DROP COLUMN aging_days;
        ALTER TABLE product_status_change DROP COLUMN cause;
        ALTER TABLE product DROP CONSTRAINT product_lifecycle_suspended_check,
            DROP COLUMN lifecycle_suspended;
        DROP TABLE prepaid_lifecycle_event;
        DROP TABLE prepaid_lifecycle_setting;
    """)
