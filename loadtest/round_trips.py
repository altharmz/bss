"""Statements per billed account, per swept account and per usage record (#305).

`make loadtest-round-trips DATE=<run date> [ACCOUNTS=2000] [RECORDS=5000]` runs this in
lt-tools on the restored dataset (docs/loadtest.md, "Round trips"), in one transaction that
is rolled back at the end: the dataset stays as it was, and the code before and after a
change is measured on the same rows. (A copy with CREATE DATABASE ... TEMPLATE crashed
lt-db's backend at 100k.) Deferred constraint triggers would fire at the outer commit, so
the round-trip steps do not run them (they are server time, not round trips); the ledger
step fires them itself.

- **billed**: the daily run of DATE (the worker's DateRun) for the first ACCOUNTS
  members, one account per step as the worker runs it;
- **swept**: the per-account passes of a full review day (lifecycle, entitlements,
  notifications) for the first ACCOUNTS active prepaid accounts, at DATE 23:00;
- **usage**: RECORDS feed records for active lines through the feed's `ingest_batch` in
  batches of 500, at DATE 23:30, and 200 through the one-record `ingest` (TMF635 POST);
- **ledger**: `balance_ledger_check` calls per prepaid transaction (a hold placed and
  released) for the first LEDGER of those prepaid accounts that have funds.

Every statement psycopg sends is counted, BEGIN, SAVEPOINT and COMMIT included (they are
round trips too), and listed by its first words. The report goes to stdout and
loadtest/out/round-trips-<time>.json.
"""

# A load-test tool: SQL from fixed text and the parsed date.
# ruff: noqa: S608

import argparse
import json
import os
import re
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg

from app.clock import FixedClock
from app.config import settings
from app.network.usage import Incoming, ingest, ingest_batch
from app.repository import funding
from app.worker import db as worker_db
from app.worker.lifecycle import ACCOUNTS as PREPAID_ACCOUNTS
from app.worker.lifecycle import process_lifecycle
from app.worker.notify import process_notifications
from app.worker.prepaid import observe_entitlements
from app.worker.process import DateRun
from app.worker.runs import start_run

BATCH = 500
CENT = Decimal("0.01")


@contextmanager
def counting() -> Iterator[list[str]]:
    """Every statement psycopg executes while the block runs (as tests/querycount), and the
    transaction control psycopg sends on its own (BEGIN, SAVEPOINT, COMMIT, RELEASE)."""
    seen: list[str] = []
    real_execute = psycopg.Cursor.execute
    real_exec_command = psycopg.Connection._exec_command

    def execute(self, query, *args, **kwargs):
        seen.append(str(query))
        return real_execute(self, query, *args, **kwargs)

    def exec_command(self, command, *args, **kwargs):
        seen.append(command.decode() if isinstance(command, bytes) else str(command))
        return real_exec_command(self, command, *args, **kwargs)

    psycopg.Cursor.execute = execute
    psycopg.Connection._exec_command = exec_command
    try:
        yield seen
    finally:
        psycopg.Cursor.execute = real_execute
        psycopg.Connection._exec_command = real_exec_command


def shape(query: str) -> str:
    """A statement by its first words, values and Composed wrappers left out."""
    text = re.sub(r"(Composed|SQL|Identifier|Literal|Placeholder)\(\[?|['\"\])]", " ", query)
    return " ".join(text.split())[:90]


def measured(name: str, units: int, run: Callable[[], None]) -> dict:
    with counting() as seen:
        run()
    by_shape = Counter(shape(q) for q in seen)
    return {
        "step": name,
        "units": units,
        "statements": len(seen),
        "per_unit": round(len(seen) / max(units, 1), 3),
        "top": [
            {"per_unit": round(n / max(units, 1), 3), "statement": s}
            for s, n in by_shape.most_common(25)
        ],
    }


