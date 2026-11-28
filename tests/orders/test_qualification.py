"""TMF679 product offering qualification (#429, docs/decisions/qualification.md): a check
answers with exactly the reasons the same order would be rejected for, changes nothing, and
is stored as answered; a query lists the offerings an account can get."""

import ast
import json
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from uuid import UUID

import pytest

from app.network import device
from app.ordering import qualification as engine
from app.repository import funding
from tests import apidata, timeline
from tests.apidata import ORDER
from tests.builders.prepaid.prepaid_orders import shop
from tests.builders.resources.api_resource_inventory import number
from tests.builders.resources.number_orders import refs
from tests.builders.tmf.events import stored, subscribe

BASE = "/tmf-api/productOfferingQualification/v5"
CHECK = f"{BASE}/checkProductOfferingQualification"
QUERY = f"{BASE}/queryProductOfferingQualification"


def _imei(tac: str) -> str:
    """tac + serial + the Luhn check digit (the stub refuses TAC 99000000-99000099)."""
    body = tac + "123456"
    return next(body + d for d in "0123456789" if device.valid(body + d))


INCOMPATIBLE = _imei("99000001")
WRITTEN = (
    "product_order",
    "product",
    "number_assignment",
    "funding_hold",
    "prepaid_command",
    "network_command",
    "event",
)


def sellable(api, **over):
    """apidata.sellable_offering (valid from START on), then the clock on START: a price
    cannot start in the past, an order cannot use an offering before it is valid."""
    api.clock.set(timeline.NOW)
    made = apidata.sellable_offering(api, **over)
    api.clock.set(timeline.START + timedelta(hours=9))
    return made


def account_ref(acc):
    return {"id": acc["id"], "@type": "BillingAccountRef"}


def check_item(order_item, acc=None):
    """A TMF622 order item as a TMF679 check item; `acc` names the billing account."""
    item = {k: v for k, v in order_item.items() if k not in ("@type", "billingAccount")}
    item["@type"] = "CheckProductOfferingQualificationItem"
    if acc is not None:
        item["product"] = {
            "@type": "Product",
            **item.get("product", {}),
            "billingAccount": account_ref(acc),
        }
    return item


def check(api, *items, status=201, **over):
    body = {
        "@type": "CheckProductOfferingQualification",
        "checkProductOfferingQualificationItem": list(items),
        **over,
    }
    response = api.post(CHECK, body)
    assert response.status_code == status, response.text
    return response.json()


def reasons(item):
    return [r["code"] for r in item.get("eligibilityResultReason", [])]


def order_codes(order):
    return sorted(e["code"] for e in order.get("productOrderErrorMessage", []))


def check_codes(answer):
    return sorted(
        {code for item in answer["checkProductOfferingQualificationItem"] for code in reasons(item)}
    )


def counts(conn):
    conn.commit()
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in WRITTEN}  # noqa: S608


def test_a_qualified_check_is_done_stored_and_changes_nothing(api, db_conn):
    offering = sellable(api)
    acc = apidata.account(api)
    before = counts(db_conn)

    answer = check(api, check_item(apidata.add_item(offering), acc))

    assert answer["state"] == "done" and answer["instantSyncQualification"] is True
    assert answer["qualificationResult"] == "qualified", answer
    (item,) = answer["checkProductOfferingQualificationItem"]
    assert item["qualificationItemResult"] == "qualified" and item["state"] == "done"
    assert "eligibilityResultReason" not in item
    assert item["productOffering"]["id"] == offering["id"]
    assert item["product"]["billingAccount"]["id"] == acc["id"]
    assert answer["expirationDate"] > answer["creationDate"]
    assert counts(db_conn) == before  # no order, product, claim, hold, command or event
    assert api.get(answer["href"]).json() == answer
    listed = api.get(f"{CHECK}?billingAccount.id={acc['id']}").json()
    assert [q["id"] for q in listed] == [answer["id"]]
    assert api.get(f"{QUERY}/{answer['id']}").status_code == 404  # a check is no query


