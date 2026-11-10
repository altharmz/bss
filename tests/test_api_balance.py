"""P4c: TMF654 bucket and accumulatedBalance reads, and TMF666 currentBalance with the
balance-action ledger entries. Every response is checked against the vendored schema
by the `api` client (tests/apiclient.py)."""

from datetime import date
from decimal import Decimal as D

from app.repository import funding
from tests import apidata
from tests.apiclient import decimals, total
from tests.factories import offering, product
from tests.test_balance_actions import credit, debit, hold, prepaid, reverse, topup, transfer
from tests.test_number_persistence import assignment, number
from tests.timeline import TODAY

BASE = "/tmf-api/prepayBalanceManagement/v4"


def bucket(api, bucket_id, query=""):
    response = api.get(f"{BASE}/bucket/{bucket_id}{query}")
    assert response.status_code == 200, response.text
    return decimals(response)


def accumulated(api, balance_id):
    response = api.get(f"{BASE}/accumulatedBalance/{balance_id}")
    assert response.status_code == 200, response.text
    return decimals(response)


def accumulated_id(conn, acc):
    return conn.execute(
        "SELECT accumulated_balance_id FROM prepaid_bucket WHERE id = %s", (acc.bucket,)
    ).fetchone()[0]


def current_balance(api, acc):
    response = api.get(f"{apidata.ACCOUNT}/{acc.id}")
    assert response.status_code == 200, response.text
    (entry,) = decimals(response)["accountBalance"]
    assert entry["balanceType"] == "currentBalance"
    return entry["amount"]["value"]


def test_bucket_figures_are_the_funding_ledger(api, db_conn):
    sender, receiver = prepaid(db_conn, "Sender"), prepaid(db_conn, "Receiver")
    topup(db_conn, sender, 100)
    credit(db_conn, sender, 20)
    debit(db_conn, sender, 5)
    transfer(db_conn, sender, receiver, 30)
    reverse(db_conn, sender, topup(db_conn, sender, 10))
    hold(db_conn, sender, 11)
    db_conn.commit()
    snapshots = {acc.id: funding.load(db_conn, acc.id) for acc in (sender, receiver)}
    db_conn.rollback()

    # L = 100 + 20 - 5 - 30 (+ 10 - 10 reversed) = 85, H = 11; the receiver has 30.
    for acc, ledger, held in ((sender, D("85.00"), D("11.00")), (receiver, D("30.00"), D(0))):
        body = bucket(api, acc.bucket)
        assert body["remainingValue"] == {"amount": ledger - held, "units": "USD"}
        assert body["reservedValue"] == {"amount": held, "units": "USD"}
        assert body["remainingValueName"] == f"{ledger - held:.2f} USD"
        snapshot = snapshots[acc.id]
        assert (snapshot.spendable, snapshot.reserved) == (ledger - held, held)
        # TMF666 currentBalance is L: adjustments, transfers and reversals included
        assert current_balance(api, acc) == snapshot.ledger == ledger
        total = accumulated(api, accumulated_id(db_conn, acc))
        assert total["totalBalance"] == body["remainingValue"]
        assert total["bucket"] == [
            {
                "id": str(acc.bucket),
                "href": f"{BASE}/bucket/{acc.bucket}",
                "name": body["name"],
                "@type": "BucketRef",
                "@referredType": "Bucket",
            }
        ]


def test_bucket_describes_its_account_and_follows_its_state(api, db_conn):
    acc = prepaid(db_conn)
    db_conn.commit()
    body = bucket(api, acc.bucket)
    assert body["@type"] == "Bucket" and body["usageType"] == "monetary"
    assert body["status"] == "active" and body["isShared"] is False
    assert body["remainingValue"] == {"amount": D("0.00"), "units": "USD"}
    assert body["partyAccount"] == {
        "id": str(acc.id),
        "href": f"{apidata.ACCOUNT}/{acc.id}",
        "name": acc.row["name"],
        "@type": "PartyAccountRef",
        "@referredType": "BillingAccount",
        "status": "Active",
    }
    (owner,) = body["relatedParty"]
    assert owner["id"] == str(acc.row["customer_id"]) and owner["role"] == "customer"
    assert owner["@referredType"] == "Customer"
    assert body["product"] == [] and body["logicalResource"] == []
    assert set(body["validFor"]) == {"startDateTime"}  # monetary credit does not expire
    for state, status in (("Suspended", "suspended"), ("Closed", "expired")):
        db_conn.execute("UPDATE billing_account SET state = %s WHERE id = %s", (state, acc.id))
        db_conn.commit()
        assert bucket(api, acc.bucket)["status"] == status
        assert bucket(api, acc.bucket)["partyAccount"]["status"] == state


