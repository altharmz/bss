"""The worker's housekeeping (#282; docs/decisions/data-model.md "Retention"): each
retention rule deletes exactly its rows and nothing of the kept-forever tables, in batches
of at most 5000 rows per transaction, only from 04:00 to 05:30 and never while a billing
run is running; the usage_record partitions are made ahead and dropped after 13 months."""

from datetime import date, datetime, time, timedelta
from uuid import uuid4

import pytest

from app.clock import FixedClock
from app.repository import housekeeping as repo
from app.worker import db as worker_db
from app.worker import housekeeping
from app.worker.housekeeping import housekeep
from tests import factories
from tests.timeline import TZ

pytestmark = pytest.mark.capability("worker.housekeeping")

TODAY = date(2026, 11, 11)
NOW = datetime.combine(TODAY, time(4, 10), tzinfo=TZ)
RETAINED = {name for name, _ in repo.RULES}


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


def no_pause(_seconds: float) -> None:
    pass


def counts(conn) -> dict[str, int]:
    """Rows per table, partitions included, of every table not under a retention rule."""
    names = [
        r[0]
        for r in conn.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1"
        )
    ]
    return {
        name: conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]  # noqa: S608
        for name in names
        if name not in RETAINED
    }


def runs(conn, accounts) -> dict[str, object]:
    """Completed runs 91 and 90 days old, a failed run 120 days old (one failed and one ok
    row) and a run 95 days old whose account waits (blocked) for the failed one."""
    a, b = accounts
    ids = {}
    for name, days, status, kind in (
        ("old", 91, "completed", None),
        ("edge", 90, "completed", None),
        ("failed", 120, "failed", "accounts"),
        ("later", 95, "failed", "accounts"),
    ):
        ids[name] = conn.execute(
            "INSERT INTO billing_run (run_date, status, started_at, finished_at, trigger,"
            " failure_kind, error) VALUES (%s, %s, %s, %s, 'scheduled', %s, %s) RETURNING id",
            (
                TODAY - timedelta(days=days),
                status,
                NOW - timedelta(days=days),
                NOW - timedelta(days=days),
                kind,
                "1 of 2" if kind else None,
            ),
        ).fetchone()[0]
    rows = [
        ("old", a, "ok", None, None),
        ("old", b, "ok", None, None),
        ("edge", a, "ok", None, None),
        ("failed", a, "failed", "boom", None),
        ("failed", b, "ok", None, None),
        ("later", a, "blocked", None, ids["failed"]),
        ("later", b, "ok", None, None),
    ]
    for run, account, status, error, waits in rows:
        conn.execute(
            "INSERT INTO billing_run_account (run_id, billing_account_id, status, error,"
            " waits_for_run_id, finished_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (ids[run], account, status, error, waits, NOW),
        )
    return ids


def sessions(conn) -> None:
    conn.execute(
        "INSERT INTO app_user (username, role, password_hash, created_at, updated_at)"
        " VALUES ('op', 'user', 'x', %s, %s)",
        (NOW, NOW),
    )
    idle = timedelta(seconds=housekeeping.settings.session_idle_seconds + 60)
    for token, expires, seen in (
        ("expired-long-ago", NOW - timedelta(days=1, seconds=1), NOW - timedelta(days=2)),
        ("expired-recently", NOW - timedelta(hours=23), NOW - timedelta(hours=23)),
        ("idle-long-ago", NOW + timedelta(days=5), NOW - idle - timedelta(days=1, seconds=1)),
        ("idle-recently", NOW + timedelta(days=5), NOW - idle - timedelta(hours=23)),
        ("live", NOW + timedelta(days=5), NOW),
    ):
        conn.execute(
            "INSERT INTO app_session (token_hash, username, created_at, last_seen_at,"
            " expires_at) VALUES (%s, 'op', %s, %s, %s)",
            (token, seen, seen, expires),
        )


def idempotency_keys(conn) -> None:
    for key, age, tombstoned in (
        ("old", timedelta(days=30, hours=2), False),  # old at every time of the window
        ("old-tombstoned", timedelta(days=31), True),
        ("recent", timedelta(days=29), False),
    ):
        created = NOW - age
        conn.execute(
            "INSERT INTO idempotency_key (operator, method, route, key, request_hash,"
            " status_code, created_at) VALUES ('op', 'POST', '/x', %s, %s, 201, %s)",
            (key, "0" * 64, created),
        )
        if tombstoned:
            conn.execute(
                "UPDATE idempotency_key SET tombstoned_at = %s WHERE key = %s",
                (created + timedelta(hours=1), key),
            )