def test_an_unqualified_item_carries_the_order_engines_reasons(api, db_conn):
    offering = sellable(api)
    acc = apidata.account(api)
    other = apidata.account(api, name="Other")
    items = [
        check_item(apidata.add_item(offering, "1"), acc),
        check_item(apidata.add_item(offering, "2"), other),  # another account than the order's
    ]
    order = api.post(
        ORDER,
        apidata.order_body(
            acc,
            apidata.add_item(offering, "1"),
            apidata.rejected_item(api, offering, "2") | {"billingAccount": account_ref(other)},
        ),
    ).json()

    answer = check(api, *items)

    assert answer["qualificationResult"] == "unqualified"
    first, second = answer["checkProductOfferingQualificationItem"]
    assert first["qualificationItemResult"] == "qualified"
    assert second["qualificationItemResult"] == "unqualified"
    assert second["eligibilityResultReason"] == [
        {
            "code": "billingAccountMismatch",
            "label": "the item's billingAccount differs from the order's",
            "@type": "EligibilityResultReason",
        }
    ]
    assert order_codes(order) == check_codes(answer) == ["billingAccountMismatch"]


def test_a_reason_against_the_whole_order_disqualifies_every_item(api):
    offering = sellable(api)
    acc = apidata.account(api)
    api.clock.set(timeline.START + timedelta(days=3, hours=9))
    yesterday = timeline.iso(timeline.START + timedelta(days=2))
    items = [
        check_item(apidata.add_item(offering, str(n)), acc) | {"expectedActivationDate": yesterday}
        for n in (1, 2)
    ]
    order = apidata.order(
        api, acc, apidata.add_item(offering, "1"), apidata.add_item(offering, "2"),
        requestedStartDate=yesterday,
    )  # fmt: skip

    answer = check(api, *items)

    assert [reasons(i) for i in answer["checkProductOfferingQualificationItem"]] == [
        ["requestedStartDateInPast"],
        ["requestedStartDateInPast"],
    ]
    assert order_codes(order) == ["requestedStartDateInPast"]


