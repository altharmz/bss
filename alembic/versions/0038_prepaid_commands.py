"""Retained prepaid command requests and retry results; no worker dispatch yet."""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE prepaid_command (
            id uuid PRIMARY KEY,
            billing_account_id uuid NOT NULL REFERENCES billing_account(id),
            kind text NOT NULL CHECK (kind IN ('order', 'renewal', 'transfer')),
            due_date date NOT NULL,
            request jsonb NOT NULL CHECK (jsonb_typeof(request) = 'object'),
            created_at timestamptz NOT NULL,
            state text NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending', 'completed', 'failedTechnical')),
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            attempted_at timestamptz,
            next_retry_at timestamptz,
            result jsonb CHECK (jsonb_typeof(result) = 'object'),
            error text CHECK (error <> ''),
            CHECK (attempted_at >= created_at),
            CHECK (
                (state = 'pending' AND result IS NULL AND next_retry_at IS NULL AND error IS NULL) OR
                (state = 'completed' AND result IS NOT NULL AND next_retry_at IS NULL AND error IS NULL
                    AND attempts > 0 AND attempted_at IS NOT NULL) OR
                (state = 'failedTechnical' AND result IS NULL AND error IS NOT NULL
                    AND attempts > 0 AND attempted_at IS NOT NULL
                    AND next_retry_at IS NOT NULL AND next_retry_at > attempted_at)
            ),
            CHECK ((attempts = 0) = (attempted_at IS NULL))
        );
        CREATE INDEX ON prepaid_command(billing_account_id);
        CREATE INDEX ON prepaid_command(due_date, id) WHERE state <> 'completed';
        CREATE FUNCTION prepaid_command_retained() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'prepaid command history must be retained' USING ERRCODE = '23514';
            END IF;
            IF (NEW.id, NEW.billing_account_id, NEW.kind, NEW.due_date, NEW.request, NEW.created_at)
                IS DISTINCT FROM
                (OLD.id, OLD.billing_account_id, OLD.kind, OLD.due_date, OLD.request, OLD.created_at)
                OR (OLD.state = 'completed' AND NEW IS DISTINCT FROM OLD)
                OR NEW.attempts < OLD.attempts OR NEW.attempts > OLD.attempts + 1
                OR NEW.attempted_at < OLD.attempted_at
                OR (NEW IS DISTINCT FROM OLD AND NEW.attempts = OLD.attempts AND NOT (
                    OLD.state = 'failedTechnical' AND NEW.state = 'pending'
                    AND NEW.attempted_at IS NOT DISTINCT FROM OLD.attempted_at
                ))
                OR (NEW.attempts = OLD.attempts + 1 AND NEW.state = 'pending') THEN
                RAISE EXCEPTION 'prepaid command request and completed result are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER prepaid_command_retained BEFORE UPDATE OR DELETE ON prepaid_command
            FOR EACH ROW EXECUTE FUNCTION prepaid_command_retained();
        CREATE TRIGGER credit_account_lock BEFORE INSERT ON prepaid_command
            FOR EACH ROW EXECUTE FUNCTION credit_account_lock();
        CREATE FUNCTION prepaid_command_hold_owner() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE command_uuid uuid;
        BEGIN
            IF TG_TABLE_NAME = 'prepaid_command' THEN command_uuid := NEW.id;
            ELSE command_uuid := NEW.command_id; END IF;
            IF EXISTS (
                SELECT FROM prepaid_command c JOIN funding_command_hold h ON h.command_id = c.id
                WHERE c.id = command_uuid AND c.billing_account_id <> h.billing_account_id
            ) THEN
                RAISE EXCEPTION 'prepaid command hold account mismatch' USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END $$;
        CREATE CONSTRAINT TRIGGER prepaid_command_hold_owner AFTER INSERT ON prepaid_command
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION prepaid_command_hold_owner();
        CREATE CONSTRAINT TRIGGER prepaid_command_hold_owner AFTER INSERT ON funding_command_hold
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION prepaid_command_hold_owner();
        CREATE FUNCTION prepaid_command_mode_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.rating_type IS DISTINCT FROM OLD.rating_type
                AND EXISTS (SELECT FROM prepaid_command WHERE billing_account_id = OLD.id) THEN
                RAISE EXCEPTION 'account mode cannot change after prepaid commands' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER prepaid_command_mode_history BEFORE UPDATE OF rating_type ON billing_account
            FOR EACH ROW EXECUTE FUNCTION prepaid_command_mode_history();
        COMMENT ON TABLE prepaid_command IS
            'Durable internal prepaid command journal; no runtime dispatcher or public API yet. Request and completed result are retained.';
    """)
    for column, comment in {
        "id": "Stable command UUID; shares the funding-command advisory lock identity.",
        "billing_account_id": "Owning billing account; additional transfer participants belong to the immutable request.",
        "kind": "Internal order, renewal or transfer command kind, not a TMF enum.",
        "due_date": "Immutable due business date; never a daily billing-run anchor.",
        "request": "Immutable checked quote/request document; money is encoded with exact Decimal JSON.",
        "created_at": "Injected-clock acceptance instant.",
        "state": "Internal pending, completed or failedTechnical recovery state.",
        "attempts": "Number of committed success or technical-failure decisions, retained across manual retries.",
        "attempted_at": "Injected-clock last recorded decision instant.",
        "next_retry_at": "Technical retry eligibility; one minute, five minutes, then hourly.",
        "result": "Immutable committed result, written in the same transaction as financial effects and events.",
        "error": "Last technical failure reason; business refusals belong in the completed result.",
    }.items():
        op.execute(f"COMMENT ON COLUMN prepaid_command.{column} IS '{comment}'")


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM prepaid_command) THEN
                RAISE EXCEPTION 'prepaid command history must be retained at revision 0038';
            END IF;
        END $$;
        DROP TRIGGER prepaid_command_mode_history ON billing_account;
        DROP FUNCTION prepaid_command_mode_history();
        DROP TRIGGER prepaid_command_hold_owner ON funding_command_hold;
        DROP TABLE prepaid_command;
        DROP FUNCTION prepaid_command_retained();
        DROP FUNCTION prepaid_command_hold_owner();
    """)
