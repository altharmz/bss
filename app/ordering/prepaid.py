"""Accept immediate and future prepaid purchases under command/account/catalog/number locks,
apply manual suspension/resumption orders, accept terminations and retain offering
changes for the next renewal.

A dated status change (slice 8) stays inProgress with an `order` command due on its date;
the billing owner revalidates and applies it then (app/billing/status_change.py).
A number-only change runs as on postpaid: synchronous, no money, no command.

A suspend order keeps the paid interval and its charge; at expiry it blocks renewal.
A resume order reuses a still-live grant, or queues the renewal of the expired one,
which authorizes from today. A status change dated today takes effect at once.
A termination ends the product and releases its number at the requested date; its
command, due that day, has the billing owner credit the unused dates
(app/billing/termination.py) and complete the order.
"""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.billing import entitlement
from app.billing.engine_tax import TaxUnavailable, quote_taxes
from app.billing.prepaid import IntervalQuote
from app.config import settings
from app.events import emit
from app.ordering import network, numbers
from app.ordering.states import item_transition, order_transition
from app.repository import balance_actions, billing, funding, orders, parties, products, sims
from app.repository import prepaid_commands as commands
from app.repository._sql import fetch_all, fetch_one


def lock_renewals(conn, product_ids):
    """Before the account lock, as the producer: the renewal identities of the latest grants."""
    rows = fetch_all(
        conn,
        "SELECT DISTINCT ON (product_id) id FROM funded_interval WHERE product_id = ANY(%s)"
        " ORDER BY product_id, lower(period) DESC, authorized_at DESC",
        (list(product_ids),),
    )
    ids = sorted(commands.renewal_id(row["id"]) for row in rows)
    for command_id in ids:
        commands.lock_identity(conn, command_id)
    return set(ids)


def problems(conn, request, account, start, today):
    """Prepaid orders purchase, terminate, change the offering at renewal, suspend or
    resume (today or dated), or change a number; a non-Active account only suspends
    and terminates."""
    if all(item.action == "add" for item in request.items):
        return _address_problems(request, account)
    if all(item.action == "delete" for item in request.items):
        return _not_started(conn, request)
    if all(item.action == "modify" and item.product_offering_id for item in request.items):
        return _offering_problems(conn, request, start, today)
    if number_only(request):
        return []
    if any(item.action != "modify" or item.product_status is None for item in request.items):
        return [
            (
                None,
                "prepaidLifecycleUnavailable",
                "prepaid orders purchase, terminate, change offering, suspend or resume",
            )
        ]
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


def _address_problems(request, account):
    """A prepaid line is taxed by the place of its primary use: no service address, no line."""
    if account["service_address"] is not None or not settings.tax_require_service_address:
        return []
    return [
        (
            item.item_id,
            "serviceAddressRequired",
            "the billing account needs a service address (a contact of type serviceAddress)"
            " before a prepaid line is activated",
        )
        for item in request.items
    ]


def sim_swap_problems(conn, request, start):
    """A SIM swap is an order of its own on a line: the IMSI alone changes, and no other
    swap or activation of the line waits for the network."""
    found = []
    for item in request.items:
        if item.action != "modify" or numbers.references(item) is None:
            continue
        product, line = network._line_profile(conn, item)
        moved = numbers.changes(conn, item, product, start)
        if line is None or "imsi" not in moved:
            continue
        if (
            item.product_offering_id is not None
            or item.product_status is not None
            or len(moved) > 1
        ):
            found.append(
                (
                    item.item_id,
                    "simSwapAlone",
                    "swap the SIM of a prepaid line in an order of its own, changing nothing else",
                )
            )
        elif sims.reserved_of(conn, product["id"]) is not None:
            found.append(
                (
                    item.item_id,
                    "imsiUnavailable",
                    "a SIM activation or swap of this line is still waiting for the network",
                )
            )
    return found


