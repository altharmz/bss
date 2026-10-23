"""P4a: balance actions, reversals and adjustment/transfer/reversal funding sources.

The writers below are the minimal ledger shapes later API slices produce; the property
test checks the funding-proofs equation against an independent oracle:
L = payments + signed adjustments (with reversals) + transfers in - transfers out
    - tax-inclusive ABRs - payouts; reservedValue = H; remainingValue = L - H.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal as D
from uuid import UUID, uuid4

import psycopg
import pytest
from alembic.config import Config
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from psycopg import errors, sql
from psycopg.types.json import Jsonb

from alembic import command
from app.billing.funding import InsufficientFunds
from app.repository import billing, funding, prepaid_commands
from app.repository._sql import count_references
from app.repository.parties import ACCOUNT_HISTORY
from tests.factories import NOW, World, account, charge, days, offering, product, rejected, run
from tests.test_funding_db import billed_charge
from tests.test_settlement_db import make_payment

OP = "operator-1"
COLUMN = {
    "payment": "payment_id",
    "creditNote": "credit_bill_id",
    "adjustment": "funding_source_id",
    "transfer": "funding_source_id",
    "reversal": "funding_source_id",
}


@dataclass
class Acc:
    row: dict
    bucket: UUID

    @property
    def id(self) -> UUID:
        return self.row["id"]


def prepaid(conn, name="Ivanov") -> Acc:
    acc = account(conn, name=name)
    conn.execute("UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (acc["id"],))
    bucket = conn.execute(
        "INSERT INTO prepaid_bucket (billing_account_id, currency, created_at)"
        " VALUES (%s, 'USD', %s) RETURNING id",
        (acc["id"], NOW),
    ).fetchone()[0]
    return Acc(acc, bucket)


def action(conn, acc: Acc, kind, amount, status="completed", **extra) -> UUID:
    row = {
        "kind": kind,
        "status": status,
        "billing_account_id": acc.id,
        "currency": "USD",
        "bucket_id": acc.bucket,
        "request": Jsonb({"@type": kind, "amount": {"amount": str(amount), "units": "USD"}}),
        "amount": D(amount),
        "operator": OP,
        "requested_at": NOW,
        "confirmed_at": NOW if status in ("completed", "failed") else None,
        **extra,
    }
    return conn.execute(
        sql.SQL("INSERT INTO balance_action ({}) VALUES ({}) RETURNING id").format(
            sql.SQL(", ").join(map(sql.Identifier, row)),
            sql.SQL(", ").join([sql.Placeholder()] * len(row)),
        ),
        tuple(row.values()),
    ).fetchone()[0]


def consume(conn, acc: Acc, action_id, sources, *, reversal=False):
    for source in sources:
        conn.execute(
            sql.SQL(
                "INSERT INTO funding_consumption (billing_account_id, action_id,"
                " reversal_action_id, {}, amount, consumed_at) VALUES (%s, %s, %s, %s, %s, %s)"
            ).format(sql.Identifier(COLUMN[source.kind])),
            (acc.id, action_id, action_id if reversal else None, source.id, source.amount, NOW),
        )


def source(conn, acc: Acc, kind, action_id, amount, consumption_id=None) -> UUID:
    return conn.execute(
        "INSERT INTO funding_source"
        " (kind, billing_account_id, currency, action_id, consumption_id, amount, available_at)"
        " VALUES (%s, %s, 'USD', %s, %s, %s, %s) RETURNING id",
        (kind, acc.id, action_id, consumption_id, D(amount), NOW),
    ).fetchone()[0]


def topup(conn, acc: Acc, amount) -> UUID:
    payment = make_payment(conn, acc.row, str(amount))
    return action(conn, acc, "topup", amount, payment_id=payment["id"])


def credit(conn, acc: Acc, amount) -> UUID:
    action_id = action(conn, acc, "adjustment", amount)
    source(conn, acc, "adjustment", action_id, amount)
    return action_id


def debit(conn, acc: Acc, amount) -> UUID:
    plan = funding.plan(conn, acc.id, D(amount))  # InsufficientFunds: nothing written
    action_id = action(conn, acc, "adjustment", -D(amount))
    consume(conn, acc, action_id, plan.sources)
    return action_id


def payout(conn, acc: Acc, amount) -> None:
    plan = funding.plan(conn, acc.id, D(amount))
    refund_id = conn.execute(
        "INSERT INTO refund (billing_account_id, currency, amount, refunded_at, method_name)"
        " VALUES (%s, 'USD', %s, %s, 'cash') RETURNING id",
        (acc.id, D(amount), NOW),
    ).fetchone()[0]
    for s in plan.sources:
        conn.execute(
            sql.SQL(
                "INSERT INTO refund_source (refund_id, billing_account_id, {}, amount)"
                " VALUES (%s, %s, %s, %s)"
            ).format(sql.Identifier(COLUMN[s.kind])),
            (refund_id, acc.id, s.id, s.amount),
        )


def hold(conn, acc: Acc, amount) -> UUID:
    hold_id = funding.reserve(conn, acc.id, D(amount), held_at=NOW)
    return action(conn, acc, "reservation", amount, status="created", hold_id=hold_id)


def release(conn, acc: Acc, action_id) -> None:
    hold_id = conn.execute(
        "UPDATE balance_action SET status = 'cancelled', cancelled_at = %s WHERE id = %s"
        " RETURNING hold_id",
        (NOW, action_id),
    ).fetchone()[0]
    funding.release(conn, acc.id, hold_id, released_at=NOW)


def transfer(conn, sender: Acc, receiver: Acc, amount) -> UUID:
    """Accept (hold principal, queue its command), then complete like the future worker."""
    command_id = uuid4()
    prepaid_commands.enqueue(
        conn, command_id, sender.id, kind="transfer", due_date=NOW.date(), request={}, now=NOW
    )
    hold_id = funding.reserve_for_command(
        conn, sender.id, D(amount), command_id=command_id, held_at=NOW
    )
    action_id = action(
        conn,
        sender,
        "transfer",
        amount,
        status="created",
        transfer_cost=D(0),
        cost_owner="originator",
        receiver_bucket_id=receiver.bucket,
        receiver_billing_account_id=receiver.id,
        receiver_currency="USD",
        hold_id=hold_id,
        command_id=command_id,
    )
    funding.release(conn, sender.id, hold_id, released_at=NOW, command_id=command_id)
    for _, payment_id, credit_id, held, source_id in conn.execute(
        funding._HELD_SOURCES, (hold_id,)
    ).fetchall():
        conn.execute(
            "INSERT INTO funding_consumption (billing_account_id, action_id, payment_id,"
            " credit_bill_id, funding_source_id, amount, consumed_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (sender.id, action_id, payment_id, credit_id, source_id, held, NOW),
        )
    source(conn, receiver, "transfer", action_id, amount)
    conn.execute(
        "UPDATE balance_action SET status = 'completed', confirmed_at = %s WHERE id = %s",
        (NOW, action_id),
    )
    return action_id


def reverse(conn, acc: Acc, action_id) -> None:
    """Cancel a completed top-up or adjustment with one compensating entry."""
    kind, amount, payment_id = conn.execute(
        "SELECT kind, amount, payment_id FROM balance_action WHERE id = %s", (action_id,)
    ).fetchone()
    conn.execute(
        "INSERT INTO balance_action_reversal"
        " (action_id, billing_account_id, currency, amount, operator, reversed_at)"
        " VALUES (%s, %s, 'USD', %s, %s, %s)",
        (action_id, acc.id, -amount, OP, NOW),
    )
    if amount > 0:  # the untouched credit is consumed by its reversal
        column, source_id = ("payment_id", payment_id)
        if kind == "adjustment":
            column = "funding_source_id"
            source_id = conn.execute(
                "SELECT id FROM funding_source WHERE action_id = %s", (action_id,)
            ).fetchone()[0]
        conn.execute(
            sql.SQL(
                "INSERT INTO funding_consumption (billing_account_id, action_id,"
                " reversal_action_id, {}, amount, consumed_at) VALUES (%s, %s, %s, %s, %s, %s)"
            ).format(sql.Identifier(column)),
            (acc.id, action_id, action_id, source_id, amount, NOW),
        )
    else:  # a debit's exact consumed sources are restored once each
        for consumption_id, consumed in conn.execute(
            "SELECT id, amount FROM funding_consumption WHERE action_id = %s", (action_id,)
        ).fetchall():
            source(conn, acc, "reversal", action_id, consumed, consumption_id)
    conn.execute(
        "UPDATE balance_action SET status = 'cancelled', cancelled_at = %s WHERE id = %s",
        (NOW, action_id),
    )


def credit_source(conn, action_id):
    """The untouched remainder of a top-up's payment or a credit's source."""
    return conn.execute(
        "SELECT coalesce(a.payment_id, f.id) FROM balance_action a"
        " LEFT JOIN funding_source f ON f.action_id = a.id AND f.kind = 'adjustment'"
        " WHERE a.id = %s",
        (action_id,),
    ).fetchone()[0]


def check(conn):
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    conn.execute("SET CONSTRAINTS ALL DEFERRED")


# --- the funding-proofs equation ---------------------------------------------------------


@dataclass
class Oracle:
    ledger: D = D(0)
    held: D = D(0)
    holds: dict = field(default_factory=dict)  # action id -> amount
    credits: list = field(default_factory=list)  # reversible top-ups/credits: (id, amount)
    debits: list = field(default_factory=list)  # reversible debits: (id, amount)

    @property
    def available(self) -> D:
        return max(D(0), self.ledger - self.held)


CENTS = st.integers(1, 5000).map(lambda c: D(c) / 100)
OPS = st.lists(
    st.tuples(
        st.sampled_from(
            ["topup", "credit", "debit", "payout", "hold", "release", "transfer", "reverse"]
        ),
        st.integers(0, 1),
        CENTS,
        st.integers(0, 10),
    ),
    max_size=14,
)


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@given(ops=OPS, charged=st.lists(CENTS, max_size=2))
def test_ledger_and_bucket_values_follow_the_funding_equation(db_conn, ops, charged):
    with db_conn.transaction(force_rollback=True):
        accounts = [prepaid(db_conn, "A"), prepaid(db_conn, "B")]
        oracles = [Oracle(), Oracle()]
        off = offering(db_conn)
        w = World(off, accounts[0].row, product(db_conn, accounts[0].id, off["id"], NOW.date()),
                  run(db_conn))  # fmt: skip
        for i, amount in enumerate(charged):  # unbilled tax-inclusive charges
            start = date(2026, 1, 1) + timedelta(days=10 * i)
            charge(db_conn, w, days(start, start + timedelta(days=10)), str(amount))
            oracles[0].ledger -= amount
        for op, who, amount, pick in ops:
            acc, oracle = accounts[who], oracles[who]
            funded = amount <= oracle.available
            if op == "topup":
                oracle.credits.append((topup(db_conn, acc, amount), amount))
                oracle.ledger += amount
            elif op == "credit":
                oracle.credits.append((credit(db_conn, acc, amount), amount))
                oracle.ledger += amount
            elif op in ("debit", "payout", "hold", "transfer"):
                try:
                    if op == "debit":
                        oracle.debits.append((debit(db_conn, acc, amount), amount))
                    elif op == "payout":
                        payout(db_conn, acc, amount)
                    elif op == "hold":
                        oracle.holds[hold(db_conn, acc, amount)] = amount
                    else:
                        transfer(db_conn, acc, accounts[1 - who], amount)
                        oracles[1 - who].ledger += amount
                        oracles[1 - who].credits.clear()  # selection may now touch them
                except InsufficientFunds:
                    assert not funded
                    continue
                assert funded
                if op == "hold":
                    oracle.held += amount
                else:
                    oracle.ledger -= amount
            elif op == "release" and oracle.holds:
                action_id = list(oracle.holds)[pick % len(oracle.holds)]
                release(db_conn, acc, action_id)
                oracle.held -= oracle.holds.pop(action_id)
            elif op == "reverse" and (oracle.credits or oracle.debits):
                entries = oracle.credits + oracle.debits
                action_id, value = entries[pick % len(entries)]
                if (action_id, value) in oracle.debits:
                    reverse(db_conn, acc, action_id)
                    oracle.debits.remove((action_id, value))
                    oracle.ledger += value
                else:
                    snapshot = funding.load(db_conn, acc.id)
                    untouched = credit_source(db_conn, action_id)
                    remaining = next(s.remaining for s in snapshot.sources if s.id == untouched)
                    oracle.credits.remove((action_id, value))
                    if remaining != value or value > oracle.available:
                        continue  # spent, held or owed: the API refuses it (409)
                    reverse(db_conn, acc, action_id)
                    oracle.ledger -= value
            check(db_conn)
            for each, expected in zip(accounts, oracles, strict=True):
                snapshot = funding.load(db_conn, each.id)
                assert billing.account_balance(db_conn, each.id) == expected.ledger
                assert snapshot.ledger == expected.ledger
                assert snapshot.reserved == expected.held
                assert snapshot.spendable == expected.ledger - expected.held


# --- schema rules ------------------------------------------------------------------------


def test_reversal_is_unique_and_entries_are_retained(db_conn):
    acc = prepaid(db_conn)
    topped = topup(db_conn, acc, "10")
    debited = debit(db_conn, acc, "4")
    reverse(db_conn, acc, debited)
    check(db_conn)
    assert funding.load(db_conn, acc.id).spendable == D("10")
    with rejected(db_conn, errors.UniqueViolation):
        db_conn.execute(
            "INSERT INTO balance_action_reversal"
            " (action_id, billing_account_id, currency, amount, operator, reversed_at)"
            " VALUES (%s, %s, 'USD', 4, %s, %s)",
            (debited, acc.id, OP, NOW),
        )
    for statement in (
        "UPDATE balance_action_reversal SET amount = 5",
        "DELETE FROM balance_action_reversal",
        "UPDATE funding_consumption SET amount = 1",
        "DELETE FROM funding_source",
        "UPDATE balance_action SET amount = 3",
        "UPDATE balance_action SET request = '{}'",
        "UPDATE balance_action SET status = 'completed'",
        "UPDATE balance_action SET confirmed_at = now() WHERE id = %(topped)s",
        "DELETE FROM balance_action WHERE id = %(topped)s",
    ):
        with rejected(db_conn, errors.CheckViolation):
            db_conn.execute(statement, {"topped": topped})
    db_conn.execute("UPDATE balance_action SET reason = 'goodwill', description = 'edited'")
    failed = action(db_conn, acc, "adjustment", "5", status="failed", failure_reason="x")
    db_conn.execute("DELETE FROM balance_action WHERE id = %s", (failed,))
    check(db_conn)
    assert count_references(db_conn, acc.id, ACCOUNT_HISTORY)["balanceAction"] == 2


@pytest.mark.parametrize(
    "kind,amount,extra",
    [
        ("adjustment", "0", {}),
        ("topup", "5", {}),  # completed without its payment
        ("topup", "-5", {"status": "failed", "failure_reason": "x"}),
        ("reservation", "-1", {"status": "failed", "failure_reason": "x"}),
        ("reservation", "1", {"status": "created"}),  # no hold
        ("adjustment", "5", {"status": "created"}),  # synchronous
        ("adjustment", "5", {"status": "failed"}),  # no failure reason
        ("transfer", "5", {"status": "failed", "failure_reason": "x"}),  # no fee owner
        (
            "transfer",
            "5",
            {
                "status": "failed",
                "failure_reason": "x",
                "transfer_cost": D(6),
                "cost_owner": "receiver",
            },
        ),  # fmt: skip
    ],
)
def test_kind_and_status_fields_are_checked(db_conn, kind, amount, extra):
    acc = prepaid(db_conn)
    with rejected(db_conn, errors.CheckViolation):
        action(db_conn, acc, kind, amount, **extra)


def test_accepted_transfer_needs_another_bucket_and_its_command(db_conn):
    acc, other = prepaid(db_conn), prepaid(db_conn, "B")
    common = {"transfer_cost": D(0), "cost_owner": "originator", "receiver_currency": "USD"}
    with rejected(db_conn, errors.CheckViolation):
        action(db_conn, acc, "transfer", "5", status="created", receiver_bucket_id=acc.bucket,
               receiver_billing_account_id=acc.id, **common)  # fmt: skip
    # A business refusal is stored even for the same bucket.
    action(db_conn, acc, "transfer", "5", status="failed", failure_reason="sameBucket",
           receiver_bucket_id=acc.bucket, receiver_billing_account_id=acc.id, **common)  # fmt: skip
    make_payment(db_conn, acc.row, "9")
    unowned = funding.reserve(db_conn, acc.id, D(5), held_at=NOW)
    with rejected(db_conn, errors.CheckViolation):
        action(db_conn, acc, "transfer", "5", status="created", hold_id=unowned,
               receiver_bucket_id=other.bucket, receiver_billing_account_id=other.id,
               **common)  # fmt: skip


@pytest.mark.parametrize(
    "corrupt,message",
    [
        ("consume_more_than_source", "combined bounds"),
        ("credit_without_source", "ledger entries"),
        ("debit_partly_consumed", "ledger entries"),
        ("hold_and_debit_same_source", "combined bounds"),
        ("transfer_without_received_source", "ledger entries"),
        ("reservation_released_while_created", "ledger entries"),
        ("topup_payment_mismatch", "ledger entries"),
        ("operator_hold_owned_by_command", "ledger entries"),
    ],
)
def test_deferred_bounds_refuse_inconsistent_ledgers(db_conn, corrupt, message):
    acc, other = prepaid(db_conn), prepaid(db_conn, "B")
    db_conn.commit()
    with pytest.raises(errors.CheckViolation, match=message), db_conn.transaction():
        if corrupt == "consume_more_than_source":
            credited = credit(db_conn, acc, "5")
            debited = action(db_conn, acc, "adjustment", "-6")
            source_id = credit_source(db_conn, credited)
            db_conn.execute(
                "INSERT INTO funding_consumption"
                " (billing_account_id, action_id, funding_source_id, amount, consumed_at)"
                " VALUES (%s, %s, %s, 6, %s)",
                (acc.id, debited, source_id, NOW),
            )
        elif corrupt == "credit_without_source":
            action(db_conn, acc, "adjustment", "5")
        elif corrupt == "debit_partly_consumed":
            credit(db_conn, acc, "5")
            plan = funding.plan(db_conn, acc.id, D(3))
            consume(db_conn, acc, action(db_conn, acc, "adjustment", "-4"), plan.sources)
        elif corrupt == "hold_and_debit_same_source":
            credit(db_conn, acc, "5")
            plan = funding.plan(db_conn, acc.id, D(4))
            funding.reserve(db_conn, acc.id, D(4), held_at=NOW)
            consume(db_conn, acc, action(db_conn, acc, "adjustment", "-4"), plan.sources)
        elif corrupt == "transfer_without_received_source":
            topup(db_conn, acc, "5")
            transfer(db_conn, acc, other, "5")
            db_conn.execute("SET session_replication_role = replica")  # bypass retention
            db_conn.execute("DELETE FROM funding_source WHERE kind = 'transfer'")
            db_conn.execute("SET session_replication_role = origin")
            db_conn.execute("UPDATE balance_action SET description = 'recheck'")
        elif corrupt == "reservation_released_while_created":
            topup(db_conn, acc, "5")
            reserved = hold(db_conn, acc, "5")
            hold_id = db_conn.execute(
                "SELECT hold_id FROM balance_action WHERE id = %s", (reserved,)
            ).fetchone()[0]
            funding.release(db_conn, acc.id, hold_id, released_at=NOW)
        elif corrupt == "topup_payment_mismatch":
            payment = make_payment(db_conn, acc.row, "7")
            action(db_conn, acc, "topup", "5", payment_id=payment["id"])
        else:
            make_payment(db_conn, acc.row, "5")
            command_id = uuid4()
            hold_id = funding.reserve_for_command(
                db_conn, acc.id, D(5), command_id=command_id, held_at=NOW
            )
            action(db_conn, acc, "reservation", "5", status="created", hold_id=hold_id)
        check(db_conn)


def test_hold_on_new_source_kinds_converts_to_exact_allocations(db_conn):
    acc = prepaid(db_conn)
    off = offering(db_conn)
    w = World(off, acc.row, product(db_conn, acc.id, off["id"], date(2026, 1, 1)), run(db_conn))
    credit(db_conn, acc, "4")
    debited = debit(db_conn, acc, "4")  # consumes the credit
    reverse(db_conn, acc, debited)  # restores it as a reversal source
    credit(db_conn, acc, "3")
    command_id = uuid4()
    funding.reserve_for_command(db_conn, acc.id, D(6), command_id=command_id, held_at=NOW)
    held = {s.kind: s.held for s in funding.load(db_conn, acc.id).sources}
    assert held == {"adjustment": D(3), "reversal": D(3)}
    bill = billed_charge(db_conn, w, date(2026, 1, 1), "6")
    funding.convert_for_command(
        db_conn, acc.id, bill["id"], command_id=command_id, converted_at=NOW
    )
    check(db_conn)
    kinds = db_conn.execute(
        "SELECT f.kind, a.amount FROM bill_allocation a JOIN funding_source f"
        " ON f.id = a.funding_source_id ORDER BY f.kind"
    ).fetchall()
    assert kinds == [("adjustment", D(3)), ("reversal", D(3))]
    snapshot = funding.load(db_conn, acc.id)
    assert (snapshot.ledger, snapshot.reserved, snapshot.spendable) == (D(1), D(0), D(1))


def test_migration_keeps_holds_and_refuses_losing_actions(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0044")
    with psycopg.connect(temp_db_url) as conn:
        acc = prepaid(conn)
        make_payment(conn, acc.row, "20")
        funding.reserve(conn, acc.id, D(5), held_at=NOW)
        assert funding.load(conn, acc.id).spendable == D(15)
    command.upgrade(cfg, "0045")
    command.downgrade(cfg, "0044")
    command.upgrade(cfg, "0045")
    with psycopg.connect(temp_db_url) as conn:
        assert funding.load(conn, acc.id).spendable == D(15)
        credit(conn, acc, "2")
        snapshot = funding.load(conn, acc.id)
        assert (snapshot.ledger, snapshot.reserved, snapshot.spendable) == (D(22), D(5), D(17))
    with pytest.raises(Exception, match="balance action and funding source history"):
        command.downgrade(cfg, "0044")
