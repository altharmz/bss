"""Builders other test modules share, from tests/prepaid/test_balance_actions.py (#423)."""

from dataclasses import dataclass
from decimal import Decimal as D
from uuid import UUID, uuid4

from psycopg import sql
from psycopg.types.json import Jsonb

from app.repository import funding, prepaid_commands
from tests.builders.billing.settlement_db import make_payment
from tests.factories import NOW, account

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


def hold(conn, acc: Acc, amount) -> UUID:
    hold_id = funding.reserve(conn, acc.id, D(amount), held_at=NOW)
    return action(conn, acc, "reservation", amount, status="created", hold_id=hold_id)


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


def check(conn):
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    conn.execute("SET CONSTRAINTS ALL DEFERRED")
