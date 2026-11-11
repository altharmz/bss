"""The consistency check of the stored run counts (#284), for tests only: every daily run's
member_count, ok_count, failed_count and blocked_count recounted live from billing_account
and billing_run_account, the way the runs view counted before the counts were stored."""

from datetime import date

import psycopg

from app.config import settings

COUNTS = ("member_count", "ok_count", "failed_count", "blocked_count")


def stored_and_live(conn: psycopg.Connection) -> dict[date, tuple[tuple, tuple]]:
    """Per run date: (stored counts, live counts), in COUNTS order."""
    rows = conn.execute(
        """
        SELECT r.run_date, r.member_count, r.ok_count, r.failed_count, r.blocked_count,
               (SELECT count(*) FROM billing_account a
                 WHERE a.opened_at < (r.run_date::timestamp AT TIME ZONE %(tz)s)),
               o.ok, o.failed, o.blocked
          FROM billing_run r
         CROSS JOIN LATERAL (
                SELECT count(*) FILTER (WHERE status = 'ok') AS ok,
                       count(*) FILTER (WHERE status = 'failed') AS failed,
                       count(*) FILTER (WHERE status = 'blocked') AS blocked
                  FROM billing_run_account WHERE run_id = r.id) o
         WHERE r.kind = 'daily'
         ORDER BY r.run_date
        """,
        {"tz": settings.billing_tz},
    ).fetchall()
    return {row[0]: (tuple(row[1:5]), tuple(row[5:9])) for row in rows}


def assert_consistent(conn: psycopg.Connection) -> dict[date, tuple]:
    """Every run's stored counts equal the live recount; the counts by run date."""
    counts = stored_and_live(conn)
    assert counts, "no daily run to check"
    wrong = {day: pair for day, pair in counts.items() if pair[0] != pair[1]}
    assert not wrong, f"stored != live (member, ok, failed, blocked): {wrong}"
    return {day: stored for day, (stored, _) in counts.items()}
