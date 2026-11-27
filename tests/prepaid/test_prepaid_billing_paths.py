"""Prepaid billing command guards, replays, and their effects on the real ledger."""

from datetime import timedelta
from decimal import Decimal as D
from uuid import UUID, uuid4

import pytest

from app.billing import offering_change, one_time, prepaid, termination
from app.integrations.tax import adapter as tax_adapter
from app.repository import funding
from app.repository import prepaid_commands as commands
from app.worker import db as worker_db
from tests import apidata
from tests.builders.billing.tax_engine import CA, set_address
from tests.builders.prepaid import prepaid_plans
from tests.builders.prepaid.prepaid_offering_changes import granted, switch  # noqa: F401
from tests.builders.prepaid.prepaid_plans import buy, offering, price, stubs  # noqa: F401
from tests.builders.prepaid.prepaid_posting import TZ, post, prepared
from tests.builders.prepaid.prepaid_renewal_producer import pay
from tests.builders.prepaid.prepaid_terminations import terminate
from tests.builders.resources.network_provisioning import PROFILE
from tests.builders.tmf.events import stored, subscribe
from tests.factories import NOW

build = prepaid_plans.build

POST = {
    "change": offering_change.post_offering_change,
    "charges": one_time.post_charges,
    "termination": termination.post_termination,
    "interval": prepaid.post_interval,
}


@pytest.fixture
def order_for(api, db_conn, build, request):
    def place(kind):
        if kind in {"charges", "interval"}:
            world = build()
            offer = world.payg if kind == "charges" else world.plan
            order = apidata.order(api, world.acc, apidata.add_item(offer))
        else:
            granted = request.getfixturevalue("granted")
            worker_db.release_ownership(db_conn)
            first, _ = granted.products
            if kind == "change":
                pay(db_conn, UUID(granted.acc["id"]), "6", api.clock.now())
                order = switch(api, granted.acc, first, granted.b, api.clock.now().date())
            else:
                order = terminate(api, granted.acc, first)
        assert order["state"] == "inProgress", order
        return order

    return place


def effects(conn):
    return conn.execute(
        "SELECT (SELECT count(*) FROM billing_run),"
        " (SELECT count(*) FROM applied_customer_billing_rate),"
        " (SELECT count(*) FROM customer_bill),"
        " (SELECT count(*) FROM funded_interval),"
        " (SELECT count(*) FROM funding_hold WHERE released_at IS NULL),"
        " (SELECT count(*) FROM funding_hold_conversion)"
    ).fetchone()


def execute(conn, kind, cid, now):
    return POST[kind](conn, cid, now=now, tz=TZ)


def assert_pending(conn, cid, before):
    assert effects(conn) == before
    command = commands.get(conn, cid)
    assert command["state"] == "pending" and command["attempts"] == 0


@pytest.mark.parametrize(
    ("kind", "outcome"),
    [("change", "offeringChanged"), ("charges", "charged"), ("termination", "terminated")],
)
def test_posting_needs_the_owners_lock_and_replay_keeps_the_original_result(
    api, db_conn, order_for, kind, outcome
):
    order = order_for(kind)
    cid = UUID(order["id"])
    now = api.clock.now()
    before = effects(db_conn)
    with pytest.raises(ValueError, match="requires billing ownership"):
        execute(db_conn, kind, cid, now)
    assert_pending(db_conn, cid, before)

    assert worker_db.acquire_ownership(db_conn)
    result = execute(db_conn, kind, cid, now)
    db_conn.commit()
    assert result["outcome"] == outcome
    assert api.get(order["href"]).json()["state"] == "completed"
    after = effects(db_conn)
    assert after != before
    assert execute(db_conn, kind, cid, now + timedelta(days=1)) == result
    assert effects(db_conn) == after
    assert commands.get(db_conn, cid)["attempts"] == 1


def use_request(patch, change):
    ready = commands.ready

    def scripted(*args, **kwargs):
        row = ready(*args, **kwargs)
        return row | {"request": change(row["request"])}

    patch.setattr(commands, "ready", scripted)


@pytest.mark.parametrize(
    ("kind", "key", "match"),
    [
        ("change", "offering_changes", "duplicate product in offering change"),
        ("charges", "charges", "duplicate product in order charges"),
        ("termination", "terminations", "duplicate terminated product"),
    ],
)
def test_duplicate_retained_items_leave_the_order_pending(
    api, db_conn, order_for, monkeypatch, kind, key, match
):
    order = order_for(kind)
    cid = UUID(order["id"])
    assert worker_db.acquire_ownership(db_conn)
    before = effects(db_conn)
    request = commands.get(db_conn, cid)["request"]
    with monkeypatch.context() as patch:
        use_request(patch, lambda value: value | {key: value[key] * 2})
        with pytest.raises(ValueError, match=match):
            execute(db_conn, kind, cid, api.clock.now())
    assert effects(db_conn) == before
    assert commands.get(db_conn, cid)["request"] == request
    db_conn.commit()
    assert api.get(order["href"]).json()["state"] == "inProgress"


