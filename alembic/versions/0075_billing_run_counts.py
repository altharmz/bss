"""Stored member and outcome counts on billing_run (#284).

Revision ID: 0075
Revises: 0074

The runs list counted, for every run on the page, the accounts opened before its date and
its billing_run_account rows by status - about 4M rows for a page of 20 daily runs at 100k
subscribers. The counts are now stored on the run (decided by the human 2026-11-09,
docs/decisions/worker.md "Stored run counts"): member_count is written by start_run
(new, reopened or taken over), the outcome counts in the transaction of each outcome row.

The backfill counts the existing rows; member_count follows the current definition
(accounts opened before 00:00 of the run date in BILLING_TZ). Only daily runs have members
and outcomes; a prepaid command run keeps zeros.
"""

from alembic import op
from app.config import settings

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None

COLUMNS = ("member_count", "ok_count", "failed_count", "blocked_count")


def upgrade() -> None:
    op.execute(
        "ALTER TABLE billing_run "
        + ", ".join(f"ADD COLUMN {c} integer NOT NULL DEFAULT 0" for c in COLUMNS)
    )
    op.execute(
        """
        UPDATE billing_run r
           SET ok_count = o.ok, failed_count = o.failed, blocked_count = o.blocked
          FROM (SELECT run_id,
                       count(*) FILTER (WHERE status = 'ok') AS ok,
                       count(*) FILTER (WHERE status = 'failed') AS failed,
                       count(*) FILTER (WHERE status = 'blocked') AS blocked
                  FROM billing_run_account GROUP BY run_id) o
         WHERE o.run_id = r.id
        """
    )
    tz = settings.billing_tz.replace("'", "''")
    op.execute(
        f"""
        UPDATE billing_run r
           SET member_count = (SELECT count(*) FROM billing_account a
                                WHERE a.opened_at < (r.run_date::timestamp AT TIME ZONE '{tz}'))
         WHERE r.kind = 'daily'
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN billing_run.member_count IS
            'Daily run: its members, the accounts opened before 00:00 of run_date (BILLING_TZ), counted when the run starts, is reopened or taken over; less one for a member deleted before the run reached it. 0 for other kinds.';
        COMMENT ON COLUMN billing_run.ok_count IS
            'Members with an ok billing_run_account row; kept in the transaction that writes the row.';
        COMMENT ON COLUMN billing_run.failed_count IS
            'Members with a failed billing_run_account row; kept in the transaction that writes the row.';
        COMMENT ON COLUMN billing_run.blocked_count IS
            'Members with a blocked billing_run_account row; kept in the transaction that writes the row.';
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE billing_run " + ", ".join(f"DROP COLUMN {c}" for c in COLUMNS))
