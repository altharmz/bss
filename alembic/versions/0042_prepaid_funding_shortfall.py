"""Retain funding decisions independently of technical retry attempts."""

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE prepaid_funding_shortfall (
            command_id uuid NOT NULL REFERENCES prepaid_command(id),
            observed_at timestamptz NOT NULL,
            required numeric NOT NULL CHECK (required > 0 AND required < 'Infinity'::numeric),
            available numeric NOT NULL CHECK (available >= 0 AND available < required),
            PRIMARY KEY (command_id, observed_at)
        );
        CREATE FUNCTION prepaid_shortfall_retained() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'prepaid funding decisions must be retained' USING ERRCODE = '23514';
        END $$;
        CREATE TRIGGER prepaid_shortfall_retained BEFORE UPDATE OR DELETE
            ON prepaid_funding_shortfall FOR EACH ROW EXECUTE FUNCTION prepaid_shortfall_retained();
        COMMENT ON TABLE prepaid_funding_shortfall IS
            'Retained needs-top-up observations; current only while the command is pending. Not technical retry attempts.';
        COMMENT ON COLUMN prepaid_funding_shortfall.command_id IS 'Owning retained prepaid command.';
        COMMENT ON COLUMN prepaid_funding_shortfall.observed_at IS 'Injected-clock funding decision instant.';
        COMMENT ON COLUMN prepaid_funding_shortfall.required IS 'Exact tax-inclusive amount required in account currency.';
        COMMENT ON COLUMN prepaid_funding_shortfall.available IS 'Exact spendable amount observed under the account lock.';
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM prepaid_funding_shortfall) THEN
                RAISE EXCEPTION 'prepaid funding decisions must be retained at revision 0042';
            END IF;
        END $$;
        DROP TABLE prepaid_funding_shortfall;
        DROP FUNCTION prepaid_shortfall_retained();
    """)
