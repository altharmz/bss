"""Shared builders of tests/test_worker_failures.py, used by other test modules (#423)."""

from uuid import UUID

from app.worker import process
from tests import apidata, timeline
from tests.builders.worker_process import DEC1, NOV20, add, at


def accounts_with_products(api, n: int = 3) -> list[UUID]:
    """n accounts with a 300/month product from Dec 1; their ids in billing order (by id)."""
    api.clock.set(timeline.NOW)
    offering = apidata.sellable_offering(api, value=300)
    api.clock.set(at(NOV20, 9))
    accounts = [apidata.account(api) for _ in range(n)]
    for acc in accounts:
        add(api, acc, offering, DEC1, on=at(NOV20))
    return sorted(UUID(acc["id"]) for acc in accounts)


def advisory_locks(admin) -> int:
    """Advisory locks in this database only: pg_locks is cluster-wide, and tests running
    in parallel hold their own in their own databases."""
    return admin.execute(
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
        " AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
    ).fetchone()[0]


def fail_for(monkeypatch, account_id):
    """bill_account raises a RuntimeError (a bug, not a BillingError) for one account;
    returns a function that removes the fault."""
    real = process.bill_account

    def failing(conn, acc, run, **kw):
        if acc == account_id:
            raise RuntimeError("injected bug")
        return real(conn, acc, run, **kw)

    monkeypatch.setattr(process, "bill_account", failing)
    return lambda: monkeypatch.setattr(process, "bill_account", real)
