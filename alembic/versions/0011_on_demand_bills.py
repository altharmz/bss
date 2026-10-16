"""Worker-owned off-cycle bills and durable requests; monthly anchors stay unchanged."""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE customer_bill ADD COLUMN run_type text NOT NULL DEFAULT 'onCycle'
            CHECK (run_type IN ('onCycle', 'offCycle'));
        ALTER TABLE customer_bill DROP CONSTRAINT customer_bill_no_overlap;
        ALTER TABLE customer_bill ADD CONSTRAINT customer_bill_no_overlap EXCLUDE USING gist
            (billing_account_id WITH =, bill_period WITH &&) WHERE (run_type = 'onCycle');
        COMMENT ON COLUMN customer_bill.run_type IS 'TMF678 runType; off-cycle documents do not advance monthly billing anchors.';
        CREATE TYPE customer_bill_on_demand_state_type AS ENUM ('inProgress', 'rejected', 'done', 'terminatedWithError');
        CREATE TABLE customer_bill_on_demand (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            billing_account_id uuid NOT NULL REFERENCES billing_account(id),
            customer_bill_id uuid REFERENCES customer_bill(id),
            requested_at timestamptz NOT NULL,
            ready_on date NOT NULL,
            state customer_bill_on_demand_state_type NOT NULL DEFAULT 'inProgress',
            name text,
            description text,
            CHECK ((state = 'done') = (customer_bill_id IS NOT NULL))
        );
        CREATE UNIQUE INDEX customer_bill_on_demand_pending ON customer_bill_on_demand(billing_account_id)
            WHERE state = 'inProgress';
        CREATE INDEX ON customer_bill_on_demand(state, requested_at);
    """)
    for column, meaning in {
        "id": "Server-generated request identifier.", "created_at": "Database insertion time.",
        "updated_at": "Last request transition by the injected clock.",
        "billing_account_id": "Account to bill.", "customer_bill_id": "Off-cycle document when done.",
        "requested_at": "Request time from injected clock.", "ready_on": "First daily run that can see facts completed on the request day.",
        "state": "TMF678 request state.", "name": "Optional friendly name.",
        "description": "Request description or rejection/error explanation.",
    }.items():
        op.execute(f"COMMENT ON COLUMN customer_bill_on_demand.{column} IS '{meaning}'")
    op.execute("COMMENT ON TABLE customer_bill_on_demand IS 'Durable TMF678 on-demand request consumed by the billing owner.'")


def downgrade() -> None:
    # Refuse destructive rollback while off-cycle financial history exists.
    op.execute("""
        DO $$ BEGIN IF EXISTS (SELECT FROM customer_bill WHERE run_type = 'offCycle') THEN
            RAISE EXCEPTION 'off-cycle history exists; downgrade requires a reviewed data migration';
        END IF; END $$;
        DROP TABLE customer_bill_on_demand;
        DROP TYPE customer_bill_on_demand_state_type;
        ALTER TABLE customer_bill DROP CONSTRAINT customer_bill_no_overlap;
        ALTER TABLE customer_bill ADD CONSTRAINT customer_bill_no_overlap EXCLUDE USING gist
            (billing_account_id WITH =, bill_period WITH &&);
        ALTER TABLE customer_bill DROP COLUMN run_type;
    """)