@pytest.mark.parametrize("kind", POST)
def test_a_command_cannot_post_a_different_pending_order(
    api, db_conn, order_for, monkeypatch, kind
):
    order = order_for(kind)
    cid = UUID(order["id"])
    assert worker_db.acquire_ownership(db_conn)
    before = effects(db_conn)
    wrong_order = str(uuid4())
    with monkeypatch.context() as patch:
        use_request(patch, lambda request: request | {"order_id": wrong_order})
        with pytest.raises(ValueError, match="must match its entire pending order"):
            execute(db_conn, kind, cid, api.clock.now())
    assert effects(db_conn) == before
    db_conn.commit()
    assert api.get(order["href"]).json()["state"] == "inProgress"


@pytest.mark.parametrize("kind", ["change", "charges", "interval"])
@pytest.mark.parametrize(
    ("problem", "match"),
    [
        ("hold", "exact outstanding command hold"),
        ("run", "command run is not running"),
    ],
)
def test_posting_refuses_a_released_hold_or_a_finished_run(
    api, db_conn, order_for, kind, problem, match
):
    order = order_for(kind)
    cid = UUID(order["id"])
    now = api.clock.now()
    assert worker_db.acquire_ownership(db_conn)
    if problem == "hold":
        account_id, hold_id = db_conn.execute(
            "SELECT billing_account_id, hold_id FROM funding_command_hold WHERE command_id = %s",
            (cid,),
        ).fetchone()
        funding.release(db_conn, account_id, hold_id, released_at=now, command_id=cid)
    else:
        run = commands.ensure_run(db_conn, cid, now=now, run_date=now.date())
        db_conn.execute(
            "UPDATE billing_run SET status = 'completed', finished_at = %s WHERE id = %s",
            (now, run["id"]),
        )
    before = effects(db_conn)
    with pytest.raises(ValueError, match=match):
        execute(db_conn, kind, cid, now)
    assert_pending(db_conn, cid, before)
    db_conn.commit()
    assert api.get(order["href"]).json()["state"] == "inProgress"


@pytest.mark.parametrize(
    ("kind", "match"),
    [
        ("change", "requires an active prepaid account"),
        ("charges", "need an order command of an active prepaid account"),
    ],
)
def test_a_suspended_account_keeps_its_purchase_pending(api, db_conn, order_for, kind, match):
    order = order_for(kind)
    cid = UUID(order["id"])
    assert worker_db.acquire_ownership(db_conn)
    account_id = commands.get(db_conn, cid)["billing_account_id"]
    db_conn.execute("UPDATE billing_account SET state = 'Suspended' WHERE id = %s", (account_id,))
    before = effects(db_conn)
    with pytest.raises(ValueError, match=match):
        execute(db_conn, kind, cid, api.clock.now())
    assert_pending(db_conn, cid, before)


@pytest.mark.parametrize(
    ("race", "match"),
    [
        ("quote", "quote does not match the pending change"),
        ("grant", "not inside the product's paid interval"),
    ],
)
def test_a_switch_checks_the_retained_quote_and_the_latest_grant_under_lock(
    api, db_conn, order_for, monkeypatch, race, match
):
    order = order_for("change")
    cid = UUID(order["id"])
    assert worker_db.acquire_ownership(db_conn)
    before = effects(db_conn)

    def wrong_anchor(request):
        [quote] = request["offering_changes"]
        return request | {"offering_changes": [quote | {"index": 1}]}

    with monkeypatch.context() as patch:
        if race == "quote":
            use_request(patch, wrong_anchor)
        else:
            patch.setattr(offering_change.entitlement, "latest_grant", lambda conn, pid: None)
        with pytest.raises(ValueError, match=match):
            execute(db_conn, "change", cid, api.clock.now())
    assert effects(db_conn) == before
    db_conn.commit()
    assert api.get(order["href"]).json()["state"] == "inProgress"


