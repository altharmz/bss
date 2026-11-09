"""Which prepaid accounts the worker's per-account passes evaluate on this tick (#292).

The lifecycle, entitlement and low-balance/grace passes change their outcome only with the
business date or with the account's money and products. Triggers (migration 0068) mark an
account in `account_review` whenever a row those passes read changes. A tick evaluates
either every prepaid account (a full sweep: the first tick of a business date, or after a
settings change) or only the marked ones, then deletes the marks at the versions it read.
docs/decisions/worker.md, "Account review".
"""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

import psycopg

from app.config import settings as app_settings
from app.repository import lifecycle


@dataclass(frozen=True)
class Review:
    full: bool
    today: date
    settings: str
    marks: tuple[tuple[UUID, int], ...]

    @property
    def accounts(self) -> list[UUID] | None:
        """The accounts to evaluate; None means all of them."""
        return None if self.full else [account for account, _ in self.marks]


def settings_key(conn: psycopg.Connection) -> str:
    """Every setting the passes decide with, as one comparable text."""
    row = lifecycle.get_row(conn)
    return "|".join(
        str(value)
        for value in (
            row["grace_days"],
            row["suspension_days"],
            row["number_aging_days"],
            row["payg_zero_balance_days"],
            app_settings.notify_low_balance,
            app_settings.notify_expiring_days,
        )
    )


def begin(conn: psycopg.Connection, today: date) -> Review:
    key = settings_key(conn)
    sweep = conn.execute("SELECT swept_on, settings FROM account_review_sweep").fetchone()
    marks = tuple(
        (account, version)
        for account, version in conn.execute(
            "SELECT billing_account_id, version FROM account_review ORDER BY billing_account_id"
        )
    )
    full = sweep is None or sweep[0] != today or sweep[1] != key
    return Review(full, today, key, marks)


def finish(conn: psycopg.Connection, review: Review) -> None:
    """After every pass completed: the marks read are evaluated, a full sweep recorded."""
    with conn.transaction():
        if review.marks:
            conn.execute(
                "DELETE FROM account_review r USING unnest(%s::uuid[], %s::bigint[]) AS m(a, v)"
                " WHERE r.billing_account_id = m.a AND r.version = m.v",
                ([a for a, _ in review.marks], [v for _, v in review.marks]),
            )
        if review.full:
            conn.execute(
                "INSERT INTO account_review_sweep (id, swept_on, settings) VALUES (true, %s, %s)"
                " ON CONFLICT (id) DO UPDATE SET swept_on = EXCLUDED.swept_on,"
                " settings = EXCLUDED.settings",
                (review.today, review.settings),
            )
