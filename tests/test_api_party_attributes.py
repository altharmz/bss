"""TMF632/629 attributes (docs/decisions/tmf-api.md, "Party and customer attributes"):
every attribute the specs define for individuals, organizations and customers is taken,
stored and returned. The descriptive ones are documents to us, checked against the spec
schema at every depth (app/api/specschema.py); a party's status is a record only."""

import copy
from typing import Any

import pytest

from app.api import customer, party, specschema
from tests import apidata, contract
from tests.apidata import CUSTOMER, PARTY
from tests.test_events import stored, subscribe

INDIVIDUAL = f"{PARTY}/individual"
ORGANIZATION = f"{PARTY}/organization"

CONTACT_MEDIA = [
    {
        "@type": "EmailContactMedium",
        "preferred": True,
        "contactType": "work",
        "emailAddress": "jane@example.org",
    },
    {"@type": "PhoneContactMedium", "phoneNumber": "+7 495 000 00 00"},
    {
        "@type": "GeographicAddressContactMedium",
        "city": "Moscow",
        "country": "Russia",
        "postCode": "101000",
        "street1": "Tverskaya 1",
        "validFor": {"startDateTime": "2026-01-01T00:00:00Z"},
    },
]

RICH_INDIVIDUAL = {
    "@type": "Individual",
    "givenName": "Jane",
    "familyName": "Lamborgizzia",
    "status": "validated",
    "gender": "female",
    "maritalStatus": "divorced",
    "birthDate": "1967-09-26T05:00:00Z",
    "nationality": "American",
    "formattedName": "Jane Smith ep Lamborgizzia",
    "contactMedium": CONTACT_MEDIA,
    "externalReference": [
        {"@type": "ExternalIdentifier", "externalIdentifierType": "crmId", "id": "C-17"}
    ],
    "partyCharacteristic": [
        {"@type": "StringArrayCharacteristic", "name": "hobby", "value": ["Jazz", "Cinema"]},
        {"@type": "IntegerCharacteristic", "name": "children", "value": 1},
        {"@type": "BooleanCharacteristic", "name": "vip", "value": False},
        {"@type": "ObjectCharacteristic", "name": "extra", "value": {"any": ["thing", 1]}},
    ],
    "individualIdentification": [
        {
            "@type": "IndividualIdentification",
            "identificationType": "passport",
            "identificationId": "51DD1234",
            "validFor": {"startDateTime": "2017-10-22T07:49:25Z"},
        }
    ],
    # LanguageAbility is not Extensible: no @type
    "languageAbility": [{"languageCode": "en", "isFavouriteLanguage": True}],
}


def _without_ids(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in ("id", "href")}


def test_every_spec_attribute_has_a_place():
    """The documents are exactly the spec's attributes minus those with behaviour, in
    both the create (_FVO) and the patch (_MVO) schema."""
    for api_name, schema, taken in (
        ("TMF632", "Individual", {"givenName", "familyName", "status"}),
        ("TMF632", "Organization", {"name", "status"}),
        ("TMF629", "Customer", {"name", "status", "engagedParty", "account"}),
    ):
        documents = {
            "Individual": party.INDIVIDUAL_DOCUMENTS,
            "Organization": party.ORGANIZATION_DOCUMENTS,
            "Customer": customer.DOCUMENTS,
        }[schema]
        assert len(set(documents)) == len(documents)
        for suffix in ("_FVO", "_MVO"):
            names = set(specschema.properties(api_name, schema + suffix))
            names -= {"id", "href", "@type", "@baseType", "@schemaLocation"}
            assert names == taken | set(documents), (schema, suffix)


def test_individual_round_trip(api):
    response = api.post(INDIVIDUAL, RICH_INDIVIDUAL)
    assert response.status_code == 201, response.text
    person = response.json()
    assert _without_ids(person) == RICH_INDIVIDUAL
    assert api.get(person["href"]).json() == person
    assert api.get(f"{person['href']}?fields=contactMedium").json() == {
        "id": person["id"],
        "href": person["href"],
        "@type": "Individual",
        "contactMedium": CONTACT_MEDIA,
    }
    assert [p["id"] for p in api.get(f"{INDIVIDUAL}?status=validated").json()] == [person["id"]]