def test_switch_from_a_free_plan_restores_funded_status_without_a_credit(api, db_conn, build):
    free = offering(
        api, "Free plan", [price(api, "recurring", 0, "Monthly", "monthly", 1)], profile=PROFILE
    )
    world = build(fee=False)
    bought = buy(api, db_conn, world, free)
    product = bought.product
    now = api.clock.now()
    order = switch(api, world.acc, product, world.plan, now.date())
    assert order["state"] == "inProgress", order
    subscribe(api, "TMF637", "ProductStateChangeEvent")
    db_conn.execute("UPDATE product SET status = 'created' WHERE id = %s", (product["id"],))
    assert worker_db.acquire_ownership(db_conn)
    result = execute(db_conn, "change", UUID(order["id"]), now)
    db_conn.commit()
    assert result["credits"] == []
    assert len(result["purchases"]) == 1
    assert db_conn.execute(
        "SELECT charge_type, amount FROM applied_customer_billing_rate ORDER BY amount"
    ).fetchall() == [("recurring", D(0)), ("recurring", D(10))]
    assert db_conn.execute(
        "SELECT status FROM product WHERE id = %s", (product["id"],)
    ).fetchone() == ("active",)
    assert [event["event"]["product"]["status"] for _, event in stored(db_conn, "TMF637")] == [
        "active"
    ]


def test_termination_credits_only_the_new_grant_after_a_same_day_switch(
    api,
    db_conn,
    granted,  # noqa: F811
):
    first, _ = granted.products
    now = api.clock.now()
    pay(db_conn, UUID(granted.acc["id"]), "6", now)
    change = switch(api, granted.acc, first, granted.b, now.date())
    assert worker_db.acquire_ownership(db_conn)
    switched = execute(db_conn, "change", UUID(change["id"]), now)
    db_conn.commit()
    order = terminate(api, granted.acc, first)
    assert order["state"] == "inProgress", order
    result = execute(db_conn, "termination", UUID(order["id"]), now)
    db_conn.commit()
    [credited] = result["credits"]
    assert credited["charge_id"] == switched["purchases"][0]["applied_rate_id"]
    assert db_conn.execute(
        "SELECT amount, tax_amount FROM applied_customer_billing_rate"
        " WHERE charge_type = 'refund' ORDER BY amount"
    ).fetchall() == [(D("-10"), D("-2")), (D("-5"), D("-1"))]
    assert api.get(order["href"]).json()["state"] == "completed"


def test_a_free_payg_order_releases_a_hold_without_issuing_a_bill(api, db_conn, build):
    free = offering(api, "Free PAYG", [], kind="payAsYouGo")
    world = build()
    order = apidata.order(api, world.acc, apidata.add_item(free))
    assert order["state"] == "inProgress", order
    cid = UUID(order["id"])
    aid = UUID(world.acc["id"])
    now = api.clock.now()
    hold = funding.reserve_for_command(db_conn, aid, D(2), command_id=cid, held_at=now)
    assert funding.load(db_conn, aid).spendable == D(28)
    assert worker_db.acquire_ownership(db_conn)
    result = execute(db_conn, "charges", cid, now + timedelta(minutes=1))
    db_conn.commit()
    assert result["bills"] == []
    assert db_conn.execute("SELECT count(*) FROM applied_customer_billing_rate").fetchone() == (0,)
    assert db_conn.execute(
        "SELECT released_at FROM funding_hold WHERE id = %s", (hold,)
    ).fetchone() == (now + timedelta(minutes=1),)
    assert funding.load(db_conn, aid).spendable == D(30)
    done = api.get(order["href"]).json()
    assert done["state"] == "completed"
    assert apidata.product_of(api, done)["status"] == "active"


def test_one_time_fee_posts_the_tax_engine_quote_against_its_own_charge(api, db_conn, build):
    world = build()
    engine = tax_adapter()
    engine.reset()
    try:
        assert set_address(api, world.acc, CA).status_code == 200
        order = apidata.order(api, world.acc, apidata.add_item(world.payg))
        assert order["state"] == "inProgress", order
        cid = UUID(order["id"])
        [charge] = commands.get(db_conn, cid)["request"]["charges"]
        [fee] = charge["fees"]
        assert worker_db.acquire_ownership(db_conn)
        result = execute(db_conn, "charges", cid, api.clock.now())
        db_conn.commit()
        assert db_conn.execute(
            "SELECT a.charge_type, a.amount, a.customer_bill_id, t.state, t.quote_id"
            " FROM applied_customer_billing_rate a"
            " JOIN tax_transaction t ON t.applied_rate_id = a.id"
        ).fetchall() == [
            ("oneTime", D(2), UUID(result["bills"][0]), "committed", fee["tax_quote_id"])
        ]
        assert len(engine.committed) == 1
    finally:
        engine.reset()


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (
            {"taxes": [{"category": "VAT", "rate": 20, "amount": 2}] * 2},
            "duplicate tax category",
        ),
        (
            {
                "fees": [
                    {
                        "price_id": str(uuid4()),
                        "name": "Fee",
                        "amount": "5",
                        "taxes": [{"category": "VAT", "rate": "20", "amount": "2"}],
                    }
                ]
            },
            "fee tax does not match amount and rate",
        ),
    ],
)
def test_an_invalid_retained_interval_tax_cannot_create_an_invoice(db_conn, change, match):
    _, _, cmd, _ = prepared(db_conn, request_changes=change)
    assert worker_db.acquire_ownership(db_conn)
    before = effects(db_conn)
    with pytest.raises(ValueError, match=match):
        post(db_conn, cmd)
    assert_pending(db_conn, cmd["id"], before)