def events(conn) -> None:
    """Events with deliveries: the names say the event's age and its deliveries' state."""
    sub = conn.execute(
        "INSERT INTO event_subscription (api, callback, subscribed_at)"
        " VALUES ('TMF622', 'https://hub.example/cb', %s) RETURNING id",
        (NOW - timedelta(days=400),),
    ).fetchone()[0]
    for name, age, delivery in (
        ("e100-finished-31", 100, ("delivered", 31)),  # delivery goes, then the event
        ("e100-finished-29", 100, ("failed", 29)),  # delivery stays, so the event stays
        ("e100-pending", 100, ("pending", None)),  # never touched while pending
        ("e89-finished-31", 89, ("delivered", 31)),  # delivery goes, event is too young
        ("e91-none", 91, None),  # no delivery at all: goes
    ):
        event = conn.execute(
            "INSERT INTO event (api, event_type, resource, resource_id, occurred_at, payload)"
            " VALUES ('TMF622', 'ProductOrderCreateEvent', 'productOrder', %s, %s, '{}')"
            " RETURNING id",
            (name, NOW - timedelta(days=age)),
        ).fetchone()[0]
        if delivery is None:
            continue
        state, finished = delivery
        conn.execute(
            "INSERT INTO event_delivery (event_id, subscription_id, state, attempts,"
            " next_attempt_at, finished_at) VALUES (%s, %s, %s, 1, %s, %s)",
            (
                event,
                sub,
                state,
                NOW if state == "pending" else None,
                None if finished is None else NOW - timedelta(days=finished),
            ),
        )


def worker_requests(conn) -> None:
    for days_done, done in ((91, True), (89, True), (200, False)):
        at = NOW - timedelta(days=days_done)
        conn.execute(
            "INSERT INTO worker_request (kind, up_to, requested_at, picked_at, done_at)"
            " VALUES ('catchUp', %s, %s, %s, %s)",
            (TODAY, at, at if done else None, at if done else None),
        )


def notifications(conn) -> None:
    """Messages 13 months + 1 day old (one of them the original of a recent resend) and
    one 13 months - 1 day old."""
    thirteen = NOW.replace(year=NOW.year - 1, month=NOW.month - 1)
    ids = {}
    for name, created, resend_of in (
        ("old", thirteen - timedelta(days=1), None),
        ("old-original", thirteen - timedelta(days=1), None),
        ("young", thirteen + timedelta(days=1), None),
        ("resend", NOW - timedelta(days=3), "old-original"),
    ):
        ids[name] = uuid4()
        conn.execute(
            "INSERT INTO notification_message (id, event_type, category, channel, template,"
            " state, state_reason, content, created_at, resend_of, description)"
            " VALUES (%s, 'operator', 'transactional', 'sms', 'operator', 'cancelled',"
            " 'operator', 'x', %s, %s, %s)",
            (ids[name], created, ids.get(resend_of), name),
        )


def qualifications(conn) -> None:
    """TMF679 answers created 31 and 29 days ago (#429)."""
    for name, days in (("q31", 31), ("q29", 29)):
        created = NOW - timedelta(days=days)
        conn.execute(
            "INSERT INTO product_offering_qualification (id, kind, billing_account_id, state,"
            " qualification_result, qualification_date, created_at, expires_at, updated_at,"
            " items, details) VALUES (%s, 'check', %s, 'done', 'qualified', %s, %s, %s, %s,"
            " '[]', jsonb_build_object('description', %s::text))",
            (uuid4(), uuid4(), created.date(), created, created + timedelta(hours=1), created,
             name),
        )  # fmt: skip


