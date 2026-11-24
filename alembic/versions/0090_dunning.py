"""Postpaid dunning: reminders, suspension, restoration (#385).

Revision ID: 0090
Revises: 0089

docs/decisions/dunning.md. Everything derives from O, the first business date an account has
an open, non-held bill past its `paymentDueDate`, and the settings; nothing stores a state
machine.

- `dunning_setting`: the one row of step days and the minimum overdue amount, edited by
  operators; steps not yet taken follow it.
- `dunning_step`: a step the worker took for an account and O (once each): the idempotency guard
  and the durable record the notification detector reads.
- `product.dunning_suspended`: the product's suspension was placed by dunning, so restoration
  resumes only those (an operator's suspension stays).
- `product_status_change.cause` takes `dunning`.
- `billing_account.dunning_exempt`: the operator's exemption; `account_audit.action` takes
  `dunning_exemption`.
- Two partial indexes: the open positive bills by due date (the daily sweep) and the
  dunning-suspended products (the restoration check on every tick).
"""

from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None

ACTIONS = (
    "'sim_swap', 'number_change', 'number_transfer_pin', 'contact_change',"
    " 'port_out', 'pin_set', 'pin_reset', 'lock_set', 'lock_cleared',"
    " 'pin_lockout', 'interaction_logged', 'ticket_opened', 'ticket_status_changed',"
    " 'care_note_added', 'care_sign_in', 'care_locked', 'care_password_changed', 'e911_changed',"
    " 'care_top_up', 'care_card_saved', 'care_card_removed', 'care_auto_top_up', 'care_add_on'"
)
ACTION_COMMENT = (
    "sim_swap, number_change, number_transfer_pin, contact_change, port_out, pin_set, pin_reset,"
    " lock_set, lock_cleared, pin_lockout, and the care work of issue 199: interaction_logged,"
    " ticket_opened, ticket_status_changed, care_note_added, and self-care of issue 195:"
    " care_sign_in, care_locked, care_password_changed, and the E911 address change of issue"
    " 201: e911_changed, and the self-care writes of issue 376: care_top_up, care_card_saved,"
    " care_card_removed, care_auto_top_up, care_add_on"
)


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE dunning_setting (
            id boolean PRIMARY KEY DEFAULT true CHECK (id),
            reminder1_days integer NOT NULL DEFAULT 1 CHECK (reminder1_days BETWEEN 0 AND 365),
            reminder2_days integer NOT NULL DEFAULT 7 CHECK (reminder2_days BETWEEN 0 AND 365),
            suspend_days integer NOT NULL DEFAULT 14 CHECK (suspend_days BETWEEN 0 AND 365),
            minimum_overdue numeric(12,2) NOT NULL DEFAULT 5.00 CHECK (minimum_overdue >= 0),
            updated_at timestamptz,
            CONSTRAINT dunning_setting_increasing
                CHECK (reminder1_days < reminder2_days AND reminder2_days < suspend_days)
        );
        INSERT INTO dunning_setting (id) VALUES (true);
        COMMENT ON TABLE dunning_setting IS
            'The postpaid dunning steps (one row): days after O, the first overdue date, and the smallest overdue amount that counts. The worker derives the steps from them on every pass, so a change applies to the steps not yet taken.';
        COMMENT ON COLUMN dunning_setting.id IS 'Always true: the table holds one row.';
        COMMENT ON COLUMN dunning_setting.reminder1_days IS
            'Days after O of the first reminder (email and SMS).';
        COMMENT ON COLUMN dunning_setting.reminder2_days IS
            'Days after O of the second reminder; greater than reminder1_days.';
        COMMENT ON COLUMN dunning_setting.suspend_days IS
            'Days after O on which every active product of the account is suspended through an internal order; greater than reminder2_days.';
        COMMENT ON COLUMN dunning_setting.minimum_overdue IS
            'In the account currency: an account owing less past due is not in dunning (no step, and its dunning suspensions are lifted).';
        COMMENT ON COLUMN dunning_setting.updated_at IS
            'Last change by an operator (injected clock); null while the defaults stand.';

        CREATE TABLE dunning_step (
            billing_account_id uuid NOT NULL REFERENCES billing_account(id),
            overdue_since date NOT NULL CHECK (isfinite(overdue_since)),
            step text NOT NULL CHECK (step IN ('reminder1', 'reminder2', 'suspend', 'restore')),
            business_date date NOT NULL CHECK (business_date >= overdue_since),
            overdue_amount numeric(12,2) NOT NULL CHECK (overdue_amount >= 0),
            products integer NOT NULL DEFAULT 0 CHECK (products >= 0),
            taken_at timestamptz NOT NULL,
            PRIMARY KEY (billing_account_id, overdue_since, step)
        );
        CREATE INDEX dunning_step_taken_idx ON dunning_step (taken_at);
        COMMENT ON TABLE dunning_step IS
            'A dunning step the worker took for an account and O (once each): the guard that makes every step happen once, and the record the notification detector reads.';
        COMMENT ON COLUMN dunning_step.billing_account_id IS 'The postpaid account.';
        COMMENT ON COLUMN dunning_step.overdue_since IS
            'O: the first business date the account had an open, non-held bill past its payment due date (the oldest such due date + 1) when the step was taken.';
        COMMENT ON COLUMN dunning_step.step IS
            'reminder1, reminder2 (a message), suspend (internal suspend orders) or restore (internal resume orders once nothing counts as overdue; O is then the suspension''s).';
        COMMENT ON COLUMN dunning_step.business_date IS 'Business date (BILLING_TZ) of the step.';
        COMMENT ON COLUMN dunning_step.overdue_amount IS
            'What the account owed past due when the step was taken, in its currency (0 for restore).';
        COMMENT ON COLUMN dunning_step.products IS
            'suspend and restore: how many products got an order the engine accepted; 0 for a reminder.';
        COMMENT ON COLUMN dunning_step.taken_at IS 'Instant from the injected clock.';

        ALTER TABLE product ADD COLUMN dunning_suspended boolean NOT NULL DEFAULT false;
        COMMENT ON COLUMN product.dunning_suspended IS
            'The product''s applied suspension was placed by postpaid dunning (cause dunning): restoration resumes it, an operator''s suspension it leaves alone. Cleared by any later applied status change.';
        CREATE INDEX product_dunning_suspended_idx ON product (billing_account_id)
            WHERE dunning_suspended;

        ALTER TABLE product_status_change
            DROP CONSTRAINT product_status_change_cause_check,
            ADD CONSTRAINT product_status_change_cause_check
                CHECK (cause IN ('manual', 'lifecycle', 'dunning'));
        COMMENT ON COLUMN product_status_change.cause IS
            'manual (an operator or client order), lifecycle (the worker''s prepaid grace/suspension policy) or dunning (the worker''s postpaid dunning suspension and restoration).';

        ALTER TABLE billing_account ADD COLUMN dunning_exempt boolean NOT NULL DEFAULT false;
        COMMENT ON COLUMN billing_account.dunning_exempt IS
            'An operator exempted the account from postpaid dunning: no step is taken and its dunning suspensions are lifted. Each change writes an account_audit row.';

        CREATE INDEX customer_bill_open_due_idx ON customer_bill (payment_due_date)
            WHERE state IN ('sent', 'partiallyPaid') AND amount_due > 0;

        ALTER TABLE account_audit DROP CONSTRAINT account_audit_action_check;
        ALTER TABLE account_audit ADD CONSTRAINT account_audit_action_check
            CHECK (action IN ({ACTIONS}, 'dunning_exemption'));
        COMMENT ON COLUMN account_audit.action IS '{ACTION_COMMENT}, and the dunning exemption of issue 385: dunning_exemption.';
    """)


def downgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM dunning_step)
               OR EXISTS (SELECT FROM product WHERE dunning_suspended)
               OR EXISTS (SELECT FROM product_status_change WHERE cause = 'dunning')
               OR EXISTS (SELECT FROM billing_account WHERE dunning_exempt)
               OR EXISTS (SELECT FROM account_audit WHERE action = 'dunning_exemption') THEN
                RAISE EXCEPTION 'dunning steps, suspensions and exemptions must be retained at revision 0090';
            END IF;
        END $$;
        ALTER TABLE account_audit DROP CONSTRAINT account_audit_action_check;
        ALTER TABLE account_audit ADD CONSTRAINT account_audit_action_check
            CHECK (action IN ({ACTIONS}));
        COMMENT ON COLUMN account_audit.action IS '{ACTION_COMMENT}.';
        DROP INDEX customer_bill_open_due_idx;
        ALTER TABLE billing_account DROP COLUMN dunning_exempt;
        ALTER TABLE product_status_change
            DROP CONSTRAINT product_status_change_cause_check,
            ADD CONSTRAINT product_status_change_cause_check
                CHECK (cause IN ('manual', 'lifecycle'));
        COMMENT ON COLUMN product_status_change.cause IS
            'manual (an operator or client order) or lifecycle (the worker''s grace/suspension policy).';
        DROP INDEX product_dunning_suspended_idx;
        ALTER TABLE product DROP COLUMN dunning_suspended;
        DROP TABLE dunning_step;
        DROP TABLE dunning_setting;
    """)
