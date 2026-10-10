"""TMF632 individual/organization, TMF629 customer, TMF666 billingAccount (with the
BssBillingAccount extension and the computed balance)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.api import account as account_api
from app.api.common import from_billing_date, to_billing_date
from tests import apidata
from tests.apiclient import decimals
from tests.apidata import ACCOUNT, CUSTOMER, NIL, PARTY

# --- TMF632 ---


def test_individual_round_trip(api):
    person = apidata.individual(api)
    assert person == {
        "id": person["id"],
        "href": f"{PARTY}/individual/{person['id']}",
        "givenName": "Ivan",
        "familyName": "Ivanov",
        "@type": "Individual",
    }
    assert api.get(person["href"]).json() == person
    patched = api.patch(person["href"], {"givenName": "Pyotr"}).json()
    assert patched["givenName"] == "Pyotr" and patched["familyName"] == "Ivanov"


def test_individual_validation(api):
    url = f"{PARTY}/individual"
    assert api.post(url, {"@type": "Individual", "familyName": "Ivanov"}).status_code == 422
    assert api.post(url, {"givenName": "I", "familyName": ""}).status_code == 422
    assert api.post(url, {"givenName": "I", "familyName": "X", "gender": "m"}).status_code == 400
    href = apidata.individual(api)["href"]
    assert api.patch(href, {"familyName": None}).status_code == 422
    assert api.patch(href, {"@type": "Organization"}).status_code == 400


def test_organization_round_trip(api):
    org = apidata.organization(api)
    assert org["name"] == "Romashka LLC" and org["@type"] == "Organization"
    assert api.patch(org["href"], {"name": "Romashka JSC"}).json()["name"] == "Romashka JSC"
    assert api.post(f"{PARTY}/organization", {"tradingName": "x", "name": "y"}).status_code == 400


def test_party_types_are_separate_resources(api):
    person, org = apidata.individual(api), apidata.organization(api)
    assert api.get(f"{PARTY}/organization/{person['id']}").status_code == 404
    assert api.get(f"{PARTY}/individual/{org['id']}").status_code == 404
    assert api.patch(f"{PARTY}/organization/{person['id']}", {"name": "x"}).status_code == 404
    assert [p["id"] for p in api.get(f"{PARTY}/individual").json()] == [person["id"]]
    assert [p["id"] for p in api.get(f"{PARTY}/organization").json()] == [org["id"]]


def test_party_filters(api):
    apidata.individual(api, "Ivan", "Ivanov")
    apidata.individual(api, "Anna", "Ivanova")
    apidata.organization(api, "Ivanov")
    url = f"{PARTY}/individual"
    assert [p["givenName"] for p in api.get(f"{url}?familyName=Ivanov").json()] == ["Ivan"]
    assert [p["familyName"] for p in api.get(f"{url}?givenName=Anna").json()] == ["Ivanova"]
    assert len(api.get(f"{PARTY}/organization?name=Ivanov").json()) == 1
    assert api.get(f"{url}?name=Ivanov").status_code == 400


# --- TMF629 ---


def test_customer_round_trip(api):
    person = apidata.individual(api)
    cust = apidata.customer(api, person)
    assert cust["status"] == "Created"
    assert cust["engagedParty"] == {
        "id": person["id"],
        "href": person["href"],
        "name": "Ivan Ivanov",
        "@type": "PartyRef",
        "@referredType": "Individual",
    }
    assert cust["href"] == f"{CUSTOMER}/{cust['id']}"
    org_customer = apidata.customer(api, apidata.organization(api), name="Romashka")
    assert org_customer["engagedParty"]["@referredType"] == "Organization"
    patched = api.patch(cust["href"], {"status": "Active", "name": "I. Ivanov"}).json()
    assert (patched["status"], patched["name"]) == ("Active", "I. Ivanov")


def test_customer_validation(api):
    person = apidata.individual(api)
    body = apidata.customer_body(person)
    assert api.post(CUSTOMER, {k: v for k, v in body.items() if k != "name"}).status_code == 422
    assert api.post(CUSTOMER, {**body, "status": "Gold"}).status_code == 422
    unknown = apidata.customer_body({**person, "id": NIL})
    assert api.post(CUSTOMER, unknown).status_code == 422
    assert api.post(CUSTOMER, {**body, "creditProfile": []}).status_code == 400
    href = apidata.customer(api, person)["href"]
    other = apidata.individual(api, "Anna", "Petrova")
    assert api.patch(href, {"engagedParty": {"id": other["id"]}}).status_code == 400
    assert api.patch(href, {"status": None}).status_code == 422
    assert len(api.get(CUSTOMER).json()) == 1


@pytest.mark.parametrize("status", ["Gold", "active", "Rejected", ""])
def test_customer_status_outside_the_enum_is_refused_on_patch(api, status):
    """TMF629 leaves Customer.status a free string; ours is Created, Active, Inactive, and a
    PATCH with anything else is a 422 that changes nothing (not a 500 from the db enum)."""
    href = apidata.customer(api)["href"]
    response = api.patch(href, {"status": status, "name": "Changed"})
    assert response.status_code == 422, response.text
    assert api.get(href).json()["status"] == "Created"
    assert api.get(href).json()["name"] == "Ivan Ivanov"


def test_customer_filters(api):
    ivan, anna = apidata.individual(api), apidata.individual(api, "Anna", "Petrova")
    apidata.customer(api, ivan)
    apidata.customer(api, anna, name="Anna", status="Active")
    assert [c["name"] for c in api.get(f"{CUSTOMER}?status=Active").json()] == ["Anna"]
    by_party = api.get(f"{CUSTOMER}?engagedParty.id={ivan['id']}").json()
    assert [c["name"] for c in by_party] == ["Ivan Ivanov"]


# --- TMF666 ---


def test_billing_account_with_extension_and_balance(api):
    cust = apidata.customer(api)
    created = api.post(ACCOUNT, apidata.account_body(cust, billCycleDay=15, currency="EUR"))
    assert created.status_code == 201
    acc = created.json()
    assert acc["@type"] == "BssBillingAccount"
    assert acc["@baseType"] == "BillingAccount"
    assert acc["@schemaLocation"] == "/tmf-api/schema/BssBillingAccount.schema.json"
    assert (acc["billCycleDay"], acc["currency"], acc["state"]) == (15, "EUR", "Active")
    assert acc["relatedParty"] == [
        {
            "role": "customer",
            "partyOrPartyRole": {
                "id": cust["id"],
                "href": cust["href"],
                "name": cust["name"],
                "@type": "PartyRoleRef",
                "@referredType": "Customer",
            },
            "@type": "RelatedPartyRefOrPartyRoleRef",
        }
    ]
    assert (
        '"accountBalance":[{"balanceType":"currentBalance","amount":{"unit":"EUR","value":0.00}'
        in created.text
    )


def test_the_extension_schema_is_served_and_valid(api):
    response = api.get(account_api.SCHEMA_LOCATION)  # the client runs check_schema on it
    assert response.status_code == 200
    schema = response.json()
    assert schema["required"] == ["@type", "@baseType", "billCycleDay", "currency"]
    assert schema["properties"]["billCycleDay"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 28,
        "description": "Day of month on which the account's billing period starts.",
    }


def test_balance_is_payments_minus_charges_positive_means_credit(api):
    acc = apidata.account(api)
    apidata.payment(api, acc, 1000.50)
    apidata.payment(api, acc, 499.5)
    balance = decimals(api.get(acc["href"]))["accountBalance"]
    assert balance == [
        {
            "balanceType": "currentBalance",
            "amount": {"unit": "RUB", "value": Decimal("1500.00")},
            "@type": "AccountBalance",
        }
    ]


@pytest.mark.parametrize(
    ("override", "status"),
    [
        ({"billCycleDay": 29}, 422),
        ({"billCycleDay": 0}, 422),
        ({"billCycleDay": "5"}, 422),
        ({"billCycleDay": 5.0}, 422),
        ({"billCycleDay": None}, 422),
        ({"currency": "rub"}, 422),
        ({"currency": None}, 422),
        ({"state": "Open"}, 422),
        ({"relatedParty": []}, 422),
        ({"accountBalance": []}, 400),
        ({"paymentStatus": "due"}, 400),  # a TMF666 attribute we do not support
    ],
)
def test_billing_account_validation(api, override, status):
    body = {k: v for k, v in apidata.account_body(apidata.customer(api)).items()}
    for key, value in override.items():
        if value is None:
            body.pop(key)
        else:
            body[key] = value
    assert api.post(ACCOUNT, body).status_code == status
    assert api.get(ACCOUNT).json() == []


def test_billing_account_opened_at_comes_from_the_injected_clock(api, db_conn):
    """A billing run for D covers the accounts opened before 00:00 of D (stage 4), so the
    time must be the injected clock's, not the database's now() - and it stays internal."""
    opened = datetime(2026, 3, 14, 20, 59, 59, tzinfo=UTC)  # 23:59:59 in Moscow
    api.clock.set(opened)
    body = apidata.account(api)
    row = db_conn.execute(
        "SELECT opened_at, created_at FROM billing_account WHERE id = %s", (body["id"],)
    ).fetchone()
    assert row[0] == opened
    assert row[1] != opened  # created_at is the real now(), months away from the clock
    assert "openedAt" not in body and "opened_at" not in body


def test_billing_account_needs_one_existing_customer(api):
    cust = apidata.customer(api)
    body = apidata.account_body(cust)
    body["relatedParty"][0]["partyOrPartyRole"]["id"] = NIL
    assert api.post(ACCOUNT, body).status_code == 422
    two = apidata.account_body(cust)
    two["relatedParty"] *= 2
    assert api.post(ACCOUNT, two).status_code == 422


@pytest.mark.parametrize("role", [None, "owner", "Customer", "customer ", ""])
def test_billing_account_party_role_must_be_customer(api, role):
    """doc-findings #3: the one related party must carry role "customer" - a missing or
    other role is a 422 and nothing is stored, never answered back as "customer"."""
    body = apidata.account_body(apidata.customer(api))
    if role is None:
        del body["relatedParty"][0]["role"]
        sent = "no role"
    else:
        body["relatedParty"][0]["role"] = role
        sent = f"role {role!r}"
    response = api.post(ACCOUNT, body)
    assert response.status_code == 422, response.text
    assert response.json()["message"] == (
        f'relatedParty: exactly one party with role "customer" is required, not {sent}'
    )
    assert api.get(ACCOUNT).json() == []


def test_billing_account_answers_the_role_it_was_given(api):
    acc = apidata.account(api)
    assert [p["role"] for p in acc["relatedParty"]] == ["customer"]
    assert [p["role"] for p in api.get(acc["href"]).json()["relatedParty"]] == ["customer"]


def test_billing_account_patch(api):
    acc = apidata.account(api)
    patched = api.patch(acc["href"], {"state": "Suspended"})
    assert patched.status_code == 200
    assert patched.json()["state"] == "Suspended"
    for day in (20, 1, None):
        response = api.patch(acc["href"], {"billCycleDay": day})
        assert response.status_code == 409, day
        assert "billCycleDay cannot be changed yet" in response.json()["message"]
    assert api.get(acc["href"]).json()["billCycleDay"] == 1
    for read_only in ("currency", "accountBalance", "relatedParty", "lastUpdate"):
        response = api.patch(acc["href"], {read_only: "x"})
        assert response.status_code == 400, read_only
        assert f"{read_only} is read-only" in response.json()["message"]
    assert api.patch(acc["href"], {"@type": "BillingAccount", "name": "B"}).status_code == 200
    assert api.patch(acc["href"], {"@type": "PartyAccount"}).status_code == 400
    assert api.get(acc["href"]).json()["currency"] == "RUB"


def test_billing_account_filters_and_fields(api):
    ivan = apidata.customer(api)
    anna = apidata.customer(api, apidata.individual(api, "Anna", "Petrova"), name="Anna")
    apidata.account(api, ivan, name="Ivan main")
    apidata.account(api, anna, name="Anna main", state="Suspended")
    assert [a["name"] for a in api.get(f"{ACCOUNT}?state=Suspended").json()] == ["Anna main"]
    by_customer = api.get(f"{ACCOUNT}?relatedParty.id={ivan['id']}").json()
    assert [a["name"] for a in by_customer] == ["Ivan main"]
    slim = api.get(f"{ACCOUNT}?fields=billCycleDay").json()
    assert all(set(a) == {"id", "href", "@type", "billCycleDay"} for a in slim)


def test_the_client_checks_the_extension_schema(api, monkeypatch):
    """Negative proof: a body that satisfies TMF666 but not BssBillingAccount fails."""
    original = account_api.account_body

    def cycle_day_31(conn, row):
        return {**original(conn, row), "billCycleDay": 31}

    monkeypatch.setattr(account_api, "account_body", cycle_day_31)
    with pytest.raises(AssertionError, match="BssBillingAccount.schema.json"):
        apidata.account(api)


# --- dates in BILLING_TZ ---


def test_date_times_become_dates_in_the_billing_timezone():
    assert to_billing_date(datetime(2026, 10, 1, 23, 30, tzinfo=UTC)) == date(2026, 10, 2)
    assert to_billing_date(datetime(2026, 10, 1, 20, 59, tzinfo=UTC)) == date(2026, 10, 1)
    assert from_billing_date(date(2026, 10, 2)) == datetime(2026, 10, 1, 21, 0, tzinfo=UTC)