def test_each_rule_deletes_exactly_its_rows_and_nothing_kept_forever(db_conn, worker):
    world = factories.world(db_conn)
    db_conn.execute(  # the factory's run (which wrote the old charge below) has finished
        "UPDATE billing_run SET status = 'completed', finished_at = started_at WHERE id = %s",
        (world.run["id"],),
    )
    second = factories.account(db_conn, name="Petrov")
    factories.charge(db_conn, world, factories.days(date(2024, 1, 1), date(2024, 2, 1)))
    run_ids = runs(db_conn, (world.account["id"], second["id"]))
    sessions(db_conn)
    idempotency_keys(db_conn)
    events(db_conn)
    worker_requests(db_conn)
    notifications(db_conn)
    qualifications(db_conn)
    db_conn.commit()
    kept = counts(db_conn)

    result = housekeep(worker, clock=FixedClock(NOW), tz=TZ, pause=no_pause)

    assert result.skipped is None and result.finished
    assert counts(db_conn) == kept  # nothing of the kept-forever list (nor any other table)
    outcomes = db_conn.execute(
        "SELECT run_id, status::text FROM billing_run_account ORDER BY run_id, status"
    ).fetchall()
    by_run = {name: sorted(s for r, s in outcomes if r == run) for name, run in run_ids.items()}
    assert by_run == {
        "old": [],  # its ok rows are gone; the counts stay on billing_run
        "edge": ["ok"],  # exactly 90 days: not older than 90 days
        "failed": ["failed", "ok"],  # not completed
        "later": ["blocked", "ok"],
    }
    assert [r[0] for r in db_conn.execute("SELECT token_hash FROM app_session ORDER BY 1")] == [
        "expired-recently",
        "idle-recently",
        "live",
    ]
    assert [r[0] for r in db_conn.execute("SELECT key FROM idempotency_key")] == ["recent"]
    assert sorted(r[0] for r in db_conn.execute("SELECT resource_id FROM event")) == [
        "e100-finished-29",
        "e100-pending",
        "e89-finished-31",
    ]
    assert sorted(
        r[0]
        for r in db_conn.execute(
            "SELECT e.resource_id FROM event_delivery d JOIN event e ON e.id = d.event_id"
        )
    ) == ["e100-finished-29", "e100-pending"]
    assert db_conn.execute(
        "SELECT count(*) FILTER (WHERE done_at IS NULL), count(*) FROM worker_request"
    ).fetchone() == (1, 2)
    assert sorted(
        r[0] for r in db_conn.execute("SELECT description FROM notification_message")
    ) == [
        "old-original",
        "resend",
        "young",
    ]
    assert result.deleted == {
        "billing_run_account": 2,
        "app_session": 2,
        "idempotency_key": 2,
        "event_delivery": 2,
        "event": 2,
        "worker_request": 1,
        "notification_message": 1,
        "product_offering_qualification": 1,
    }
    assert [
        r[0]
        for r in db_conn.execute(
            "SELECT details->>'description' FROM product_offering_qualification"
        )
    ] == ["q29"]
    # a second pass finds nothing more
    again = housekeep(worker, clock=FixedClock(NOW), tz=TZ, pause=no_pause)
    assert again.finished and again.deleted == {}


def test_the_api_still_cannot_delete_an_idempotency_key(db_conn):
    idempotency_keys(db_conn)
    with pytest.raises(Exception, match="an idempotency key is retained"):
        db_conn.execute("DELETE FROM idempotency_key WHERE key = 'old'")
    db_conn.rollback()


def test_no_transaction_deletes_more_than_5000_rows(db_conn, worker):
    """12,001 expired keys: three batches (5000, 5000, 2001), each its own transaction,
    100 ms apart. A trigger notes the transaction of every deleted row."""
    db_conn.execute(
        "INSERT INTO idempotency_key (operator, method, route, key, request_hash, status_code,"
        " created_at) SELECT 'op', 'POST', '/x', 'k' || n, %s, 201, %s"
        " FROM generate_series(1, 12001) n",
        ("0" * 64, NOW - timedelta(days=40)),
    )
    db_conn.execute(
        """
        CREATE TABLE deleted_in (xid bigint NOT NULL);
        CREATE FUNCTION note_xid() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN INSERT INTO deleted_in VALUES (txid_current()); RETURN OLD; END $$;
        CREATE TRIGGER note_xid AFTER DELETE ON idempotency_key
            FOR EACH ROW EXECUTE FUNCTION note_xid();
        """
    )
    db_conn.commit()
    pauses = []

    result = housekeep(worker, clock=FixedClock(NOW), tz=TZ, pause=pauses.append)

    assert result.batches == [("idempotency_key", 5000)] * 2 + [("idempotency_key", 2001)]
    per_transaction = [
        r[0]
        for r in db_conn.execute("SELECT count(*) FROM deleted_in GROUP BY xid ORDER BY 1 DESC")
    ]
    assert per_transaction == [5000, 5000, 2001]
    assert pauses == [0.1, 0.1]  # between batches, not after the last
    assert db_conn.execute("SELECT count(*) FROM idempotency_key").fetchone() == (0,)


