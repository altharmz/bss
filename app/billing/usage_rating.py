"""Rating of pay-as-you-go usage against the prepaid balance (docs/decisions/usage.md).

One pass rates an account's closed days (event date before today) of unrated records of its
pay-as-you-go lines. The records of a line, event date, kind and usage zone (#200) are summed,
rounded up once to whole blocks of the usage price's unit and charged `blocks x price`: one
`oneTime` ABR per group (taxed through the tax adapter as usage), all on one off-cycle bill
that is issued and settled against the account's unused payments at once. The balance is the
ledger, so the
charge draws it down; what the balance cannot pay stays an open bill and the next payment
settles it first (a negative balance recovered from the top-up).

A pass is a retained `prepaid_command` of kind `usage` with its own run, posted in one
transaction under the account lock: it commits completely or not at all, and the primary key
of `usage_charge` keeps a record from ever being charged twice.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.range import Range

from app.billing import engine_tax, settlement
from app.billing.adapter import settle_account
from app.billing.core import midnight
from app.billing.model import AppliedTax
from app.billing.prepaid import FeeQuote
from app.events import emit
from app.network import kind as offering_kind
from app.repository import billing, presentation, products
from app.repository import prepaid_commands as commands
from app.repository import usage as repo
from app.repository._sql import Row, fetch_one
from app.worker.db import holds_ownership

NAMESPACE = UUID("5c0f8a2e-91d4-5b37-a6c8-2e7d4f1b9a03")
BATCH = 2000


def pass_id(first_record: UUID) -> UUID:
    """The command of a pass: the first record it rates belongs to no other pass."""
    return uuid5(NAMESPACE, str(first_record))


def blocks_of(quantity: int, block: int) -> int:
    """Whole blocks the quantity needs, rounded up."""
    return -(-quantity // block)


def rate_account(
    conn: psycopg.Connection, account_id: UUID, *, now: datetime, tz: ZoneInfo
) -> dict | None:
    """Rate the account's closed days. Returns the command's result, or None when there was
    nothing to rate. The caller holds a savepoint: any error leaves nothing behind."""
    if not holds_ownership(conn):
        raise ValueError("rating requires billing ownership")
    today = now.astimezone(tz).date()
    account = billing.load_account(conn, account_id)  # the account lock
    state = fetch_one(conn, "SELECT rating_type FROM billing_account WHERE id = %s", (account_id,))
    if state is None or state["rating_type"] != "prepaid":
        return None
    groups: dict[tuple[UUID, date, str, str], list[Row]] = defaultdict(list)
    for record in repo.unrated(conn, account_id, today, BATCH):
        offering = products.offering_on(conn, record["product_id"], record["event_date"])
        details = conn.execute(
            "SELECT details FROM product_offering WHERE id = %s", (offering,)
        ).fetchone()[0]
        if offering_kind.kind_of(details) == offering_kind.PAY_AS_YOU_GO:
            zone = repo.zone_of(record["kind"], record["roaming"], record["international"])
            groups[record["product_id"], record["event_date"], record["kind"], zone].append(record)
    if not groups:
        return None
    first = min((r["id"] for rows in groups.values() for r in rows), key=str)
    command_id = pass_id(first)
    ids = sorted(str(r["id"]) for rows in groups.values() for r in rows)
    commands.enqueue(
        conn,
        command_id,
        account_id,
        kind="usage",
        due_date=today,
        request={"records": ids},
        now=now,
    )
    run = commands.ensure_run(conn, command_id, run_date=today, now=now)
    abrs: list[UUID] = []
    gross = Decimal(0)
    for (product_id, day, usage_kind, zone), records in sorted(groups.items(), key=lambda g: g[0]):
        offering = products.offering_on(conn, product_id, day)
        found = billing.load_usage_prices(conn, offering, account.currency, midnight(day, tz))
        # a zone without a price of its own is not charged (operators define zone prices)
        priced = found.get((usage_kind, zone))
        abr_id = None
        blocks = None
        if priced is not None:
            price, name, block = priced
            blocks = blocks_of(sum(r["quantity"] for r in records), block)
            net = price.amount * blocks
            if net > 0:
                taxes, quote_id = engine_tax.quote_taxes(
                    conn, account, account_id, price, net, day, tz, transaction_type="usage"
                )
                quote = FeeQuote(
                    price_id=price.id, name=name, amount=net, taxes=taxes, tax_quote_id=quote_id
                )
                abr = billing.insert_abr(
                    conn,
                    product_id=product_id,
                    billing_account_id=account_id,
                    charge_type="oneTime",
                    amount=net,
                    currency=account.currency,
                    period=Range(day, day + timedelta(days=1), "[)"),
                    billing_run_id=run["id"],
                    applied_at=now,
                    product_offering_id=offering,
                    taxes=tuple(
                        AppliedTax(t.category, t.rate, t.amount, t.jurisdiction) for t in taxes
                    ),
                    one_time_price_id=price.id,
                    charge_name=name,
                    usage_command_id=command_id,
                )
                if quote_id is not None:
                    engine_tax.commit(conn, abr, account_id, quote, now=now)
                abr_id = abr["id"]
                abrs.append(abr_id)
                gross += quote.gross
        for record in records:
            repo.insert_charge(
                conn,
                record_id=record["id"],
                event_date=record["event_date"],
                command_id=command_id,
                account_id=account_id,
                product_id=product_id,
                price_id=None if priced is None else priced[0].id,
                abr_id=abr_id,
                blocks=blocks,
                now=now,
            )
    bills = []
    if abrs:
        bills.append(_post_bill(conn, account, run, abrs, gross, now, today))
        settle_account(conn, account, now=now, cause="run")
    conn.execute(
        "UPDATE billing_run SET status = 'completed', finished_at = %s, heartbeat_at = %s"
        " WHERE id = %s",
        (now, now, run["id"]),
    )
    result = {
        "outcome": "rated",
        "run_id": str(run["id"]),
        "records": len(ids),
        "bills": [str(b) for b in bills],
        "charged": str(gross),
    }
    return commands.complete(conn, command_id, result, now=now)["result"]


def _post_bill(conn, account, run, abrs: list[UUID], gross: Decimal, now, today: date) -> UUID:
    day = Range(today, today + timedelta(days=1), "[)")
    bill = billing.insert_bill(
        conn,
        billing_account_id=account.id,
        currency=account.currency,
        bill_period=day,
        bill_date=today,
        amount_due=gross,
        billing_run_id=run["id"],
        payment_due_date=today,
        run_type="offCycle",
    )
    billing.record_bill_issue(conn, bill["id"], now=now)
    billing.attach_abrs_to_bill(conn, bill["id"], account.id, abrs)
    candidate = next(
        c for c in billing.load_issue_candidates(conn, account.id) if c.id == bill["id"]
    )
    settlement.validate(candidate, day.upper)
    presentation.snapshot_bill(conn, bill["id"], now=now)
    for change in settlement.issue(candidate):
        billing.record_bill_state(conn, change, now=now, cause="run", notify=False)
    emit.bill(conn, bill["id"], "Create", at=now)
    return bill["id"]


__all__ = ["BATCH", "blocks_of", "pass_id", "rate_account"]