def test_a_taken_number_is_unavailable_as_in_the_order(api, db_conn):
    offering = sellable(api)
    response = api.patch(
        offering["productSpecification"]["href"],
        {"resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]},
    )
    assert response.status_code == 200, response.text
    api.clock.set(timeline.START + timedelta(hours=9))
    acc = apidata.account(api)
    uid = number(api, "12025550123")["id"]

    def item():
        line = apidata.add_item(offering)
        line["product"] = {"@type": "ProductRefOrValue", "realizingResource": refs(uid)}
        return line

    assert check(api, check_item(item(), acc))["qualificationResult"] == "qualified"
    assert apidata.order(api, acc, item())["state"] == "completed"
    second = apidata.order(api, acc, item())
    answer = check(api, check_item(item(), acc))
    assert answer["qualificationResult"] == "unqualified"
    assert check_codes(answer) == order_codes(second) == ["numberUnavailable"]


def test_prepaid_funds_are_checked_with_tax_and_nothing_is_reserved(api, db_conn):
    off, acc = shop(api, db_conn, funds="23.99")  # two lines of 10 + 20% tax need 24
    lines = []
    for n in range(2):
        line = apidata.add_item(off, str(n + 1))
        line["product"] = {
            "@type": "ProductRefOrValue",
            "realizingResource": refs(number(api, value=f"7999123400{n}")["id"]),
        }
        lines.append(line)
    before = counts(db_conn)

    answer = check(api, *(check_item(line, acc) for line in lines))

    assert check_codes(answer) == ["insufficientFunds"]
    assert counts(db_conn) == before
    assert funding.load(db_conn, UUID(acc["id"])).spendable == D("23.99")
    db_conn.commit()  # funding.load locks the account
    order = api.post(ORDER, apidata.order_body(acc, *lines)).json()
    assert order_codes(order) == ["insufficientFunds"]


def test_an_incompatible_device_is_a_reason(api, db_conn):
    off, acc = shop(api, db_conn, funds="100")
    item = apidata.add_item(off)
    item["product"] = {
        "@type": "ProductRefOrValue",
        "realizingResource": refs(number(api, value="79991234500")["id"]),
        "productCharacteristic": [
            {"@type": "StringCharacteristic", "name": "imei", "value": INCOMPATIBLE}
        ],
    }

    answer = check(api, check_item(item, acc))
    order = api.post(ORDER, apidata.order_body(acc, item)).json()

    assert check_codes(answer) == order_codes(order)
    assert check_codes(answer) in (["deviceIncompatible"], ["deviceNotApplicable"])


def test_the_flags_shape_the_answer_and_are_kept(api):
    offering = sellable(api)
    acc = apidata.account(api)
    other = apidata.account(api, name="Other")
    items = [
        check_item(apidata.add_item(offering, "1"), acc),
        check_item(apidata.add_item(offering, "2"), other),
    ]

    answer = check(api, *items, provideResultReason=False, provideOnlyAvailable=True,
                   provideAlternative=True)  # fmt: skip

    assert answer["qualificationResult"] == "unqualified"
    assert [i["id"] for i in answer["checkProductOfferingQualificationItem"]] == ["1"]
    assert answer["provideAlternative"] is True and answer["provideResultReason"] is False
    hidden = check(api, *items, provideResultReason=False)
    assert all("eligibilityResultReason" not in i for i in hidden[
        "checkProductOfferingQualificationItem"])  # fmt: skip


@pytest.mark.parametrize(
    ("change", "status", "message"),
    [
        (lambda b: b["checkProductOfferingQualificationItem"][0].pop("product"), 422,
         "a qualification needs a billing account"),
        (lambda b: b.update(instantSyncQualification=False), 422, "set by the server"),
        (lambda b: b.update(zzProbe=1), 400, "zzProbe"),
        (lambda b: b["checkProductOfferingQualificationItem"][0].update(note=[]), 400, "note"),
        (lambda b: b["checkProductOfferingQualificationItem"].append(
            dict(b["checkProductOfferingQualificationItem"][0])), 422, "unique"),
    ],
)  # fmt: skip
def test_malformed_checks_are_refused_and_nothing_is_stored(api, db_conn, change, status, message):
    offering = sellable(api)
    acc = apidata.account(api)
    body = {
        "checkProductOfferingQualificationItem": [check_item(apidata.add_item(offering), acc)],
        "instantSyncQualification": True,
    }
    change(body)
    response = api.post(CHECK, body)
    assert response.status_code == status, response.text
    assert message in response.text
    assert db_conn.execute("SELECT count(*) FROM product_offering_qualification").fetchone() == (0,)


def test_items_are_one_order_with_one_start_date(api):
    offering = sellable(api)
    acc = apidata.account(api)
    items = [
        check_item(apidata.add_item(offering, str(n)), acc)
        | {"expectedActivationDate": timeline.iso(timeline.START + timedelta(days=n))}
        for n in (1, 2)
    ]
    response = api.post(CHECK, {"checkProductOfferingQualificationItem": items})
    assert response.status_code == 422 and "one date" in response.text


def test_patch_changes_only_what_describes_it_and_delete_removes_it(api, db_conn):
    offering = sellable(api)
    acc = apidata.account(api)
    subscribe(api, "TMF679")
    answer = check(api, check_item(apidata.add_item(offering), acc), description="before")

    patched = api.patch(
        answer["href"], {"description": "after", "note": [{"text": "n", "@type": "Note"}]}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["description"] == "after"
    assert patched.json()["checkProductOfferingQualificationItem"] == answer[
        "checkProductOfferingQualificationItem"]  # fmt: skip
    for name, value in (("qualificationResult", "unqualified"), ("provideResultReason", False)):
        refused = api.patch(answer["href"], {name: value})
        assert refused.status_code == 400 and "read-only" in refused.text

    assert api.delete(answer["href"]).status_code == 204
    assert api.get(answer["href"]).status_code == 404
    assert api.delete(answer["href"]).status_code == 404
    db_conn.commit()
    assert [t for t, _ in stored(db_conn, "TMF679")] == [
        "CheckProductOfferingQualificationCreateEvent",
        "CheckProductOfferingQualificationAttributeValueChangeEvent",
        "CheckProductOfferingQualificationDeleteEvent",
    ]


def query(api, acc, status=201, **criteria):
    body = {
        "@type": "QueryProductOfferingQualification",
        "searchCriteria": {
            "@type": "QueryProductOfferingQualificationItem",
            "product": {"@type": "Product", "billingAccount": account_ref(acc)},
            **criteria,
        },
    }
    response = api.post(QUERY, body)
    assert response.status_code == status, response.text
    return response.json()


def offered(answer):
    return [i["productOffering"]["name"] for i in answer["qualifiedProductOfferingItem"]]


def test_a_query_lists_the_offerings_the_account_can_get(api, db_conn):
    sellable(api, name="Fiber 100")
    sellable(api, name="Fiber 500")
    euro = sellable(api, name="Fiber EUR", unit="EUR")  # no USD price
    acc = apidata.account(api)
    before = counts(db_conn)

    answer = query(api, acc)

    assert answer["state"] == "done"
    assert offered(answer) == ["Fiber 100", "Fiber 500"]
    assert answer["searchCriteria"]["product"]["billingAccount"]["id"] == acc["id"]
    assert counts(db_conn) == before
    one = query(api, acc, productOffering={"id": euro["id"], "@type": "ProductOfferingRef"})
    assert offered(one) == []
    assert api.get(answer["href"]).json() == answer


def test_a_query_checks_prepaid_funds_where_no_input_is_missing(api, db_conn):
    off, poor = shop(api, db_conn, funds="11.99", numbers=False)  # one line: 10 + 20% tax
    assert offered(query(api, poor)) == []
    api.clock.set(timeline.NOW)
    _, rich = shop(api, db_conn, funds="12", numbers=False)
    assert offered(query(api, rich)) == [off["name"], off["name"]]  # both cost 12


def test_a_query_lists_an_offering_whose_only_reason_is_a_missing_number(api, db_conn):
    """The engine checks funds only once the line has its number: a query, which has none,
    lists the plan; the check with the number gives the full answer."""
    off, acc = shop(api, db_conn, funds="0")
    assert offered(query(api, acc)) == [off["name"]]
    line = apidata.add_item(off)
    line["product"] = {
        "@type": "ProductRefOrValue",
        "realizingResource": refs(number(api, value="79991234000")["id"]),
    }
    assert check_codes(check(api, check_item(line, acc))) == ["insufficientFunds"]


def test_a_query_names_its_account(api):
    response = api.post(QUERY, {"searchCriteria": {"product": {"@type": "Product"}}})
    assert response.status_code == 422 and "billingAccount" in response.text


def test_every_order_refusal_is_a_qualification_reason():
    """The qualification's reasons are the order engine's (a dry run of it): every refusal
    code an order can carry is one, except those listed in ORDER_ONLY with their reason."""
    root = Path(__file__).resolve().parents[2]
    codes = set(json.loads((root / "frontend/src/api/orderErrors.codes.json").read_text()))
    assert set(engine.ORDER_ONLY) <= codes
    assert engine.NEEDS_INPUT <= codes - set(engine.ORDER_ONLY)
    qualification_reasons = codes - set(engine.ORDER_ONLY)
    assert len(qualification_reasons) == len(codes) - 5
    # the dry run is the order engine: place_order inside a savepoint, nothing of its own
    tree = ast.parse((root / "app/ordering/qualification.py").read_text())
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "place_order" in called