def number_only(request):
    """Every item is a modify of the subscriber number alone (no offering, no status)."""
    return all(
        item.action == "modify"
        and item.product_offering_id is None
        and item.product_status is None
        and numbers.references(item) is not None
        for item in request.items
    )


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


def _offering_problems(conn, request, start, today):
    """An offering change takes effect at the next renewal (the latest grant's paid-through
    date, or today once that has passed) or, on a live grant, on a date inside the paid
    interval (slice 151). One change may wait per product."""
    found = _not_started(conn, request)
    for item in request.items:
        grant = entitlement.latest_grant(conn, item.product_id)
        if grant is None:
            continue
        paid = grant["period"].upper
        if paid <= today and start != today:
            found.append(
                (
                    item.item_id,
                    "offeringChangeNotAtRenewal",
                    f"a funding-suspended prepaid offering changes today ({today})",
                )
            )
        elif paid > today and start > paid:
            found.append(
                (
                    item.item_id,
                    "offeringChangeNotAtRenewal",
                    f"{start} is after the current paid-through date {paid}",
                )
            )
    return found


def waiting_change(conn, product_id):
    """The product's retained offering change that has not taken effect yet."""
    return fetch_one(
        conn,
        "SELECT * FROM product_offering_change WHERE product_id = %s"
        " AND effect_announced_at IS NULL",
        (product_id,),
    )


def mid_period(conn, request, start):
    """The items changing inside their paid interval (before its end)."""
    return [
        item
        for item in request.items
        if entitlement.latest_grant(conn, item.product_id)["period"].upper > start
    ]


def _taxes(conn, core, price, start, tz):
    """The tax lines of a first interval, from the engine when the account has a service
    address; an unavailable engine refuses the request (503) and nothing is held."""
    try:
        return quote_taxes(conn, core, core.id, price, price.amount, start, tz)
    except TaxUnavailable as error:
        from app.api.errors import TmfError  # lazily: app.api imports this package

        raise TmfError(503, f"tax engine unavailable: {error}") from None


def prepare_changes(conn, request, account, quotes, start, now):
    """Quote the new offering's first interval from `start` for each mid-period item and
    check the total against current spendable funds: the anticipated credit never funds it.
    Returns the quotes, or None when every item changes at its renewal."""
    items = mid_period(conn, request, start)
    if not items:
        return None
    core = billing.core_account(account)
    prices = {
        p.id: p for p in billing.load_recurring_prices(conn, [i.product_offering_id for i in items])
    }
    tz = ZoneInfo(settings.billing_tz)
    out = []
    for item in items:
        price = prices[quotes[item.item_id]["id"]]
        taxes, tax_quote_id = _taxes(conn, core, price, start, tz)
        out.append(
            IntervalQuote(
                product_id=item.product_id,
                offering_id=item.product_offering_id,
                price_id=price.id,
                currency=account["currency"],
                amount=price.amount,
                taxes=taxes,
                tax_quote_id=tax_quote_id,
                anchor=start,
                index=0,
                unit=price.recurring_period,
                length=price.recurring_period_length,
            )
        )
    balance_actions.release_expired(conn, account["id"], now)
    funding.plan(conn, account["id"], quote_total(out))
    return out


def quote_total(quotes):
    return sum(
        (q.amount + sum((t.amount for t in q.taxes), Decimal(0)) for q in quotes), Decimal(0)
    )