def test_organization_round_trip(api):
    body = {
        "@type": "Organization",
        "name": "Romashka LLC",
        "status": "initialized",
        "tradingName": "Romashka",
        "isLegalEntity": True,
        "isHeadOffice": False,
        "organizationType": "company",
        "existsDuring": {"startDateTime": "2001-02-03T00:00:00Z"},
        "organizationIdentification": [
            {
                "@type": "OrganizationIdentification",
                "identificationType": "INN",
                "identificationId": "7701",
            }
        ],
        "otherName": [{"@type": "OtherNameOrganization", "tradingName": "Daisy"}],
        "contactMedium": CONTACT_MEDIA[:1],
    }
    response = api.post(ORGANIZATION, body)
    assert response.status_code == 201, response.text
    org = response.json()
    assert _without_ids(org) == body
    assert api.get(org["href"]).json() == org


def test_the_spec_s_customer_example_is_taken(api):
    """TMF629's own Customer_Create_example (engagedParty pointed at a party of ours:
    ids are UUIDs here) - contact media of two subclasses and a related party."""
    example = contract.spec("TMF629")["components"]["examples"]["Customer_Create_example_request"]
    body = copy.deepcopy(example["value"])
    org = apidata.organization(api)
    body["engagedParty"] |= {"id": org["id"], "href": org["href"]}
    response = api.post(CUSTOMER, body)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["contactMedium"] == body["contactMedium"]
    assert created["relatedParty"] == body["relatedParty"]  # stored as sent, not resolved
    assert api.get(created["href"]).json() == created


def test_customer_documents_and_account(api):
    person = apidata.individual(api)
    body = apidata.customer_body(
        person,
        description="Our best customer",
        role="residential",
        statusReason="KYC done",
        validFor={"startDateTime": "2026-01-01T03:00:00+03:00"},
        creditProfile=[
            {
                "@type": "CreditProfile",
                "creditProfileDate": "2026-01-01T00:00:00Z",
                "creditRiskRating": 3,
                "validFor": {"startDateTime": "2026-01-01T00:00:00Z"},
            }
        ],
        paymentMethod=[{"@type": "PaymentMethodRef", "id": "pm-1"}],
        account=[],
    )
    response = api.post(CUSTOMER, body)
    assert response.status_code == 201, response.text
    cust = response.json()
    # date-times go out in UTC, as everywhere
    assert cust["validFor"] == {"startDateTime": "2026-01-01T00:00:00Z"}
    assert cust["creditProfile"] == body["creditProfile"] and cust["account"] == []
    # `account` is the billing accounts' side of the link: only the list we answer
    acc = apidata.account(api, cust)
    listed = api.get(cust["href"]).json()["account"]
    assert [a["id"] for a in listed] == [acc["id"]]
    assert api.patch(cust["href"], {"account": listed}).status_code == 200
    assert api.patch(cust["href"], {"account": []}).status_code == 422
    assert api.post(CUSTOMER, {**body, "account": listed}).status_code == 422


