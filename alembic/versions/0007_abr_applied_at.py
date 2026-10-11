"""applied_customer_billing_rate.applied_at: the ABR's TMF678 `date` from the clock

TMF678 `date` (the ABR's creation date) was the audit column created_at - the database's
now(), outside the injected clock - so data written under a FixedClock (tests, the seed's
simulated history) showed the time the rows were inserted, not the simulated day
(docs/doc-findings.md #9). applied_at is the injected clock of the billing run's attempt
that wrote the ABR: the same instant the bills of that account and run are issued at
(adapter.bill_account, `issued_at`). Kept in its own column because billing_run.started_at
moves on every reopen or takeover.

Existing ABRs get their run's started_at - the same back-fill 0006 used for the issue rows
of existing bills. The ABR was written by the run's last attempt, or by an earlier one
whose start is no longer kept; started_at is the best value there is.

Written by hand - do not regenerate.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-11

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPLIED_AT = (
    "TMF678 date: when the ABR was written - the injected clock of the billing run's"
    " attempt that wrote it (the instant that run issues its bills at), not the"
    " database's now()."
)


def upgrade() -> None:
    op.execute("ALTER TABLE applied_customer_billing_rate ADD COLUMN applied_at timestamptz")
    op.execute(
        """
        UPDATE applied_customer_billing_rate a SET applied_at = r.started_at
          FROM billing_run r WHERE r.id = a.billing_run_id
        """
    )
    op.execute("ALTER TABLE applied_customer_billing_rate ALTER COLUMN applied_at SET NOT NULL")
    op.execute(
        "COMMENT ON COLUMN applied_customer_billing_rate.applied_at IS "
        + "'"
        + APPLIED_AT.replace("'", "''")
        + "'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE applied_customer_billing_rate DROP COLUMN applied_at")
