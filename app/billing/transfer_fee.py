"""A transfer's fee, posted by the billing owner in the transfer's completion.

`transferCost` is one tax-inclusive Money and no rate belongs to it (it is no product's
price), so the fee is posted as it is: one `oneTime` rate of that amount with no tax
lines and no product, over the one day it is posted, and one settled `offCycle` bill.
The payer is the sender (the rest of the transfer's hold pays it) or the receiver (the
received `transfer` source pays it); either way every cent of the bill is allocated from
exactly the sources the caller names, in the caller's transaction and savepoint.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import psycopg
from psycopg.types.range import Range

from app.billing import settlement
from app.billing.settlement import StateChange
from app.events import emit
from app.repository import billing, funding, presentation
from app.repository import prepaid_commands as commands
from app.worker.db import holds_ownership

# (payment_id, credit_bill_id, funding_source_id, amount) - exactly one source id is set
Part = tuple[UUID | None, UUID | None, UUID | None, Decimal]


def post_fee(
    conn: psycopg.Connection,
    *,
    command_id: UUID,
    action_id: UUID,
    payer_id: UUID,
    currency: str,
    fee: Decimal,
    parts: list[Part],
    now: datetime,
    today: date,
) -> dict:
    """Charge `fee` to `payer_id` for the transfer `action_id` and settle it from `parts`."""
    if not holds_ownership(conn):
        raise ValueError("fee posting requires billing ownership")
    if fee <= 0 or sum((p[3] for p in parts), Decimal(0)) != fee:
        raise ValueError("the fee's sources must cover it exactly")
    run = commands.ensure_run(conn, command_id, run_date=today, now=now)
    if run["status"] != "running":
        raise ValueError("command run is not running")
    day = Range(today, today + timedelta(days=1), "[)")
    abr = billing.insert_abr(
        conn,
        product_id=None,
        billing_account_id=payer_id,
        charge_type="oneTime",
        amount=fee,
        currency=currency,
        period=day,
        billing_run_id=run["id"],
        applied_at=now,
        balance_action_id=action_id,
    )
    bill = billing.insert_bill(
        conn,
        billing_account_id=payer_id,
        currency=currency,
        bill_period=day,
        bill_date=today,
        amount_due=fee,
        billing_run_id=run["id"],
        payment_due_date=today,
        run_type="offCycle",
    )
    billing.record_bill_issue(conn, bill["id"], now=now)
    billing.attach_abrs_to_bill(conn, bill["id"], payer_id, [abr["id"]])
    candidate = next(c for c in billing.load_issue_candidates(conn, payer_id) if c.id == bill["id"])
    settlement.validate(candidate, day.upper)
    presentation.snapshot_bill(conn, bill["id"], now=now)
    for change in settlement.issue(candidate):
        billing.record_bill_state(conn, change, now=now, cause="run", notify=False)
    emit.bill(conn, bill["id"], "Create", at=now)
    for payment_id, credit_id, source_id, amount in parts:
        funding.allocate(
            conn, payer_id, currency, bill["id"], payment_id, credit_id, source_id, amount, now
        )
    billing.record_bill_state(
        conn, StateChange(bill["id"], "sent", "settled"), now=now, cause="run"
    )
    conn.execute(
        "UPDATE billing_run SET status = 'completed', finished_at = %s, heartbeat_at = %s"
        " WHERE id = %s",
        (now, now, run["id"]),
    )
    return {
        "fee_applied_rate_id": str(abr["id"]),
        "fee_bill_id": str(bill["id"]),
        "fee_payer_id": str(payer_id),
        "run_id": str(run["id"]),
    }
