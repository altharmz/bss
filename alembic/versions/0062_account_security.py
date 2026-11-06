"""Account security: customer PIN, per-line locks and the audit log (issue #193).

Revision ID: 0062
Revises: 0061
"""

from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE customer_pin (
            customer_id uuid PRIMARY KEY REFERENCES customer(id),
            pin_hash text NOT NULL CHECK (pin_hash <> ''),
            set_at timestamptz NOT NULL,
            set_by text NOT NULL CHECK (set_by <> ''),
            reason text NOT NULL CHECK (reason <> ''),
            failed_attempts integer NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
            locked_until timestamptz
        );
        COMMENT ON TABLE customer_pin IS
            'The account PIN of a customer (CPNI verification): hashed, set or reset by an agent with a recorded reason. No API returns it. Sensitive actions on a protected customer (a live prepaid product) need it in the X-Account-Pin header.';
        COMMENT ON COLUMN customer_pin.pin_hash IS 'scrypt hash (the format of app_user.password_hash) of the 6 to 8 digit PIN; never logged or returned.';
        COMMENT ON COLUMN customer_pin.set_by IS 'The operator who set or last reset it.';
        COMMENT ON COLUMN customer_pin.reason IS 'Why it was set or reset (recorded by the agent).';
        COMMENT ON COLUMN customer_pin.failed_attempts IS 'Consecutive wrong PINs; a right one resets it, the lockout starts at the configured maximum.';
        COMMENT ON COLUMN customer_pin.locked_until IS 'While in the future, verification is refused even for the right PIN.';

        CREATE TABLE line_lock (
            product_id uuid PRIMARY KEY REFERENCES product(id),
            sim_swap boolean NOT NULL DEFAULT false,
            port_out boolean NOT NULL DEFAULT false,
            updated_at timestamptz NOT NULL,
            updated_by text NOT NULL CHECK (updated_by <> ''),
            reason text NOT NULL CHECK (reason <> '')
        );
        COMMENT ON TABLE line_lock IS
            'Optional protection of one line: while sim_swap is set a SIM swap order is refused, while port_out is set no Number Transfer PIN is issued and a carrier port-out is rejected, even with a valid PIN. No row means both off.';
        COMMENT ON COLUMN line_lock.sim_swap IS 'SIM swap lock.';
        COMMENT ON COLUMN line_lock.port_out IS 'Port-out lock.';
        COMMENT ON COLUMN line_lock.reason IS 'Why the lock was last changed (recorded by the agent).';

        CREATE TABLE account_audit (
            id uuid PRIMARY KEY,
            seq bigint GENERATED ALWAYS AS IDENTITY,
            at timestamptz NOT NULL,
            operator text NOT NULL CHECK (operator <> ''),
            role text,
            customer_id uuid,
            billing_account_id uuid,
            product_id uuid,
            action text NOT NULL CHECK (action IN (
                'sim_swap', 'number_change', 'number_transfer_pin', 'contact_change',
                'port_out', 'pin_set', 'pin_reset', 'lock_set', 'lock_cleared',
                'pin_lockout')),
            outcome text NOT NULL CHECK (outcome IN ('done', 'refused')),
            reason text,
            verification text NOT NULL CHECK (verification IN ('pin', 'notRequired', 'none')),
            detail jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(detail) = 'object'),
            CHECK (outcome = 'done' OR reason IS NOT NULL)
        );
        CREATE INDEX account_audit_customer_idx ON account_audit (customer_id, at DESC, seq DESC);
        CREATE INDEX account_audit_product_idx ON account_audit (product_id, at DESC, seq DESC);
        CREATE INDEX account_audit_at_idx ON account_audit (at DESC, seq DESC);
        COMMENT ON TABLE account_audit IS
            'Append-only log of agent actions on accounts: who, what, when, whether it was done or refused and how the customer was verified. No foreign keys: the log outlives what it describes. Never holds a PIN.';
        COMMENT ON COLUMN account_audit.seq IS 'Insertion order: the tie-break when two rows share a time.';
        COMMENT ON COLUMN account_audit.operator IS 'The signed-in user, the MCP principal, or carrier for a carrier request.';
        COMMENT ON COLUMN account_audit.action IS 'sim_swap, number_change, number_transfer_pin, contact_change, port_out, pin_set, pin_reset, lock_set, lock_cleared, pin_lockout.';
        COMMENT ON COLUMN account_audit.outcome IS 'done, or refused (see reason).';
        COMMENT ON COLUMN account_audit.reason IS 'Why it was refused (pinRequired, pinInvalid, pinNotSet, pinLocked, simSwapLocked, portOutLocked) or the reason the agent gave for a PIN or lock change.';
        COMMENT ON COLUMN account_audit.verification IS 'pin (a valid PIN was sent), notRequired (the switch is off), none (no PIN was needed or sent).';
        COMMENT ON COLUMN account_audit.detail IS 'What the action touched (order, SIM, number, fields changed), and for a contact change the addresses the alert goes to. No secrets.';

        CREATE FUNCTION account_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'account_audit is append-only';
        END $$;
        CREATE TRIGGER account_audit_immutable BEFORE UPDATE OR DELETE ON account_audit
            FOR EACH ROW EXECUTE FUNCTION account_audit_immutable();
        CREATE TRIGGER account_audit_no_truncate BEFORE TRUNCATE ON account_audit
            FOR EACH STATEMENT EXECUTE FUNCTION account_audit_immutable();
    """)


def downgrade() -> None:
    # a rollback discards the PINs, the locks and the audit log (the log is append-only for
    # the application; dropping the table is the operator's decision)
    op.execute("""
        DROP TABLE account_audit;
        DROP FUNCTION account_audit_immutable();
        DROP TABLE line_lock;
        DROP TABLE customer_pin;
    """)