def ledger_checks(conn: psycopg.Connection, accounts: list[UUID], now: datetime) -> dict:
    """balance_ledger_check calls per prepaid transaction: for each account with funds, a
    hold of 0.01 placed and released (what an order or renewal does to the funding
    tables), then its deferred checks fired with SET CONSTRAINTS ... IMMEDIATE and
    counted from the transaction's function statistics (track_functions = pl)."""
    conn.execute("SET track_functions = 'pl'")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")  # the earlier steps' checks fire now
    conn.execute("SET CONSTRAINTS ALL DEFERRED")
    calls = "SELECT coalesce(pg_stat_get_xact_function_calls('balance_ledger_check'::regproc), 0)"
    transactions = checks = 0
    for account_id in accounts:
        if funding.load(conn, account_id).spendable < CENT:
            continue
        before = conn.execute(calls).fetchone()[0]
        with conn.transaction():
            hold_id = funding.reserve(conn, account_id, CENT, held_at=now)
            funding.release(conn, account_id, hold_id, released_at=now)
            conn.execute("SET CONSTRAINTS balance_ledger_bounds IMMEDIATE")
            conn.execute("SET CONSTRAINTS balance_ledger_bounds DEFERRED")
        checks += conn.execute(calls).fetchone()[0] - before
        transactions += 1
    return {
        "transactions": transactions,
        "checks": checks,
        "per_transaction": round(checks / max(transactions, 1), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--accounts", type=int, default=2000)
    parser.add_argument("--records", type=int, default=5000)
    parser.add_argument("--ledger", type=int, default=500)
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    if os.environ.get("APP_ENV") != "test":
        raise SystemExit("round-trips: APP_ENV=test only (the load-test stack)")
    tz = ZoneInfo(settings.billing_tz)
    report: dict = {"date": str(args.date), "label": args.label, "steps": []}
    conn = worker_db.connect()
    worker_db.acquire_ownership(conn)  # a session lock: it outlives the rollback
    # one outer transaction, rolled back at the end: the worker's per-account BEGIN and
    # COMMIT become SAVEPOINT and RELEASE, the same number of round trips
    conn.autocommit = False
    try:

        def at(hour: int, minute: int) -> FixedClock:
            return FixedClock(datetime.combine(args.date, time(hour, minute), tzinfo=tz))

        clock = at(0, 31)
        started = start_run(conn, args.date, now=clock.now(), trigger="scheduled", tz=tz.key)
        date_run = DateRun(conn, started.run, clock=clock, tz=tz)
        billed = min(args.accounts, len(date_run.todo))

        def bill() -> None:
            for _ in range(billed):
                date_run.step()

        report["steps"].append(measured("billed", billed, bill))

        prepaid = [
            row[0]
            for row in conn.execute(
                PREPAID_ACCOUNTS + " ORDER BY a.id LIMIT %s", (args.accounts,)
            ).fetchall()
        ]
        clock = at(23, 0)

        def sweep() -> None:
            for step in (process_lifecycle, observe_entitlements, process_notifications):
                step(conn, clock=clock, tz=tz, accounts=prepaid)

        report["steps"].append(measured("swept", len(prepaid), sweep))

        lines = conn.execute(
            "SELECT n.value FROM product p JOIN number_assignment a ON a.product_id = p.id"
            " JOIN subscriber_number n ON n.id = a.subscriber_number_id"
            " WHERE p.status = 'active' AND n.resource_specification_id = 'msisdn'"
            " AND a.cancelled_at IS NULL AND a.valid_for @> %s::date ORDER BY p.id LIMIT %s",
            (args.date, max(args.records // 20, 1)),
        ).fetchall()
        clock = at(23, 30)
        kinds = ("data", "voice", "sms")
        records = [
            Incoming(
                network_record_id=f"rt-{uuid4()}",
                kind=kinds[i % 3],
                quantity=(1_000_000, 60, 1)[i % 3],
                event_at=clock.now() - timedelta(minutes=i % 600),
                msisdn=lines[i % len(lines)][0],
            )
            for i in range(args.records + 200)
        ]
        feed, single = records[: args.records], records[args.records :]

        def batches() -> None:
            for start in range(0, len(feed), BATCH):
                with conn.transaction():
                    ingest_batch(conn, feed[start : start + BATCH], now=clock.now(), tz=tz)

        def one_by_one() -> None:
            for incoming in single:
                with conn.transaction():
                    ingest(conn, incoming, now=clock.now(), tz=tz)

        report["steps"].append(measured("usage (feed batches)", len(feed), batches))
        report["steps"].append(measured("usage (one record)", len(single), one_by_one))
        report["ledger_checks"] = ledger_checks(conn, prepaid[: args.ledger], clock.now())
    finally:
        conn.rollback()  # the dataset stays as it was
        conn.close()
    for step in report["steps"]:
        print(f"\n{step['step']}: {step['per_unit']} statements per unit ({step['units']} units)")
        for row in step["top"][:15]:
            print(f"  {row['per_unit']:7.2f}  {row['statement']}")
    checks = report["ledger_checks"]
    print(
        f"\nledger: {checks['per_transaction']} balance_ledger_check calls per prepaid"
        f" transaction ({checks['transactions']} holds placed and released)"
    )
    out = Path("loadtest/out") / f"round-trips-{datetime.now(UTC):%Y%m%dT%H%M%S}.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"\nreport: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
