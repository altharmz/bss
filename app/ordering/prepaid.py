"""Accept immediate and future prepaid purchases under command/account/catalog/number locks,
apply manual suspension/resumption orders and accept terminations.

A suspend order keeps the paid interval and its charge; at expiry it blocks renewal.
A resume order reuses a still-live grant, or queues the renewal of the expired one,
which authorizes from today. Status changes take effect on the order's day only.
A termination ends the product and releases its number at the requested date; its
command, due that day, has the billing owner credit the unused dates
(app/billing/termination.py) and complete the order.
"""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.billing import entitlement
from app.billing.core import charge_taxes
from app.billing.prepaid import IntervalQuote, TaxQuote
from app.config import settings
from app.events import emit
from app.ordering import numbers
from app.ordering.states import item_transition, order_transition
from app.repository import billing, funding, orders, parties, products
from app.repository import prepaid_commands as commands
from app.repository._sql import fetch_all, fetch_one


def lock_renewals(conn, product_ids):
    """Before the account lock, as the producer: the renewal identities of the latest grants."""
    rows = fetch_all(
        conn,
        "SELECT DISTINCT ON (product_id) id FROM funded_interval WHERE product_id = ANY(%s)"
        " ORDER BY product_id, upper(period) DESC",
        (list(product_ids),),
    )
    ids = sorted(commands.renewal_id(row["id"]) for row in rows)
    for command_id in ids:
        commands.lock_identity(conn, command_id)
    return set(ids)


def problems(conn, request, account, start, today):
    """Prepaid orders purchase, terminate, or suspend/resume today; a non-Active account
    only suspends and terminates."""
    if all(item.action == "add" for item in request.items):
        return []
    if all(item.action == "delete" for item in request.items):
        return _not_started(conn, request)
    if any(item.action != "modify" or item.product_status is None for item in request.items):
        return [
            (
                None,
                "prepaidLifecycleUnavailable",
                "prepaid orders purchase, terminate, suspend or resume",
            )
        ]
    if start != today:
        return [(None, "prepaidLifecycleUnavailable", "prepaid status changes take effect today")]
    found = []
    if account["state"] != "Active" and any(i.product_status == "active" for i in request.items):
        found.append(
            (
                None,
                "accountNotActive",
                f"billing account is {account['state']}: only suspensions and cleanup orders",
            )
        )
    return found + _not_started(conn, request)


def _not_started(conn, request):
    return [
        (
            item.item_id,
            "productNotStarted",
            "the prepaid product has no funded grant yet: cancel its order instead",
        )
        for item in request.items
        if entitlement.latest_grant(conn, item.product_id) is None
    ]


def terminate(conn, order, request, rows, command_id, start, today, now):
    """End the products on `start` and queue the credit command due that day; the order
    stays inProgress until the billing owner posts it."""
    orders.set_order_state(conn, order["id"], order_transition(order["state"], "inProgress"))
    for item in request.items:
        row = rows[item.item_id]
        orders.set_order_item_state(conn, row["id"], item_transition(row["state"], "inProgress"))
        numbers.execute(conn, item, item.product_id, row["id"], start, now)
        products.terminate_product(
            conn,
            item.product_id,
            termination_date=start,
            terminated_by_item_id=row["id"],
            status="terminated" if start <= today else "pendingTerminate",
        )
        emit.product(conn, item.product_id, "StateChange", at=now, today=today)
    commands.enqueue(
        conn,
        command_id,
        order["billing_account_id"],
        kind="order",
        due_date=start,
        request={
            "order_id": str(order["id"]),
            "terminations": [str(item.product_id) for item in request.items],
        },
        now=now,
    )


def restore_status(conn, product_id, account, today):
    """After a cancelled status change: the latest applied change's manual cause and the
    effective status. True if the status changed."""
    before = products.get_product(conn, product_id)["status"]
    latest = fetch_one(
        conn,
        "SELECT status FROM product_status_change WHERE product_id = %s"
        " AND applied_at IS NOT NULL ORDER BY change_date DESC LIMIT 1",
        (product_id,),
    )
    conn.execute(
        "UPDATE product SET manually_suspended = %s WHERE id = %s",
        (latest is not None and latest["status"] == "suspended", product_id),
    )
    product = products.get_product(conn, product_id)
    status = entitlement.view(conn, product, account, today)["status"]
    products.set_product_status(conn, product_id, status)
    return status != before


def change_statuses(conn, order, request, rows, renewals, today, now):
    """Set or lift the manual cause; the stored status follows effective entitlement."""
    account = parties.get_billing_account(conn, order["billing_account_id"])
    state = order_transition(order["state"], "inProgress")
    orders.set_order_state(conn, order["id"], state)
    for item in request.items:
        row = rows[item.item_id]
        progress = item_transition(row["state"], "inProgress")
        orders.set_order_item_state(conn, row["id"], progress)
        before = products.get_product(conn, item.product_id)["status"]
        products.change_status(
            conn,
            item.product_id,
            change_date=today,
            status=item.product_status,
            changed_by_item_id=row["id"],
            applied_at=now,
        )
        product = products.get_product(conn, item.product_id)
        status = entitlement.view(conn, product, account, today)["status"]
        products.set_product_status(conn, item.product_id, status)
        grant = entitlement.latest_grant(conn, item.product_id)
        renewal = commands.renewal_id(grant["id"])
        resumed = item.product_status == "active"
        if resumed and grant["period"].upper <= today and renewal in renewals:
            commands.enqueue(
                conn,
                renewal,
                account["id"],
                kind="renewal",
                due_date=grant["period"].upper,
                request={"previous_interval_id": str(grant["id"])},
                now=now,
            )
        if status != before:
            emit.product(conn, item.product_id, "StateChange", at=now, today=today)
        orders.set_order_item_state(conn, row["id"], item_transition(progress, "completed"))
    orders.set_order_state(
        conn, order["id"], order_transition(state, "completed"), completion_date=now
    )


def prepare(conn, request, account, quotes, start, *, future=False):
    """Validate immediate funds, or retain future product identities without a quote."""
    core = billing.core_account(account)
    prices = {
        p.id: p
        for p in billing.load_recurring_prices(conn, [i.product_offering_id for i in request.items])
    }
    accepted = {}
    for item in request.items:
        new = item.new_product or products.NewProduct(details=(item.details or {}).get("product"))
        new = replace(new, id=new.id or uuid4())
        if future:
            accepted[item.item_id] = (new, None)
            continue
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
    if future:
        return accepted, None
    total = sum(
        (q.amount + sum((t.amount for t in q.taxes), Decimal(0)) for _, q in accepted.values()),
        Decimal(0),
    )
    funding.plan(conn, account["id"], total)
    return accepted, total


def accept(conn, order, request, rows, prepared, command_id, start, now):
    """Caller transaction retains the hold, complete item tree and command together."""
    accepted, total = prepared
    if total is not None:
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
            **(
                {"products": [str(new.id) for new, _ in accepted.values()]}
                if total is None
                else {
                    "intervals": [quote.model_dump(mode="json") for _, quote in accepted.values()]
                }
            ),
        },
        now=now,
    )
