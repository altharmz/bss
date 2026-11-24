"""The daily retry of unfunded renewals (#340): set-based for those due only by the date,
credit signals of every kind, and a time budget for the per-account attempts."""

from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4

from app.repository import balance_actions, funding
from app.repository import prepaid_commands as commands
from tests.builders.billing.settlement_db import make_payment
from tests.builders.prepaid.prepaid_dispatch import dispatch
from tests.builders.prepaid.prepaid_posting import TZ, prepared
from tests.builders.prepaid.prepaid_renewal import renewal
from tests.factories import account, prepaid_bucket
from tests.perf.guard import counting


def short_renewals(conn, count):
    """`count` renewals on accounts of their own, each 24.00 due with 18.00 spendable, and
    their first shortfall observed at the returned instant."""
    made = [renewal(conn, amount="20") for _ in range(count)]
    conn.commit()
    now = made[0][3]
    assert set(dispatch(conn, now)) == {cmd["id"] for _, cmd, _, _ in made}
    return made, now


def observations(conn, command_id):
    return conn.execute(
        "SELECT observed_at, required, available FROM prepaid_funding_shortfall"
        " WHERE command_id = %s ORDER BY observed_at",
        (command_id,),
    ).fetchall()


def scan(conn, now):
    return {
        row["id"]: row["signalled"]
        for row in commands.candidates(conn, now=now, today=now.astimezone(TZ).date(), tz=TZ.key)
    }


def test_a_renewal_still_short_gets_one_observation_per_date_in_constant_statements(
    db_conn, monkeypatch
):
    made, now = short_renewals(db_conn, 3)
    ids = [cmd["id"] for _, cmd, _, _ in made]
    next_day = now + timedelta(days=1)
    with counting(monkeypatch) as daily:
        # each gets the day's decision; nothing is loaded, locked or posted
        assert sorted(dispatch(db_conn, next_day)) == sorted(ids)
    for command_id in ids:
        assert observations(db_conn, command_id) == [
            (now, D("24.00"), D("18.00")),
            (next_day, D("24.00"), D("18.00")),
        ]
        assert commands.get(db_conn, command_id)["attempts"] == 0
    assert not [s for s in daily if "FOR UPDATE" in s or "pg_advisory_xact_lock" in s]
    with counting(monkeypatch) as idle:  # the same date again: nothing is due
        assert dispatch(db_conn, next_day + timedelta(hours=1)) == []
    assert all(len(observations(db_conn, command_id)) == 2 for command_id in ids)
    # the balances and the insert: two statements for any number of renewals
    assert len(daily) == len(idle) + 2
    dispatch(db_conn, now + timedelta(days=2))
    assert all(len(observations(db_conn, command_id)) == 3 for command_id in ids)


def test_a_renewal_the_balance_now_covers_is_posted_on_that_date(db_conn):
    (made,), now = short_renewals(db_conn, 1)
    acc, cmd, _, _ = made
    make_payment(db_conn, acc, "20")  # received before the shortfall: not a credit signal
    db_conn.commit()
    assert dispatch(db_conn, now + timedelta(hours=1)) == []  # the same date: not due
    assert scan(db_conn, now + timedelta(days=1)) == {cmd["id"]: False}
    assert dispatch(db_conn, now + timedelta(days=1)) == [cmd["id"]]
    assert commands.get(db_conn, cmd["id"])["state"] == "completed"
    assert len(observations(db_conn, cmd["id"])) == 1


def test_an_adjustment_credit_is_a_credit_signal_on_the_same_date(db_conn):
    (made,), now = short_renewals(db_conn, 1)
    acc, cmd, _, _ = made
    bucket = dict(prepaid_bucket(db_conn, acc["id"], now), billing_account_id=acc["id"])
    balance_actions.insert_adjustment(
        db_conn,
        dict(bucket, currency="USD"),
        request={},
        amount=D("10"),
        operator="test",
        now=now + timedelta(minutes=1),
        reason=None,
        description=None,
    )
    db_conn.commit()
    assert scan(db_conn, now + timedelta(minutes=2)) == {cmd["id"]: True}
    assert dispatch(db_conn, now + timedelta(minutes=2)) == [cmd["id"]]
    assert commands.get(db_conn, cmd["id"])["state"] == "completed"


def test_an_incoming_transfer_is_a_credit_signal_on_the_same_date(db_conn):
    (made,), now = short_renewals(db_conn, 1)
    acc, cmd, _, _ = made
    sender = account(db_conn, name="Sender")
    give = dict(prepaid_bucket(db_conn, sender["id"], now), billing_account_id=sender["id"])
    take = dict(prepaid_bucket(db_conn, acc["id"], now), billing_account_id=acc["id"])
    make_payment(db_conn, sender, "10")
    accepted = now + timedelta(minutes=1)
    transfer = commands.enqueue(
        db_conn,
        uuid4(),
        sender["id"],
        kind="transfer",
        due_date=accepted.date(),
        request={},
        now=accepted,
    )
    hold = funding.reserve_for_command(
        db_conn, sender["id"], D("10"), command_id=transfer["id"], held_at=accepted
    )
    balance_actions.insert_transfer(
        db_conn,
        dict(give, currency="USD"),
        dict(take, currency="USD"),
        action_id=uuid4(),
        request={},
        amount=D("10"),
        cost=D("0"),
        cost_owner="originator",
        operator="test",
        now=accepted,
        reason=None,
        description=None,
        hold_id=hold,
        command_id=transfer["id"],
    )
    db_conn.commit()
    later = now + timedelta(minutes=2)
    assert dispatch(db_conn, later) == [transfer["id"]]  # completed after the renewals
    assert commands.get(db_conn, cmd["id"])["state"] == "pending"
    assert scan(db_conn, later) == {cmd["id"]: True}  # the received 10.00 is a credit
    assert dispatch(db_conn, later) == [cmd["id"]]
    assert commands.get(db_conn, cmd["id"])["state"] == "completed"


def test_with_no_budget_one_account_is_attempted_per_wake_up_and_none_is_skipped(db_conn):
    firsts = sorted(prepared(db_conn)[2]["id"] for _ in range(3))
    db_conn.commit()
    done = []
    for _ in range(3):
        attempted = dispatch(db_conn, budget=timedelta(0))
        assert len(attempted) == 1
        done += attempted
    assert sorted(done) == firsts
    assert dispatch(db_conn, budget=timedelta(0)) == []
    assert all(commands.get(db_conn, i)["state"] == "completed" for i in firsts)


def test_a_signalled_account_is_attempted_before_one_due_only_by_the_date(db_conn):
    (made,), now = short_renewals(db_conn, 1)
    acc, dated, _, _ = made
    make_payment(db_conn, acc, "20")  # received before the shortfall: due only by the date
    _, fresh, _, _ = renewal(db_conn, amount="1")  # never attempted: signalled
    db_conn.commit()
    next_day = now + timedelta(days=1)
    assert dispatch(db_conn, next_day, budget=timedelta(0)) == [fresh["id"]]
    assert dispatch(db_conn, next_day, budget=timedelta(0)) == [dated["id"]]
    assert {commands.get(db_conn, c["id"])["state"] for c in (fresh, dated)} == {"completed"}
