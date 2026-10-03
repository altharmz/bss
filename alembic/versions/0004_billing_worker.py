"""billing worker: run bookkeeping, per-(run, account) outcomes, the UI request queue

Stage 4 (docs/stage4-design.md). Four parts:

- billing_run gets the worker's bookkeeping: trigger (scheduled | catchUp | retry),
  attempt, heartbeat_at, and failure_kind (accounts | run) - set exactly when the run is
  failed: 'accounts' = some account failed or is blocked, the worker moves on to later
  dates; 'run' = the run itself failed (database gone, a bug outside bill_account), the
  worker does not move past that date. No account counters: the counts are derived from
  billing_run_account and the run's members (accounts opened before 00:00 of the run
  date, which never changes: opened_at is immutable and accounts are never deleted), and
  "completed means every member is ok" is the worker's rule, proven by a test.
- billing_run_account: one row per (run, billing account) with the outcome ok | failed |
  blocked. A blocked row names the run it waits for (an earlier date that failed for
  this account); that it is earlier is the worker's rule, checked in tests - a CHECK
  cannot compare two rows.
- worker_request: catch-up and retry requests from the admin UI. The API only inserts
  them; the worker picks them up.
- billing_account.opened_at: when the account was created, by the injected clock. A run
  for D covers the accounts opened before 00:00 of D (BILLING_TZ); created_at is the
  database's now() and cannot be used for that (tests and the seed run on a FixedClock).

The enums are ours, not TMF. Existing billing_run rows (local seeds only; production has
none - checked before deploy) are completed runs: the defaults describe them correctly,
and the trigger default is dropped again so the worker must always say why a run started.
Existing failed rows (none expected) get failure_kind 'run' before the CHECK is added.

Written by hand - do not regenerate.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-03

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUDIT = """
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()"""


def upgrade() -> None:
    op.execute("CREATE TYPE billing_run_trigger AS ENUM ('scheduled', 'catchUp', 'retry')")
    op.execute("CREATE TYPE billing_run_failure_kind AS ENUM ('accounts', 'run')")
    op.execute("CREATE TYPE billing_run_account_status AS ENUM ('ok', 'failed', 'blocked')")
    op.execute("CREATE TYPE worker_request_kind AS ENUM ('catchUp', 'retry')")

    # --- billing_run: the worker's bookkeeping ---
    op.execute(
        """
        ALTER TABLE billing_run
            ADD COLUMN trigger billing_run_trigger NOT NULL DEFAULT 'scheduled',
            ADD COLUMN attempt integer NOT NULL DEFAULT 1
                CONSTRAINT billing_run_attempt_positive CHECK (attempt >= 1),
            ADD COLUMN heartbeat_at timestamptz,
            ADD COLUMN failure_kind billing_run_failure_kind
        """
    )
    op.execute("ALTER TABLE billing_run ALTER COLUMN trigger DROP DEFAULT")
    op.execute("UPDATE billing_run SET failure_kind = 'run' WHERE status = 'failed'")
    op.execute(
        "ALTER TABLE billing_run ADD CONSTRAINT billing_run_failure_kind_iff_failed"
        " CHECK ((status = 'failed') = (failure_kind IS NOT NULL))"
    )

    # --- billing_run_account: one outcome per (run, account) ---
    op.execute(
        """
        CREATE TABLE billing_run_account (
            run_id uuid NOT NULL REFERENCES billing_run (id),
            billing_account_id uuid NOT NULL REFERENCES billing_account (id),
            status billing_run_account_status NOT NULL,
            error text,
            waits_for_run_id uuid REFERENCES billing_run (id),
            attempts integer NOT NULL DEFAULT 1
                CONSTRAINT billing_run_account_attempts_positive CHECK (attempts >= 1),
            finished_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (run_id, billing_account_id),
            CONSTRAINT billing_run_account_error_iff_failed
                CHECK ((status = 'failed') = (error IS NOT NULL)),
            CONSTRAINT billing_run_account_waits_iff_blocked
                CHECK ((status = 'blocked') = (waits_for_run_id IS NOT NULL)),
            CONSTRAINT billing_run_account_waits_for_another_run
                CHECK (waits_for_run_id <> run_id)
        )
        """
    )
    # the worker's question: "the earliest failed run of this account"
    op.execute(
        "CREATE INDEX billing_run_account_unresolved ON billing_run_account"
        " (billing_account_id, run_id) WHERE status <> 'ok'"
    )

    # --- worker_request: the UI's queue; the API inserts, the worker picks up ---
    op.execute(
        f"""
        CREATE TABLE worker_request ({AUDIT},
            kind worker_request_kind NOT NULL,
            up_to date,
            run_id uuid REFERENCES billing_run (id),
            requested_at timestamptz NOT NULL,
            requested_by text,
            picked_at timestamptz,
            done_at timestamptz,
            CONSTRAINT worker_request_catch_up_has_date
                CHECK ((kind = 'catchUp') = (up_to IS NOT NULL)),
            CONSTRAINT worker_request_retry_has_run
                CHECK ((kind = 'retry') = (run_id IS NOT NULL)),
            CONSTRAINT worker_request_picked_after_requested
                CHECK (picked_at >= requested_at),
            CONSTRAINT worker_request_done_after_picked
                CHECK (done_at IS NULL OR (picked_at IS NOT NULL AND done_at >= picked_at))
        )
        """
    )
    op.execute(
        "CREATE INDEX worker_request_pending ON worker_request (requested_at)"
        " WHERE done_at IS NULL"
    )

    # --- billing_account.opened_at: creation time by the injected clock ---
    op.execute("ALTER TABLE billing_account ADD COLUMN opened_at timestamptz")
    op.execute("UPDATE billing_account SET opened_at = created_at")
    op.execute("ALTER TABLE billing_account ALTER COLUMN opened_at SET NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE billing_account DROP COLUMN opened_at")
    op.execute("DROP TABLE worker_request")
    op.execute("DROP TABLE billing_run_account")
    op.execute(
        """
        ALTER TABLE billing_run
            DROP CONSTRAINT billing_run_failure_kind_iff_failed,
            DROP COLUMN failure_kind,
            DROP COLUMN heartbeat_at,
            DROP COLUMN attempt,
            DROP COLUMN trigger
        """
    )
    op.execute("DROP TYPE worker_request_kind")
    op.execute("DROP TYPE billing_run_account_status")
    op.execute("DROP TYPE billing_run_failure_kind")
    op.execute("DROP TYPE billing_run_trigger")