def test_nothing_happens_while_a_billing_run_is_running(db_conn, worker):
    idempotency_keys(db_conn)
    factories.run(db_conn, TODAY)  # a running run
    db_conn.commit()

    result = housekeep(worker, clock=FixedClock(NOW), tz=TZ, pause=no_pause)

    assert result.skipped == "billing run running" and result.created == []
    assert db_conn.execute("SELECT count(*) FROM idempotency_key").fetchone() == (3,)


@pytest.mark.parametrize(
    ("at", "due"),
    [(time(3, 59, 59), False), (time(4, 0), True), (time(5, 29, 59), True), (time(5, 30), False)],
)
def test_it_runs_from_0400_to_0530_only(db_conn, worker, at, due):
    idempotency_keys(db_conn)
    db_conn.commit()
    clock = FixedClock(datetime.combine(TODAY, at, tzinfo=TZ))

    result = housekeep(worker, clock=clock, tz=TZ, pause=no_pause)

    assert (result.skipped is None) == due
    left = db_conn.execute("SELECT count(*) FROM idempotency_key").fetchone()[0]
    assert left == (1 if due else 3)


class Ticking:
    """A clock that moves `step` forward on every reading."""

    def __init__(self, at: datetime, step: timedelta) -> None:
        self.at, self.step = at, step

    def now(self) -> datetime:
        self.at += self.step
        return self.at

    def today(self) -> date:
        return self.at.astimezone(TZ).date()


def test_it_stops_at_0530_and_carries_on_the_next_day(db_conn, worker, monkeypatch):
    monkeypatch.setattr(housekeeping, "BATCH", 2)
    db_conn.execute(
        "INSERT INTO idempotency_key (operator, method, route, key, request_hash, status_code,"
        " created_at) SELECT 'op', 'POST', '/x', 'k' || n, %s, 201, %s"
        " FROM generate_series(1, 20) n",
        ("0" * 64, NOW - timedelta(days=40)),
    )
    db_conn.commit()
    late = Ticking(datetime.combine(TODAY, time(5, 29, 50), tzinfo=TZ), timedelta(seconds=1))

    first = housekeep(worker, clock=late, tz=TZ, pause=no_pause)

    assert not first.finished and 0 < first.deleted["idempotency_key"] < 20
    left = db_conn.execute("SELECT count(*) FROM idempotency_key").fetchone()[0]
    assert left == 20 - first.deleted["idempotency_key"]
    nothing = housekeep(worker, clock=late, tz=TZ, pause=no_pause)  # 05:30 passed
    assert nothing.skipped == "not due"
    next_day = housekeep(worker, clock=FixedClock(NOW + timedelta(days=1)), tz=TZ, pause=no_pause)
    assert next_day.finished and next_day.deleted == {"idempotency_key": left}


def test_one_wake_up_gives_it_at_most_its_budget(db_conn, worker, monkeypatch):
    monkeypatch.setattr(housekeeping, "BATCH", 2)
    idempotency_keys(db_conn)
    db_conn.execute(
        "INSERT INTO idempotency_key (operator, method, route, key, request_hash, status_code,"
        " created_at) SELECT 'op', 'POST', '/x', 'k' || n, %s, 201, %s"
        " FROM generate_series(1, 20) n",
        ("0" * 64, NOW - timedelta(days=40)),
    )
    db_conn.commit()
    clock = Ticking(NOW, timedelta(seconds=10))

    result = housekeep(worker, clock=clock, tz=TZ, pause=no_pause, budget=timedelta(seconds=45))

    assert not result.finished and len(result.batches) < 11


def partition_names(conn) -> list[str]:
    return sorted(repo.partitions(conn))


