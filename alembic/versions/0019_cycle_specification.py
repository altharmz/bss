"""TMF666 billingCycleSpecification writes: named specifications of the one policy.

Billing has one policy: calendar-monthly periods from the account's billCycleDay, bills
on the period start. A stored specification is a named instance of exactly that
(billingPeriod and frequency monthly, billingDateShift 0 - CHECK-enforced), so billing
never reads this table. A billing account may name one (`cycle_specification_id`; NULL =
the built-in `monthly`, which is not stored).

Revision ID: 0019
Revises: 0018
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE billing_cycle_specification (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            name text NOT NULL CHECK (length(name) > 0),
            description text,
            billing_period text NOT NULL DEFAULT 'monthly' CHECK (billing_period = 'monthly'),
            frequency text NOT NULL DEFAULT 'monthly' CHECK (frequency = 'monthly'),
            billing_date_shift integer NOT NULL DEFAULT 0 CHECK (billing_date_shift = 0)
        );
        CREATE TRIGGER billing_cycle_specification_set_updated_at
            BEFORE UPDATE ON billing_cycle_specification
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        ALTER TABLE billing_account
            ADD COLUMN cycle_specification_id uuid REFERENCES billing_cycle_specification;
        CREATE INDEX billing_account_cycle_specification_idx
            ON billing_account (cycle_specification_id);
        COMMENT ON TABLE billing_cycle_specification IS
            'TMF666 billingCycleSpecification: named instances of the one monthly policy; billing never reads it.';
        COMMENT ON COLUMN billing_account.cycle_specification_id IS
            'billStructure.cycleSpecification; NULL = the built-in monthly specification.';
    """)
    for column, meaning in {
        "id": "Server-generated identifier.",
        "created_at": "Database insertion time; stable list ordering.",
        "updated_at": "Database update time.",
        "name": "Display name; nonempty.",
        "description": "Optional description.",
        "billing_period": "TMF billingPeriod: monthly, the only period billing supports.",
        "frequency": "TMF frequency: monthly, the only one billing supports.",
        "billing_date_shift": "TMF billingDateShift: 0 - bills on the period start.",
    }.items():
        op.execute(f"COMMENT ON COLUMN billing_cycle_specification.{column} IS '{meaning}'")


def downgrade() -> None:
    op.execute("""
        ALTER TABLE billing_account DROP COLUMN cycle_specification_id;
        DROP TABLE billing_cycle_specification;
    """)
