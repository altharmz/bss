"""P5a: TMF666 accepts `ratingType: prepaid`; TMF622/637/678 references match the mode;
the first-charge preview states required, available and shortfall."""

from tests import apidata
from tests.apidata import ACCOUNT, ORDER

BUCKETS = "/tmf-api/prepayBalanceManagement/v4/bucket"
PRODUCT = "/tmf-api/productInventory/v5/product"
ON_DEMAND = "/tmf-api/customerBillManagement/v5/customerBillOnDemand"
INTERNAL = "/internal/v1/billingAccount"
TOMORROW = "2026-10-02"


def prepaid_account(api, **over):
    return apidata.account(api, **{"ratingType": "prepaid", **over})


def buckets_of(api, acc):
    response = api.get(f"{BUCKETS}?partyAccount.id={acc['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def test_post_prepaid_creates_the_account_with_its_one_bucket(api):
    acc = prepaid_account(api)
    assert acc["ratingType"] == "prepaid"
    assert api.get(acc["href"]).json()["ratingType"] == "prepaid"
    assert len(buckets_of(api, acc)) == 1
    assert apidata.account(api)["ratingType"] == "postpaid"  # the default
    other = apidata.account(api)
    assert buckets_of(api, other) == []


def test_a_positive_credit_limit_is_refused_on_a_prepaid_account(api):
    cust = apidata.customer(api)
    body = apidata.account_body(cust, ratingType="prepaid")
    positive = {**body, "creditLimit": {"unit": "USD", "value": 1}}
    assert api.post(ACCOUNT, positive).status_code == 422
    zero = {**body, "creditLimit": {"unit": "USD", "value": 0}}
    assert api.post(ACCOUNT, zero).status_code == 201
    acc = api.post(ACCOUNT, body).json()
    response = api.patch(acc["href"], {"creditLimit": {"unit": "USD", "value": 5}})
    assert response.status_code == 422
    assert api.patch(acc["href"], {"creditLimit": {"unit": "USD", "value": 0}}).status_code == 200


def test_patch_switches_the_mode_only_before_history(api):
    acc = apidata.account(api)
    response = api.patch(acc["href"], {"ratingType": "prepaid"})
    assert response.status_code == 200, response.text
    assert response.json()["ratingType"] == "prepaid"
    assert len(buckets_of(api, acc)) == 1
    # a prepaid account always has its bucket: no way back
    response = api.patch(acc["href"], {"ratingType": "postpaid"})
    assert response.status_code == 409
    assert "history" in response.json()["message"]
    assert api.patch(acc["href"], {"ratingType": "prepaid"}).status_code == 200  # a repeat
    assert api.patch(acc["href"], {"ratingType": None}).status_code == 422

    used = apidata.account(api)
    offering = apidata.sellable_offering(api)
    apidata.order(api, used, apidata.add_item(offering))
    assert api.patch(used["href"], {"ratingType": "prepaid"}).status_code == 409
    assert api.get(used["href"]).json()["ratingType"] == "postpaid"


def test_a_positive_credit_limit_blocks_the_switch(api):
    cust = apidata.customer(api)
    limited = api.post(
        ACCOUNT, apidata.account_body(cust, creditLimit={"unit": "USD", "value": 50})
    ).json()
    assert api.patch(limited["href"], {"ratingType": "prepaid"}).status_code == 422
    both = {"ratingType": "prepaid", "creditLimit": None}
    assert api.patch(limited["href"], both).status_code == 200


def test_order_and_product_references_must_match_the_mode(api):
    prepaid = prepaid_account(api)
    postpaid = apidata.account(api)
    offering = apidata.sellable_offering(api)
    for acc, wrong in ((prepaid, "postpaid"), (postpaid, "prepaid")):
        ref = {"id": acc["id"], "@type": "BillingAccountRef", "ratingType": wrong}
        body = apidata.order_body(acc, apidata.add_item(offering), billingAccount=ref)
        assert api.post(ORDER, body).status_code == 422, wrong
        item = apidata.add_item(offering, billingAccount=ref)
        item_body = apidata.order_body(acc, item)
        assert api.post(ORDER, item_body).status_code == 422, wrong
        product = {
            "@type": "Product",
            "productOffering": {"id": offering["id"], "@type": "ProductOfferingRef"},
            "billingAccount": ref,
        }
        assert api.post(PRODUCT, product).status_code == 422, wrong
        demand = {"@type": "CustomerBillOnDemand", "billingAccount": ref}
        assert api.post(ON_DEMAND, demand).status_code == 422, wrong
    right = {"id": postpaid["id"], "@type": "BillingAccountRef", "ratingType": "postpaid"}
    body = apidata.order_body(postpaid, apidata.add_item(offering), billingAccount=right)
    assert api.post(ORDER, body).status_code == 201


def test_references_to_a_prepaid_account_state_its_mode(api):
    acc = prepaid_account(api)
    offering = apidata.sellable_offering(api)
    ref = {"id": acc["id"], "@type": "BillingAccountRef", "ratingType": "prepaid"}
    body = apidata.order_body(acc, apidata.add_item(offering), billingAccount=ref)
    response = api.post(ORDER, body)
    # no funds: a business refusal stored on a rejected order, never a mode mismatch
    assert response.status_code == 201, response.text
    assert response.json()["billingAccount"]["ratingType"] == "prepaid"
    assert response.json()["state"] == "rejected"


def first_charge(api, acc, offering, start):
    return api.get(
        f"{INTERNAL}/{acc['id']}/firstCharge?productOffering.id={offering['id']}&startDate={start}"
    )


def test_first_charge_preview_of_a_prepaid_account_shows_the_funding(api):
    acc = prepaid_account(api)
    offering = apidata.sellable_offering(
        api, value=100, tax=[{"@type": "TaxItem", "taxCategory": "VAT", "taxRate": 20}]
    )
    today = TOMORROW  # prices are valid from their creation, not from midnight
    body = first_charge(api, acc, offering, today).json()
    assert body["required"]["unit"] == "USD"
    assert float(body["required"]["value"]) == 120
    assert float(body["available"]["value"]) == 0
    assert float(body["shortfall"]["value"]) == 120
    assert body["exceedsCreditLimit"] is True
    assert body["prorated"] is False

    apidata.payment(api, acc, 70)
    body = first_charge(api, acc, offering, today).json()
    assert float(body["available"]["value"]) == 70
    assert float(body["shortfall"]["value"]) == 50
    apidata.payment(api, acc, 100)
    body = first_charge(api, acc, offering, today).json()
    assert float(body["shortfall"]["value"]) == 0
    assert body["exceedsCreditLimit"] is False
    assert float(body["balanceAfter"]["value"]) == 50


def test_first_charge_preview_of_a_postpaid_account_has_no_funding_fields(api):
    acc = apidata.account(api)
    offering = apidata.sellable_offering(api)
    today = TOMORROW  # prices are valid from their creation, not from midnight
    body = first_charge(api, acc, offering, today).json()
    assert not {"required", "available", "shortfall"} & set(body)