def test_partitions_for_this_and_the_next_two_months_are_made_once(db_conn, worker):
    """On 2031-05-11 the partitions of May, June and July 2031 are made, once. A record
    the DEFAULT partition took for June moves into June's partition."""
    db_conn.execute(
        "INSERT INTO usage_record (id, network_record_id, kind, quantity, msisdn, event_at,"
        " event_date, status, rejection_reason, created_at)"
        " VALUES (%s, 'net-1', 'sms', 1, '79990000001', %s, '2031-06-15', 'rejected',"
        " 'noLine', %s)",
        (uuid4(), datetime(2031, 6, 15, 12, tzinfo=TZ), NOW),
    )
    db_conn.commit()
    assert repo.default_rows(db_conn) == 1
    db_conn.commit()  # an open read would hold the DEFAULT partition the move needs
    clock = FixedClock(datetime(2031, 5, 11, 4, 10, tzinfo=TZ))

    first = housekeep(worker, clock=clock, tz=TZ, pause=no_pause)
    second = housekeep(worker, clock=clock, tz=TZ, pause=no_pause)

    assert first.created == [date(2031, 5, 1), date(2031, 6, 1), date(2031, 7, 1)]
    assert second.created == [] and second.dropped == []
    names = partition_names(db_conn)
    assert {"usage_record_y2031m05", "usage_record_y2031m06", "usage_record_y2031m07"} <= set(names)
    assert repo.default_rows(db_conn) == 0
    assert db_conn.execute("SELECT count(*) FROM usage_record_y2031m06").fetchone() == (1,)
    options = db_conn.execute(
        "SELECT reloptions FROM pg_class WHERE relname = 'usage_record_y2031m06'"
    ).fetchone()[0]
    assert set(options) == {
        "autovacuum_vacuum_scale_factor=0.02",
        "autovacuum_analyze_scale_factor=0.01",
    }
    # the partition carries the table's keys: a resend of the same date is refused
    from psycopg.errors import UniqueViolation

    with pytest.raises(UniqueViolation):
        db_conn.execute(
            "INSERT INTO usage_record (id, network_record_id, kind, quantity, msisdn, event_at,"
            " event_date, status, rejection_reason, created_at)"
            " VALUES (%s, 'net-1', 'sms', 1, '79990000001', %s, '2031-06-15', 'rejected',"
            " 'noLine', %s)",
            (uuid4(), datetime(2031, 6, 15, 12, tzinfo=TZ), NOW),
        )
    db_conn.rollback()


def test_partitions_older_than_13_months_are_dropped(db_conn, worker):
    """On 2026-11-11 September 2025 (14 months back) goes, October 2025 (13) stays."""
    for month in (date(2025, 9, 1), date(2025, 10, 1)):
        repo.create_partition(db_conn, month)
    db_conn.commit()

    result = housekeep(worker, clock=FixedClock(NOW), tz=TZ, pause=no_pause)

    assert result.dropped == ["usage_record_y2025m09"]
    names = partition_names(db_conn)
    assert "usage_record_y2025m09" not in names and "usage_record_y2025m10" in names


def test_each_batch_gets_one_heartbeat(db_conn, worker):
    """#511: the liveness file is touched after every batch, deleting or not."""
    beats = []
    result = housekeep(
        worker, clock=FixedClock(NOW), tz=TZ, pause=no_pause, heartbeat=lambda: beats.append(1)
    )
    assert result.finished and len(beats) == len(repo.RULES)  # one short batch per rule


def test_a_busy_usage_record_leaves_the_partitions_for_the_next_wake_up(
    db_conn, worker, monkeypatch
):
    """#511: a lock not granted in time is no failure: the retention rules still run and the
    next wake-up makes the partitions."""
    monkeypatch.setattr(housekeeping, "DDL_LOCK_TIMEOUT", "100ms")
    clock = FixedClock(datetime(2033, 2, 11, 4, 10, tzinfo=TZ))
    db_conn.execute("LOCK TABLE usage_record IN ACCESS EXCLUSIVE MODE")  # held until commit
    try:
        busy = housekeep(worker, clock=clock, tz=TZ, pause=no_pause)
    finally:
        db_conn.rollback()
    assert busy.created == [] and busy.dropped == [] and busy.finished
    assert "usage_record_y2033m02" not in partition_names(db_conn)
    free = housekeep(worker, clock=clock, tz=TZ, pause=no_pause)
    assert free.created == [date(2033, 2, 1), date(2033, 3, 1), date(2033, 4, 1)]
