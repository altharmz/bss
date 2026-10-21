"""Separate retained prepaid command runs from daily scheduling identities."""

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE billing_run
            ADD COLUMN kind text NOT NULL DEFAULT 'daily'
                CHECK (kind IN ('daily', 'prepaidCommand')),
            ADD COLUMN command_id uuid UNIQUE REFERENCES prepaid_command(id),
            ADD CONSTRAINT billing_run_command_kind
                CHECK ((kind = 'prepaidCommand') = (command_id IS NOT NULL)),
            DROP CONSTRAINT billing_run_run_date_key;
        CREATE UNIQUE INDEX billing_run_daily_date ON billing_run(run_date) WHERE kind = 'daily';
        CREATE FUNCTION billing_run_identity_retained() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.kind = 'prepaidCommand' THEN
                    RAISE EXCEPTION 'prepaid command run history must be retained' USING ERRCODE = '23514';
                END IF;
                RETURN OLD;
            END IF;
            IF (NEW.kind, NEW.command_id, NEW.run_date, NEW.id) IS DISTINCT FROM
               (OLD.kind, OLD.command_id, OLD.run_date, OLD.id) THEN
                RAISE EXCEPTION 'billing run identity is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER billing_run_identity_retained BEFORE UPDATE OR DELETE ON billing_run
            FOR EACH ROW EXECUTE FUNCTION billing_run_identity_retained();
        CREATE FUNCTION billing_run_daily_reference() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME = 'worker_request' THEN
                IF NEW.run_id IS NULL THEN RETURN NEW; END IF;
            END IF;
            IF EXISTS (SELECT FROM billing_run WHERE id = NEW.run_id AND kind <> 'daily') THEN
                RAISE EXCEPTION 'daily bookkeeping requires a daily run' USING ERRCODE = '23514';
            END IF;
            IF TG_TABLE_NAME = 'billing_run_account' THEN
                IF EXISTS (SELECT FROM billing_run WHERE id = NEW.waits_for_run_id AND kind <> 'daily') THEN
                    RAISE EXCEPTION 'daily blocking requires a daily run' USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER billing_run_daily_reference BEFORE INSERT OR UPDATE ON billing_run_account
            FOR EACH ROW EXECUTE FUNCTION billing_run_daily_reference();
        CREATE TRIGGER billing_run_daily_reference BEFORE INSERT OR UPDATE ON worker_request
            FOR EACH ROW EXECUTE FUNCTION billing_run_daily_reference();
        COMMENT ON TABLE billing_run IS
            'Daily runs have one identity per business date; prepaid command runs have one retained identity per command. Only billing runs write ABRs and bills.';
        COMMENT ON COLUMN billing_run.kind IS
            'Internal daily or prepaidCommand identity; only daily runs drive scheduling, health and daily APIs.';
        COMMENT ON COLUMN billing_run.command_id IS
            'Retained one-to-one prepaid command identity; null on daily runs.';
        COMMENT ON COLUMN billing_run.run_date IS
            'Business date; unique only for daily runs. Command runs retain their actual authorization date.';
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM billing_run WHERE kind <> 'daily') THEN
                RAISE EXCEPTION 'prepaid command run history must be retained at revision 0039';
            END IF;
        END $$;
        DROP TRIGGER billing_run_daily_reference ON worker_request;
        DROP TRIGGER billing_run_daily_reference ON billing_run_account;
        DROP FUNCTION billing_run_daily_reference();
        DROP TRIGGER billing_run_identity_retained ON billing_run;
        DROP FUNCTION billing_run_identity_retained();
        DROP INDEX billing_run_daily_date;
        ALTER TABLE billing_run DROP CONSTRAINT billing_run_command_kind,
            DROP COLUMN command_id, DROP COLUMN kind,
            ADD CONSTRAINT billing_run_run_date_key UNIQUE (run_date);
        COMMENT ON TABLE billing_run IS
            'One daily billing run per date (ours, not TMF). Only a billing run writes ABRs and bills; the worker''s bookkeeping lives here (trigger, attempt, failure_kind).';
        COMMENT ON COLUMN billing_run.run_date IS
            'The business date the run bills (unique). It sees orders completed before 00:00 of this date (BILLING_TZ); ABRs it writes carry this run date.';
    """)