@pytest.mark.parametrize(
    ("kind", "payload", "match"),
    [
        ("transfer", {}, "interval posting requires an order or renewal"),
        ("renewal", {"products": []}, "multi-product posting requires an order"),
        ("renewal", {"previous_interval_id": str(uuid4())}, "requires an owned previous interval"),
        (
            "order",
            {"order_id": str(uuid4()), "products": ["duplicate"]},
            "duplicate future product",
        ),
        (
            "order",
            {"order_id": str(uuid4()), "products": [str(uuid4())]},
            "does not belong to account",
        ),
    ],
)
def test_an_invalid_journal_entry_cannot_create_a_grant(db_conn, kind, payload, match):
    acc, prod, _, _ = prepared(db_conn)
    if payload.get("products") == ["duplicate"]:
        payload = payload | {"products": [str(prod["id"]), str(prod["id"])]}
    cmd = commands.enqueue(
        db_conn, uuid4(), acc["id"], kind=kind, due_date=NOW.date(), request=payload, now=NOW
    )
    assert worker_db.acquire_ownership(db_conn)
    before = effects(db_conn)
    with pytest.raises(ValueError, match=match):
        post(db_conn, cmd)
    assert_pending(db_conn, cmd["id"], before)


def test_interval_posting_requires_an_aware_authorization_time(db_conn):
    _, _, cmd, _ = prepared(db_conn)
    assert worker_db.acquire_ownership(db_conn)
    before = effects(db_conn)
    with pytest.raises(ValueError, match="requires an aware instant"):
        post(db_conn, cmd, NOW.replace(tzinfo=None))
    assert_pending(db_conn, cmd["id"], before)


def test_interval_posting_refuses_a_product_before_its_start(db_conn):
    _, prod, cmd, _ = prepared(db_conn)
    db_conn.execute(
        "UPDATE product SET start_date = %s WHERE id = %s",
        (NOW.date() + timedelta(days=1), prod["id"]),
    )
    assert worker_db.acquire_ownership(db_conn)
    before = effects(db_conn)
    with pytest.raises(ValueError, match="not eligible on authorization date"):
        post(db_conn, cmd)
    assert_pending(db_conn, cmd["id"], before)


@pytest.mark.parametrize(
    ("kind", "match"),
    [
        ("change", "requires a prepaid order command"),
        ("termination", "requires a prepaid order command"),
    ],
)
def test_a_renewal_cannot_post_an_order_effect(api, db_conn, order_for, kind, match):
    order = order_for(kind)
    original = commands.get(db_conn, UUID(order["id"]))
    now = api.clock.now()
    renewal = commands.enqueue(
        db_conn,
        uuid4(),
        original["billing_account_id"],
        kind="renewal",
        due_date=original["due_date"],
        request=original["request"],
        now=now,
    )
    assert worker_db.acquire_ownership(db_conn)
    before = effects(db_conn)
    with pytest.raises(ValueError, match=match):
        execute(db_conn, kind, renewal["id"], now)
    assert_pending(db_conn, renewal["id"], before)


@pytest.mark.parametrize(
    ("problem", "match"),
    [
        ("ended", "product lifecycle prevents charging"),
        ("foreign", "charged product does not belong to account"),
    ],
)
def test_an_activation_fee_requires_a_live_product_of_the_order_account(
    api, db_conn, order_for, problem, match
):
    order = order_for("charges")
    cid = UUID(order["id"])
    [charge] = commands.get(db_conn, cid)["request"]["charges"]
    if problem == "ended":
        db_conn.execute(
            "UPDATE product SET status = 'aborted' WHERE id = %s", (charge["product_id"],)
        )
    else:
        other = apidata.account(api, name="Other account")
        db_conn.execute(
            "UPDATE product SET billing_account_id = %s WHERE id = %s",
            (other["id"], charge["product_id"]),
        )
    assert worker_db.acquire_ownership(db_conn)
    before = effects(db_conn)
    with pytest.raises(ValueError, match=match):
        execute(db_conn, "charges", cid, api.clock.now())
    assert_pending(db_conn, cid, before)