def change_offerings(
    conn, order, request, rows, start, today, now, command_id, quoted=None, gated=False
):
    """Retain each change. At a renewal only a funded renewal applies it
    (app/billing/prepaid.py) and the order completes at once. A mid-period change holds
    the quoted amount and leaves the order inProgress until its command, due `start`, is
    posted (app/billing/offering_change.py). A number change takes effect on `start`
    regardless. A `gated` change at a renewal (a network line) also waits: a `profile_changes`
    command due `start` completes the items once the network confirmed `change_profile`
    (app/worker/network.py)."""
    state = order_transition(order["state"], "inProgress")
    orders.set_order_state(conn, order["id"], state)
    if quoted:
        funding.reserve_for_command(
            conn,
            order["billing_account_id"],
            quote_total(quoted),
            command_id=command_id,
            held_at=now,
        )
    for item in request.items:
        row = rows[item.item_id]
        progress = item_transition(row["state"], "inProgress")
        orders.set_order_item_state(conn, row["id"], progress)
        products.change_offering(
            conn,
            item.product_id,
            change_date=start,
            product_offering_id=item.product_offering_id,
            changed_by_item_id=row["id"],
        )
        if numbers.references(item) is not None:
            numbers.execute(conn, item, item.product_id, row["id"], start, now)
            if start <= today:
                emit.product(conn, item.product_id, "AttributeValueChange", at=now, today=today)
        if not quoted and not gated:
            orders.set_order_item_state(conn, row["id"], item_transition(progress, "completed"))
    if gated and not quoted:
        commands.enqueue(
            conn,
            command_id,
            order["billing_account_id"],
            kind="order",
            due_date=start,
            request={
                "order_id": str(order["id"]),
                "profile_changes": [str(i.product_id) for i in request.items],
            },
            now=now,
        )
        return
    if quoted:
        commands.enqueue(
            conn,
            command_id,
            order["billing_account_id"],
            kind="order",
            due_date=start,
            request={
                "order_id": str(order["id"]),
                "offering_changes": [q.model_dump(mode="json") for q in quoted],
            },
            now=now,
        )
        return
    orders.set_order_state(
        conn, order["id"], order_transition(state, "completed"), completion_date=now
    )


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


def schedule_statuses(conn, order, request, rows, command_id, start, today, now):
    """Retain dated status changes: the items stay inProgress and one `order` command
    due on `start` applies them (app/billing/status_change.py)."""
    state = order_transition(order["state"], "inProgress")
    orders.set_order_state(conn, order["id"], state)
    for item in request.items:
        row = rows[item.item_id]
        orders.set_order_item_state(conn, row["id"], item_transition(row["state"], "inProgress"))
        products.change_status(
            conn,
            item.product_id,
            change_date=start,
            status=item.product_status,
            changed_by_item_id=row["id"],
        )
    commands.enqueue(
        conn,
        command_id,
        order["billing_account_id"],
        kind="order",
        due_date=start,
        request={
            "order_id": str(order["id"]),
            "status_changes": [
                {"product_id": str(i.product_id), "status": i.product_status} for i in request.items
            ],
        },
        now=now,
    )


def schedule_sim_swaps(conn, order, request, rows, command_id, start, now, targets):
    """Reserve the new SIM of each item and wait for the network: one `order` command due
    on `start` moves the claims, retires the old SIM and completes the items once
    `swap_sim` is confirmed (app/worker/network.py); a refusal only drops the reservation."""
    orders.set_order_state(conn, order["id"], order_transition(order["state"], "inProgress"))
    swaps = []
    for item in request.items:
        row = rows[item.item_id]
        orders.set_order_item_state(conn, row["id"], item_transition(row["state"], "inProgress"))
        sims.hold(conn, targets[item.item_id], item.product_id, "reserved", now)
        swaps.append(
            {
                "item_id": str(row["id"]),
                "product_id": str(item.product_id),
                "subscriber_number_id": str(targets[item.item_id]),
            }
        )
    commands.enqueue(
        conn,
        command_id,
        order["billing_account_id"],
        kind="order",
        due_date=start,
        request={"order_id": str(order["id"]), "sim_swaps": swaps},
        now=now,
    )


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


def prepare(conn, request, account, quotes, start, now, *, future=False):
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
        taxes, tax_quote_id = _taxes(conn, core, price, start, ZoneInfo(settings.billing_tz))
        quote = IntervalQuote(
            product_id=new.id,
            offering_id=item.product_offering_id,
            price_id=price.id,
            currency=account["currency"],
            amount=price.amount,
            taxes=taxes,
            tax_quote_id=tax_quote_id,
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
    balance_actions.release_expired(conn, account["id"], now)
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