def test_merge_patch_replaces_a_document_whole_and_null_removes_it(api):
    person = api.post(INDIVIDUAL, RICH_INDIVIDUAL).json()
    patched = api.patch(
        person["href"], {"contactMedium": CONTACT_MEDIA[1:2], "gender": None, "title": "Dr"}
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["contactMedium"] == CONTACT_MEDIA[1:2]
    assert "gender" not in body and body["title"] == "Dr"
    assert body["partyCharacteristic"] == RICH_INDIVIDUAL["partyCharacteristic"]  # untouched
    assert api.patch(person["href"], {"status": None}).json().get("status") is None
    # JSON Patch reaches into a document (slice 2: applied to the GET body)
    op = [{"op": "replace", "path": "/contactMedium/0/phoneNumber", "value": "112"}]
    replaced = api.patch(person["href"], op, "application/json-patch+json")
    assert replaced.json()["contactMedium"][0]["phoneNumber"] == "112"


def test_numbers_in_a_document_stay_exact(api):
    text = (
        '{"givenName": "A", "familyName": "B", "partyCharacteristic": [{"@type":'
        ' "NumberCharacteristic", "name": "score", "value": 19.990}]}'
    )
    response = api.post_raw(INDIVIDUAL, text)
    assert response.status_code == 201, response.text
    assert '"value":19.990' in api.get(response.json()["href"]).text


@pytest.mark.parametrize(
    ("attributes", "status", "message"),
    [
        # unknown attributes at any depth are a 400, as at the top
        (
            {"contactMedium": [{"@type": "EmailContactMedium", "fax": "1"}]},
            400,
            "contactMedium.0.fax: unknown attribute",
        ),
        ({"existsDuring": {"start": "2001-01-01T00:00:00Z"}}, 400, None),
        # the subclass decides what is known: a phone number is not an email's
        (
            {"contactMedium": [{"@type": "EmailContactMedium", "phoneNumber": "1"}]},
            400,
            "contactMedium.0.phoneNumber: unknown attribute",
        ),
        # a subclass the mapping does not name is read as the base: it knows no email
        (
            {"contactMedium": [{"@type": "PigeonContactMedium", "emailAddress": "a@b"}]},
            400,
            "contactMedium.0.emailAddress: unknown attribute",
        ),
        # a oneOf has no base: its @type must be one it maps
        (
            {
                "relatedParty": [
                    {
                        "@type": "RelatedPartyOrPartyRole",
                        "role": "x",
                        "partyOrPartyRole": {"id": "1"},
                    }
                ]
            },
            422,
            "relatedParty.0.partyOrPartyRole.@type: Field required",
        ),
        (
            {
                "relatedParty": [
                    {
                        "@type": "RelatedPartyOrPartyRole",
                        "role": "x",
                        "partyOrPartyRole": {"@type": "Pigeon"},
                    }
                ]
            },
            422,
            "relatedParty.0.partyOrPartyRole.@type: must be one of",
        ),
        # the spec's required attributes, @type included
        ({"contactMedium": [{"phoneNumber": "1"}]}, 422, "contactMedium.0.@type: Field required"),
        (
            {"partyCharacteristic": [{"@type": "StringCharacteristic", "name": "x"}]},
            422,
            "partyCharacteristic.0.value: Field required",
        ),
        # types, enums, formats
        ({"isLegalEntity": "yes"}, 422, "isLegalEntity: must be a boolean"),
        (
            {"contactMedium": {"@type": "EmailContactMedium"}},
            422,
            "contactMedium: must be an array",
        ),
        ({"status": "deceased"}, 422, None),  # an individual's status, not an organization's
        (
            {"existsDuring": {"startDateTime": "2001-01-01T00:00:00"}},
            422,
            "existsDuring.startDateTime: must be a date-time with a time zone",
        ),
        ({"tradingName": None}, None, None),  # null on create: nothing sent
    ],
)
def test_organization_documents_are_checked_against_the_spec(api, attributes, status, message):
    response = api.post(ORGANIZATION, {"name": "Romashka", **attributes})
    if status is None:
        assert response.status_code == 201 and "tradingName" not in response.json()
        return
    assert response.status_code == status, response.text
    if message:
        assert message in response.json()["message"]


def test_document_and_model_problems_are_one_error(api):
    """POST without the required givenName still names the unknown nested attribute
    (the gap probe relies on it, tests/tmf_gaps.py)."""
    response = api.post(INDIVIDUAL, {"familyName": "B", "skill": [{"@type": "Skill", "x": 1}]})
    assert response.status_code == 422
    message = response.json()["message"]
    assert "givenName: Field required" in message and "skill.0.x: unknown attribute" in message


def test_party_status_is_a_state_with_its_own_event(api, db_conn):
    subscribe(api, "TMF632")
    org = api.post(ORGANIZATION, {"name": "Romashka", "status": "initialized"}).json()
    api.patch(org["href"], {"status": "closed"})
    api.patch(org["href"], {"tradingName": "Daisy"})
    assert [t for t, _ in stored(db_conn, "TMF632")] == [
        "OrganizationCreateEvent",
        "OrganizationStateChangeEvent",
        "OrganizationAttributeValueChangeEvent",
    ]
    # a record only: a closed organization still becomes a customer
    assert api.post(CUSTOMER, apidata.customer_body(org, name="Romashka")).status_code == 201
    with pytest.raises(Exception, match="party_status_of_its_type"):
        db_conn.execute("UPDATE party SET status = 'deceased' WHERE id = %s", (org["id"],))