def test_live_products_and_numbers_fund_from_the_bucket(api, db_conn):
    acc = prepaid(db_conn)
    plan = offering(db_conn)
    first = product(db_conn, acc.id, plan["id"], date(2026, 9, 1))
    second = product(db_conn, acc.id, plan["id"], date(2026, 9, 1))
    ended = product(db_conn, acc.id, plan["id"], date(2026, 9, 1))
    db_conn.execute(
        "UPDATE product SET status = 'terminated', termination_date = %s WHERE id = %s",
        (date(2026, 9, 20), ended["id"]),
    )
    one = number(db_conn, "12025550101")
    assignment(db_conn, one, first, date(2026, 9, 1), date(2026, 11, 1))
    # a scheduled number change does not make the product hold two numbers
    assignment(db_conn, number(db_conn, "12025550103"), first, start=date(2026, 11, 1))
    assignment(db_conn, number(db_conn, "12025550102"), ended, date(2026, 9, 1), date(2026, 9, 20))
    db_conn.commit()

    body = bucket(api, acc.bucket)
    # one transaction: equal creation instants, so the id orders them
    assert [p["id"] for p in body["product"]] == sorted([str(first["id"]), str(second["id"])])
    assert next(p for p in body["product"] if p["id"] == str(first["id"])) == {
        "id": str(first["id"]),
        "href": f"{apidata.PRODUCT}/{first['id']}",
        "name": first["name"],
        "@type": "ProductRef",
        "@referredType": "Product",
    }
    (held,) = body["logicalResource"]
    assert held["id"] == str(one) and held["@type"] == "LogicalResourceRef"
    assert held["href"] == f"/tmf-api/resourceInventory/v5/resource/{one}"
    assert body["isShared"] is False
    # v4 accumulatedBalance.logicalResource is one reference: given for exactly one number
    total = accumulated(api, accumulated_id(db_conn, acc))
    assert total["logicalResource"] == held and total["product"] == body["product"]

    assignment(db_conn, number(db_conn, "12025550104"), second, start=TODAY)
    db_conn.commit()
    body = bucket(api, acc.bucket)
    assert len(body["logicalResource"]) == 2 and body["isShared"] is True
    assert "logicalResource" not in accumulated(api, accumulated_id(db_conn, acc))


def test_lists_filter_page_and_select(api, db_conn):
    first, second = prepaid(db_conn, "First"), prepaid(db_conn, "Second")
    postpaid = apidata.account(api)  # a postpaid account has no bucket
    db_conn.execute("UPDATE billing_account SET state = 'Suspended' WHERE id = %s", (second.id,))
    db_conn.commit()

    listed = api.get(f"{BASE}/bucket")
    assert total(listed, api) == "2"
    assert {b["id"] for b in listed.json()} == {str(first.bucket), str(second.bucket)}
    only = api.get(f"{BASE}/bucket?partyAccount.id={second.id}").json()
    assert [b["id"] for b in only] == [str(second.bucket)]
    suspended = api.get(f"{BASE}/bucket?status=suspended&usageType=monetary").json()
    assert [b["id"] for b in suspended] == [str(second.bucket)]
    assert api.get(f"{BASE}/bucket?partyAccount.id={postpaid['id']}").json() == []
    paged = api.get(f"{BASE}/bucket?offset=1&limit=1")
    assert paged.headers["X-Result-Count"] == "1" and total(paged, api) == "2"
    assert api.get(f"{BASE}/bucket?owner=x").status_code == 400

    selected = api.get(f"{BASE}/bucket/{first.bucket}?fields=remainingValue").json()
    assert set(selected) == {"id", "href", "@type", "remainingValue"}
    assert api.get(f"{BASE}/bucket/{first.bucket}?fields=requestedDate").status_code == 400

    totals = api.get(f"{BASE}/accumulatedBalance?partyAccount.id={first.id}").json()
    assert [t["id"] for t in totals] == [str(accumulated_id(db_conn, first))]
    assert total(api.get(f"{BASE}/accumulatedBalance"), api) == "2"


def test_unknown_ids_are_404_and_reads_write_nothing(api, db_conn):
    acc = prepaid(db_conn)
    db_conn.commit()
    other = accumulated_id(db_conn, acc)
    # the two identities are distinct resources
    assert api.get(f"{BASE}/bucket/{other}").status_code == 404
    assert api.get(f"{BASE}/accumulatedBalance/{acc.bucket}").status_code == 404
    assert api.get(f"{BASE}/bucket/{apidata.NIL}").status_code == 404
    assert api.get(f"{BASE}/bucket/not-a-uuid").status_code in (400, 404)
    # an account made prepaid without a bucket row gets none from a read
    bare = apidata.account(api, name="Bare")
    db_conn.execute(
        "UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (bare["id"],)
    )
    db_conn.commit()
    assert api.get(f"{BASE}/bucket?partyAccount.id={bare['id']}").json() == []
    count = db_conn.execute("SELECT count(*) FROM prepaid_bucket").fetchone()[0]
    assert count == 1
