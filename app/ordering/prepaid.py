"""Accept immediate prepaid purchases under command/account/catalog/number locks."""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.billing.core import charge_taxes
from app.billing.prepaid import IntervalQuote, TaxQuote
from app.config import settings
from app.events import emit
from app.ordering import numbers
from app.repository import billing, funding, orders, products
from app.repository import prepaid_commands as commands


def prepare(conn, request, account, quotes, start):
    """Validate full-period tax-inclusive funds before creating products or claims."""
    core = billing.core_account(account)
    prices = {
        p.id: p
        for p in billing.load_recurring_prices(conn, [i.product_offering_id for i in request.items])
    }
    accepted = {}
    for item in request.items:
        new = item.new_product or products.NewProduct(details=(item.details or {}).get("product"))
        new = replace(new, id=new.id or uuid4())
        price = prices[quotes[item.item_id]["id"]]
        quote = IntervalQuote(
            product_id=new.id,
            offering_id=item.product_offering_id,
            price_id=price.id,
            currency=account["currency"],
            amount=price.amount,
            taxes=[
                TaxQuote(category=t.category, rate=t.rate, amount=t.amount)
                for t in charge_taxes(
                    price.amount, price, core, start, ZoneInfo(settings.billing_tz)
                )
            ],
            anchor=start,
            index=0,
            unit=price.recurring_period,
            length=price.recurring_period_length,
        )
        accepted[item.item_id] = (new, quote)
    total = sum(
        (q.amount + sum((t.amount for t in q.taxes), Decimal(0)) for _, q in accepted.values()),
        Decimal(0),
    )
    funding.plan(conn, account["id"], total)
    return accepted, total


def accept(conn, order, request, rows, prepared, command_id, start, now):
    """Caller transaction retains the hold, complete item tree and command together."""
    accepted, total = prepared
    funding.reserve_for_command(
        conn, order["billing_account_id"], total, command_id=command_id, held_at=now
    )
    orders.set_order_state(conn, order["id"], "inProgress")
    for item in request.items:
        row = rows[item.item_id]
        new, _ = accepted[item.item_id]
        orders.set_order_item_state(conn, row["id"], "inProgress")
        product = products.create_product(
            conn,
            billing_account_id=order["billing_account_id"],
            product_offering_id=item.product_offering_id,
            created_by_item_id=row["id"],
            start_date=start,
            status="pendingActive",
            new=new,
        )
        orders.set_order_item_product(conn, row["id"], product["id"])
        numbers.execute(conn, item, product["id"], row["id"], start, now)
        emit.product(conn, product["id"], "Create", at=now, today=start)
    commands.enqueue(
        conn,
        command_id,
        order["billing_account_id"],
        kind="order",
        due_date=start,
        request={
            "order_id": str(order["id"]),
            "intervals": [quote.model_dump(mode="json") for _, quote in accepted.values()],
        },
        now=now,
    )
