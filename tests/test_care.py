"""Customer care (issue #199): party interactions (TMF683) and trouble tickets (TMF621),
linked to the account audit log. docs/decisions/care.md.

Every response is checked against the vendored schemas by the `api` client, every stored
event against its listener schema (`stored`)."""

from datetime import UTC, timedelta

import psycopg
import pytest

from app.notify import events as notify_events
from tests import apidata, timeline
from tests.apiclient import total
from tests.builders.events import stored, subscribe, types

pytestmark = pytest.mark.capability("api.tmf683.partyInteraction.create")

INTERACTION = "/tmf-api/partyInteractionManagement/v5/partyInteraction"
TICKET = "/tmf-api/troubleTicket/v5/troubleTicket"
AUDIT = "/internal/v1/security/audit"
JSON_PATCH = "application/json-patch+json"
OPERATOR = "i.petrova"


def advance(api, minutes: int) -> None:
    api.clock.set(api.clock.now() + timedelta(minutes=minutes))


def now_z(api) -> str:
    return api.clock.now().astimezone(UTC).isoformat().replace("+00:00", "Z")


def party(customer: dict) -> list[dict]:
    return [
        {
            "role": "customer",
            "partyOrPartyRole": {
                "id": customer["id"],
                "@type": "PartyRoleRef",
                "@referredType": "Customer",
            },
        }
    ]


def channel(name: str = "phone") -> dict:
    return {"id": name, "@type": "ChannelRef"}


def entity(kind: str, uid: str, role: str = "subject") -> dict:
    return {"role": role, "entity": {"id": uid, "@type": "EntityRef", "@referredType": kind}}


def interaction_body(customer: dict, **over) -> dict:
    return {
        "@type": "PartyInteraction",
        "direction": "inbound",
        "reason": "Asked about the last bill",
        "relatedChannel": [{"role": "interaction", "channel": channel()}],
        "relatedParty": party(customer),
        **over,
    }


def ticket_body(customer: dict, **over) -> dict:
    return {
        "@type": "TroubleTicket",
        "description": "No data since yesterday",
        "severity": "major",
        "ticketType": "incident",
        "relatedParty": party(customer),
        **over,
    }


def interaction(api, customer: dict, **over) -> dict:
    response = api.post(INTERACTION, interaction_body(customer, **over))
    assert response.status_code == 201, response.text
    return response.json()


def ticket(api, customer: dict, **over) -> dict:
    response = api.post(TICKET, ticket_body(customer, **over))
    assert response.status_code == 201, response.text
    return response.json()


def move(api, case: dict, status: str, reason: str | None = None):
    body = {"status": status} | ({} if reason is None else {"statusChangeReason": reason})
    return api.patch(case["href"], body)


def audit(api, **filters) -> list[dict]:
    query = "".join(f"&{name}={value}" for name, value in filters.items())
    response = api.get(f"{AUDIT}?limit=100{query}")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def line(api):
    """A customer with a billing account and one product (the line)."""
    customer = apidata.customer(api)
    account = apidata.account(api, customer)
    offering = apidata.sellable_offering(api)
    api.clock.set(timeline.START + timedelta(hours=9))  # the prices start at START
    placed = apidata.order(api, account, apidata.add_item(offering))
    return customer, account, apidata.product_of(api, placed)


# --- interactions (TMF683) ---


def test_an_interaction_is_logged_with_its_items_and_notes(api, line):
    customer, account, product = line
    advance(api, 5)
    logged = interaction(
        api,
        customer,
        description="Explained the proration",
        interactionDate={
            "startDateTime": "2026-01-10T10:00:00+03:00",
            "endDateTime": "2026-01-10T10:12:00+03:00",
        },
        interactionItem=[
            {"item": entity("Product", product["id"]), "reason": "the line"},
            {
                "id": "bill",
                "item": entity("BillingAccount", account["id"], role="billing"),
                "resolution": "explained",
                "interactionItemType": "billing",
            },
        ],
        note=[{"text": "Customer was satisfied"}],
    )
    now = now_z(api)
    assert logged["status"] == "completed"  # the default: notes are written after the contact
    assert logged["direction"] == "inbound" and logged["reason"] == "Asked about the last bill"
    assert logged["creationDate"] == logged["lastUpdate"] == logged["statusChangeDate"] == now
    assert logged["interactionDate"] == {
        "startDateTime": "2026-01-10T07:00:00Z",
        "endDateTime": "2026-01-10T07:12:00Z",
    }
    assert logged["relatedChannel"] == [
        {
            "role": "interaction",
            "channel": {"id": "phone", "name": "phone", "@type": "ChannelRef"},
            "@type": "RelatedChannel",
        }
    ]
    (related,) = logged["relatedParty"]
    assert related["role"] == "customer"
    assert related["partyOrPartyRole"]["id"] == customer["id"]
    assert related["partyOrPartyRole"]["@referredType"] == "Customer"
    first, second = logged["interactionItem"]
    assert first["id"] == "1" and first["reason"] == "the line"
    assert first["item"]["entity"] == {
        "id": product["id"],
        "href": product["href"],
        "@type": "EntityRef",
        "@referredType": "Product",
    }
    assert second["id"] == "bill" and second["item"]["role"] == "billing"
    assert second["resolution"] == "explained" and second["interactionItemType"] == "billing"
    assert second["item"]["entity"]["href"] == account["href"]
    (note,) = logged["note"]
    assert note["text"] == "Customer was satisfied"
    assert note["author"] == OPERATOR and note["date"] == now
    assert api.get(logged["href"]).json() == logged


def test_a_patch_adds_notes_and_changes_an_open_interaction(api, line):
    customer, _, _ = line
    opened = interaction(api, customer, status="open", note=[{"text": "first"}])
    advance(api, 1)
    patched = api.patch(
        opened["href"],
        {"status": "inProgress", "description": "Waiting for a call back", "reason": "Refund"},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["status"] == "inProgress" and body["reason"] == "Refund"
    assert body["description"] == "Waiting for a call back"
    assert body["statusChangeDate"] == body["lastUpdate"] > opened["statusChangeDate"]
    advance(api, 1)
    noted = api.patch(opened["href"], {"note": [{"text": "second"}]}).json()
    assert [n["text"] for n in noted["note"]] == ["first", "second"]  # appended, not replaced
    assert noted["statusChangeDate"] == body["statusChangeDate"] < noted["lastUpdate"]
    assert api.patch(opened["href"], {"description": None}).json().get("description") is None
    # a JSON Patch sends the whole array: the existing notes unchanged, the new one added
    added = api.patch(
        opened["href"], [{"op": "add", "path": "/note/-", "value": {"text": "third"}}], JSON_PATCH
    )
    assert added.status_code == 200, added.text
    assert [n["text"] for n in added.json()["note"]] == ["first", "second", "third"]
    changed = [noted["note"][0] | {"text": "rewritten"}]
    assert api.patch(opened["href"], {"note": changed}).status_code == 422
    assert api.patch(opened["href"], {}).json() == api.get(opened["href"]).json()


def test_a_completed_interaction_only_takes_notes(api, line):
    customer, _, _ = line
    done = interaction(api, customer)
    assert api.patch(done["href"], {"reason": "Other"}).status_code == 409
    assert api.patch(done["href"], {"status": "open"}).status_code == 409
    assert api.patch(done["href"], {"description": "x"}).status_code == 409
    same = api.patch(done["href"], {"status": "completed", "reason": done["reason"]})
    assert same.status_code == 200 and same.json() == done  # nothing changes
    noted = api.patch(done["href"], {"note": [{"text": "called back"}]})
    assert noted.status_code == 200 and len(noted.json()["note"]) == 1
    assert api.get(done["href"]).json()["reason"] == done["reason"]


def test_an_interaction_is_a_record(api, line, db_conn):
    customer, _, _ = line
    logged = interaction(api, customer, note=[{"text": "kept"}])
    assert api.delete(logged["href"]).status_code == 405
    for statement in (
        "UPDATE party_interaction_note SET text = 'x'",
        "DELETE FROM party_interaction_note",
    ):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            db_conn.execute(statement)
        db_conn.rollback()


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"direction": "sideways"}, 422),
        ({"status": "ongoing"}, 422),
        ({"reason": ""}, 422),
        ({"relatedChannel": []}, 422),
        ({"relatedChannel": [{"role": "x", "channel": {"id": "fax"}}]}, 422),
        ({"relatedChannel": [{"channel": {"id": "phone"}}]}, 422),
        (
            {
                "relatedChannel": [
                    {"role": "a", "channel": {"id": "phone"}},
                    {"role": "b", "channel": {"id": "chat"}},
                ]
            },
            422,
        ),
        ({"relatedParty": []}, 422),
        ({"relatedParty": None}, 422),
        ({"interactionDate": {"endDateTime": "2026-01-01T00:00:00Z"}}, 422),
        (
            {
                "interactionDate": {
                    "startDateTime": "2026-01-02T00:00:00Z",
                    "endDateTime": "2026-01-01T00:00:00Z",
                }
            },
            422,
        ),
        ({"interactionItem": [{"reason": "no item"}]}, 422),
        ({"interactionItem": [{"item": entity("Product", apidata.NIL)}]}, 422),
        ({"interactionItem": [{"item": entity("AuditEntry", apidata.NIL)}]}, 422),
        ({"interactionItem": [{"item": entity("ProductOrder", apidata.NIL)}]}, 422),
        ({"interactionItem": [{"item": entity("PartyInteraction", apidata.NIL)}]}, 422),
        ({"note": [{"text": ""}]}, 422),
        ({"note": [{"text": "x", "author": "someone"}]}, 422),
        ({"note": [{"text": "x", "date": "2026-01-01T00:00:00Z"}]}, 422),
        ({"note": [{"text": "x", "id": "n1"}]}, 422),
        ({"attachment": [{"name": "x"}]}, 422),
        ({"interactionRelationship": [{"id": "1"}]}, 422),
        ({"externalIdentifier": [{"id": "1"}]}, 422),
        ({"lastUpdate": "2020-01-01T00:00:00Z"}, 422),
        ({"id": "not-a-uuid"}, 422),
        ({"@type": "Other"}, 400),
        ({"href": "/x"}, 400),
        ({"statusChangeDate": "2026-01-01T00:00:00Z"}, 400),
        ({"nonsense": 1}, 400),
        ({"interactionItem": [{"item": entity("Product", apidata.NIL), "zz": 1}]}, 400),
    ],
)
def test_a_bad_interaction_is_refused(api, db_conn, line, change, status):
    customer, _, _ = line
    response = api.post(INTERACTION, interaction_body(customer) | change)
    assert response.status_code == status, response.text
    assert db_conn.execute("SELECT count(*) FROM party_interaction").fetchone() == (0,)
    assert audit(api) == []


def test_an_interaction_needs_what_the_spec_requires_and_a_customer(api, line):
    customer, _, _ = line
    for missing in ("direction", "reason", "relatedChannel", "relatedParty"):
        body = interaction_body(customer)
        del body[missing]
        assert api.post(INTERACTION, body).status_code == 422, missing
    wrong_role = interaction_body(customer)
    wrong_role["relatedParty"][0]["role"] = "agent"
    assert api.post(INTERACTION, wrong_role).status_code == 422
    unknown = interaction_body({"id": apidata.NIL})
    assert api.post(INTERACTION, unknown).status_code == 422
    party_ref = interaction_body(customer)
    party_ref["relatedParty"][0]["partyOrPartyRole"]["@referredType"] = "Individual"
    assert api.post(INTERACTION, party_ref).status_code == 422


def test_an_item_must_belong_to_the_customer(api, line):
    customer, account, product = line
    stranger = apidata.customer(api, name="Petr Petrov")
    for kind, uid in (("Product", product["id"]), ("BillingAccount", account["id"])):
        refused = api.post(
            INTERACTION, interaction_body(stranger, interactionItem=[{"item": entity(kind, uid)}])
        )
        assert refused.status_code == 422, refused.text
        assert "another customer" in refused.json()["message"]
    case = ticket(api, customer)
    refused = api.post(
        INTERACTION,
        interaction_body(stranger, interactionItem=[{"item": entity("TroubleTicket", case["id"])}]),
    )
    assert refused.status_code == 422
    twice = [{"id": "a", "item": entity("Product", product["id"])}] * 2
    assert (
        api.post(INTERACTION, interaction_body(customer, interactionItem=twice)).status_code == 422
    )


def test_a_patch_cannot_change_what_was_recorded(api, line):
    customer, _, product = line
    opened = interaction(api, customer, status="open")
    for change in (
        {"relatedParty": party(customer)},
        {"relatedChannel": [{"role": "x", "channel": channel("chat")}]},
        {"interactionItem": [{"item": entity("Product", product["id"])}]},
        {"interactionDate": {"startDateTime": "2026-01-01T00:00:00Z"}},
        {"direction": "outbound"},
        {"id": opened["id"]},
        {"@type": "Other"},
    ):
        assert api.patch(opened["href"], change).status_code == 400, change
    for change in (
        {"status": "done"},
        {"status": None},
        {"reason": None},
        {"attachment": []},
        {"lastUpdate": "2020-01-01T00:00:00Z"},
    ):
        assert api.patch(opened["href"], change).status_code == 422, change
    assert api.patch(opened["href"], {"lastUpdate": opened["lastUpdate"]}).status_code == 200
    assert api.patch(f"{INTERACTION}/{apidata.NIL}", {"reason": "x"}).status_code == 404
    assert api.get(opened["href"]).json() == opened


def test_the_interaction_list_filters_pages_and_selects_fields(api, line):
    customer, account, product = line
    other = apidata.customer(api, name="Petr Petrov")
    advance(api, 1)
    first = interaction(api, customer, interactionItem=[{"item": entity("Product", product["id"])}])
    advance(api, 1)
    second = interaction(
        api,
        customer,
        direction="outbound",
        status="open",
        interactionItem=[{"item": entity("BillingAccount", account["id"])}],
    )
    advance(api, 1)
    third = interaction(api, other)

    def ids(query: str = "") -> list[str]:
        response = api.get(f"{INTERACTION}{query}")
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()]

    assert ids() == [third["id"], second["id"], first["id"]]  # newest first
    assert ids(f"?relatedParty.id={customer['id']}") == [second["id"], first["id"]]
    assert ids(f"?product.id={product['id']}") == [first["id"]]
    assert ids(f"?billingAccount.id={account['id']}") == [second["id"]]
    assert ids("?status=open") == [second["id"]]
    assert ids("?direction=outbound") == [second["id"]]
    assert ids(f"?id={third['id']}") == [third["id"]]
    assert ids(f"?relatedParty.id={apidata.NIL}") == []
    for bad in ("?status=ongoing", "?direction=up", "?bogus=1", "?product.id=x", "?limit=0"):
        assert api.get(f"{INTERACTION}{bad}").status_code == 400, bad
    page = api.get(f"{INTERACTION}?limit=2")
    assert len(page.json()) == 2 and total(page, api) == "3"
    assert set(api.get(f"{first['href']}?fields=reason,note").json()) == {
        "id",
        "href",
        "@type",
        "reason",
        "note",
    }
    assert api.get(f"{first['href']}?fields=attachment").status_code == 400
    assert api.get(f"{INTERACTION}/{apidata.NIL}").status_code == 404
    assert api.get(f"{INTERACTION}/nope").status_code == 404


def test_a_client_may_choose_the_id_and_the_meta_is_kept(api, line):
    customer, _, _ = line
    uid = "7a1f5c2e-0000-4000-8000-00000000c0de"
    for url, body in (
        (INTERACTION, interaction_body(customer, status="open")),
        (TICKET, ticket_body(customer)),
    ):
        made = api.post(
            url, body | {"id": uid, "@baseType": "Entity", "@schemaLocation": "https://x.test/s"}
        )
        assert made.status_code == 201, made.text
        assert made.json()["id"] == uid and made.headers["location"] == f"{url}/{uid}"
        assert made.json()["@baseType"] == "Entity"
        assert made.json()["@schemaLocation"] == "https://x.test/s"
        assert api.post(url, body | {"id": uid}).status_code == 409
        cleared = api.patch(f"{url}/{uid}", {"@baseType": None}).json()
        assert "@baseType" not in cleared and cleared["@schemaLocation"] == "https://x.test/s"


# --- cases (TMF621) ---


def test_a_ticket_is_opened_acknowledged(api, line):
    customer, account, product = line
    advance(api, 5)
    case = ticket(
        api,
        customer,
        name="No data",
        channel=channel("chat"),
        requestedResolutionDate="2026-02-01T12:00:00+03:00",
        expectedResolutionDate="2026-02-02T09:00:00Z",
        relatedEntity=[
            entity("Product", product["id"]),
            entity("BillingAccount", account["id"], role="billing"),
        ],
        note=[{"text": "Restarted the phone, no change"}],
    )
    now = now_z(api)
    assert case["status"] == "acknowledged" and case["priority"] == "medium"
    assert case["severity"] == "major" and case["ticketType"] == "incident"
    assert case["name"] == "No data" and case["description"] == "No data since yesterday"
    assert case["channel"] == {"id": "chat", "name": "chat", "@type": "ChannelRef"}
    assert case["creationDate"] == case["lastUpdate"] == case["statusChangeDate"] == now
    assert case["requestedResolutionDate"] == "2026-02-01T09:00:00Z"
    assert case["expectedResolutionDate"] == "2026-02-02T09:00:00Z"
    assert "resolutionDate" not in case and "statusChangeReason" not in case
    assert case["statusChangeHistory"] == [
        {"status": "acknowledged", "statusChangeDate": now, "@type": "StatusChange"}
    ]
    assert case["relatedParty"][0]["partyOrPartyRole"]["id"] == customer["id"]
    assert [e["entity"]["@referredType"] for e in case["relatedEntity"]] == [
        "Product",
        "BillingAccount",
    ]
    assert case["relatedEntity"][1]["role"] == "billing"
    assert case["relatedEntity"][0]["entity"]["href"] == product["href"]
    (note,) = case["note"]
    assert note["text"].startswith("Restarted") and note["author"] == OPERATOR
    assert api.get(case["href"]).json() == case
    # the server's values may be sent back; another value is refused
    echoed = api.post(TICKET, ticket_body(customer, status="acknowledged"))
    assert echoed.status_code == 201
    assert api.post(TICKET, ticket_body(customer, status="inProgress")).status_code == 422


def test_a_ticket_moves_along_the_transitions_and_keeps_its_history(api, line):
    customer, _, _ = line
    case = ticket(api, customer)
    steps = [
        ("inProgress", None),
        ("pending", "Waiting for the customer"),
        ("inProgress", None),
        ("held", "Network maintenance"),
        ("inProgress", None),
        ("resolved", None),
        ("inProgress", "The fault came back"),
        ("resolved", "Replaced the SIM"),
        ("closed", None),
    ]
    resolved_at = []
    for status, reason in steps:
        advance(api, 10)
        moved = move(api, case, status, reason)
        assert moved.status_code == 200, moved.text
        body = moved.json()
        now = now_z(api)
        assert body["status"] == status and body["statusChangeDate"] == now
        assert body.get("statusChangeReason") == reason
        if status == "resolved":
            assert body["resolutionDate"] == now
            resolved_at.append(now)
        elif status == "closed":
            assert body["resolutionDate"] == resolved_at[-1]  # kept from the resolution
        else:
            assert "resolutionDate" not in body  # a reopening clears it
    history = api.get(case["href"]).json()["statusChangeHistory"]
    assert [h["status"] for h in history] == ["acknowledged", *(s for s, _ in steps)]
    assert [h.get("statusChangeReason") for h in history] == [None, *(r for _, r in steps)]
    assert len(resolved_at) == 2 and resolved_at[0] < resolved_at[1]


@pytest.mark.parametrize(
    ("path", "target"),
    [
        ((), "pending"),
        ((), "held"),
        ((), "resolved"),
        ((), "closed"),
        (("inProgress",), "acknowledged"),
        (("inProgress",), "rejected"),
        (("inProgress",), "closed"),
        (("inProgress", "pending"), "held"),
        (("inProgress", "pending"), "resolved"),
        (("inProgress", "held"), "pending"),
        (("inProgress", "resolved"), "cancelled"),
        (("inProgress", "resolved"), "pending"),
    ],
)
def test_other_moves_are_a_conflict(api, line, path, target):
    customer, _, _ = line
    case = ticket(api, customer)
    for status in path:
        assert move(api, case, status, "because").status_code == 200
    before = api.get(case["href"]).json()
    assert move(api, case, target, "because").status_code == 409
    assert api.get(case["href"]).json() == before


@pytest.mark.parametrize(
    ("path", "final"), [((), "rejected"), ((), "cancelled"), (("inProgress", "resolved"), "closed")]
)
def test_a_final_ticket_takes_no_change(api, line, path, final):
    customer, _, _ = line
    case = ticket(api, customer)
    for status in (*path, final):
        assert move(api, case, status, "because").status_code == 200
    closed = api.get(case["href"]).json()
    for change in (
        {"status": "inProgress", "statusChangeReason": "again"},
        {"note": [{"text": "late"}]},
        {"priority": "high"},
        {"status": final},
        {"@baseType": "X"},
    ):
        assert api.patch(case["href"], change).status_code == 409, change
    assert api.patch(case["href"], {}).status_code == 200  # an empty patch changes nothing
    assert api.get(case["href"]).json() == closed
    assert api.delete(case["href"]).status_code == 405


def test_a_reason_is_required_where_the_case_stops_or_reopens(api, line):
    customer, _, _ = line
    for target in ("rejected", "cancelled"):
        case = ticket(api, customer)
        assert move(api, case, target).status_code == 422
        assert move(api, case, target, "Duplicate").json()["statusChangeReason"] == "Duplicate"
    case = ticket(api, customer)
    assert move(api, case, "inProgress").status_code == 200
    for target in ("pending", "held"):
        assert move(api, case, target).status_code == 422
    assert move(api, case, "resolved").status_code == 200
    assert move(api, case, "inProgress").status_code == 422  # a reopening
    assert api.get(case["href"]).json()["status"] == "resolved"
    # a reason without a status change is refused, the same status is no change
    assert api.patch(case["href"], {"statusChangeReason": "x"}).status_code == 422
    same = api.patch(case["href"], {"status": "resolved"})
    assert same.status_code == 200 and len(same.json()["statusChangeHistory"]) == 3


def test_a_patch_changes_the_case_its_links_and_adds_notes(api, line):
    customer, account, product = line
    case = ticket(api, customer, relatedEntity=[entity("Product", product["id"])])
    advance(api, 1)
    patched = api.patch(
        case["href"],
        {
            "name": "Billing dispute",
            "description": "Charged twice",
            "severity": "critical",
            "ticketType": "complaint",
            "priority": "high",
            "channel": channel("email"),
            "expectedResolutionDate": "2026-03-01T00:00:00Z",
            "relatedEntity": [entity("BillingAccount", account["id"])],
            "note": [{"text": "Checked the ledger"}],
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert (body["name"], body["severity"], body["ticketType"], body["priority"]) == (
        "Billing dispute",
        "critical",
        "complaint",
        "high",
    )
    assert body["channel"]["id"] == "email" and body["description"] == "Charged twice"
    assert body["expectedResolutionDate"] == "2026-03-01T00:00:00Z"
    assert [e["entity"]["@referredType"] for e in body["relatedEntity"]] == ["BillingAccount"]
    assert [n["text"] for n in body["note"]] == ["Checked the ledger"]
    assert body["lastUpdate"] > case["lastUpdate"]
    assert body["statusChangeDate"] == case["statusChangeDate"]
    cleared = api.patch(
        case["href"], {"name": None, "channel": None, "expectedResolutionDate": None}
    ).json()
    assert not {"name", "channel", "expectedResolutionDate"} & set(cleared)
    replaced = api.patch(
        case["href"],
        [{"op": "replace", "path": "/priority", "value": "low"}],
        JSON_PATCH,
    )
    assert replaced.status_code == 200 and replaced.json()["priority"] == "low"
    assert len(replaced.json()["note"]) == 1


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"severity": "fatal"}, 422),
        ({"ticketType": "question"}, 422),
        ({"priority": "urgent"}, 422),
        ({"description": ""}, 422),
        ({"channel": {"id": "fax"}}, 422),
        ({"relatedParty": []}, 422),
        ({"relatedEntity": [entity("Product", apidata.NIL)]}, 422),
        ({"relatedEntity": [entity("TroubleTicket", apidata.NIL)]}, 422),
        ({"relatedEntity": [{"entity": {"id": apidata.NIL, "@referredType": "Product"}}]}, 422),
        ({"note": [{"text": "x", "author": "me"}]}, 422),
        ({"status": "resolved"}, 422),
        ({"statusChangeDate": "2020-01-01T00:00:00Z"}, 422),
        ({"resolutionDate": "2020-01-01T00:00:00Z"}, 422),
        ({"requestedResolutionDate": "tomorrow"}, 422),
        ({"attachment": [{"name": "x"}]}, 422),
        ({"externalIdentifier": [{"id": "1"}]}, 422),
        ({"troubleTicketRelationship": [{"id": "1"}]}, 422),
        ({"troubleTicketSpecification": {"id": "1"}}, 422),
        ({"troubleTicketCharacteristic": [{"name": "a", "value": 1}]}, 422),
        ({"id": "nope"}, 422),
        ({"href": "/elsewhere"}, 422),
        ({"@type": "Other"}, 400),
        ({"statusChangeHistory": []}, 400),
        ({"creationDate": "2020-01-01T00:00:00Z"}, 400),
        ({"nonsense": 1}, 400),
    ],
)
def test_a_bad_ticket_is_refused(api, db_conn, line, change, status):
    customer, _, _ = line
    response = api.post(TICKET, ticket_body(customer) | change)
    assert response.status_code == status, response.text
    assert db_conn.execute("SELECT count(*) FROM trouble_ticket").fetchone() == (0,)
    assert audit(api) == []


def test_a_ticket_needs_what_the_spec_requires_and_a_customer(api, line):
    customer, _, _ = line
    for missing in ("description", "severity", "ticketType", "relatedParty"):
        body = ticket_body(customer)
        del body[missing]
        assert api.post(TICKET, body).status_code == 422, missing


def test_a_bad_ticket_patch_is_refused(api, line):
    customer, _, _ = line
    case = ticket(api, customer)
    for change in (
        {"relatedParty": party(customer)},
        {"creationDate": "2020-01-01T00:00:00Z"},
        {"@type": "Other"},
        {"zz": 1},
    ):
        assert api.patch(case["href"], change).status_code == 400, change
    for change in (
        {"status": "open"},
        {"status": None},
        {"severity": None},
        {"description": None},
        {"priority": "urgent"},
        {"resolutionDate": "2026-01-01T00:00:00Z"},
        {"statusChangeHistory": []},
        {"id": apidata.NIL},
        {"relatedEntity": [entity("Product", apidata.NIL)]},
        {"troubleTicketSpecification": {"id": "1"}},
    ):
        assert api.patch(case["href"], change).status_code == 422, change
    kept = api.patch(
        case["href"],
        {
            "id": case["id"],
            "href": case["href"],
            "statusChangeHistory": case["statusChangeHistory"],
        },
    )
    assert kept.status_code == 200  # the server's own values are accepted
    assert api.patch(f"{TICKET}/{apidata.NIL}", {"priority": "low"}).status_code == 404
    assert api.get(case["href"]).json() == case


def test_the_ticket_list_filters_and_pages(api, line):
    customer, account, product = line
    other = apidata.customer(api, name="Petr Petrov")
    advance(api, 1)
    first = ticket(api, customer, relatedEntity=[entity("Product", product["id"])])
    advance(api, 1)
    second = ticket(
        api,
        customer,
        severity="minor",
        ticketType="request",
        priority="low",
        relatedEntity=[entity("BillingAccount", account["id"])],
    )
    advance(api, 1)
    third = ticket(api, other)
    assert move(api, third, "inProgress").status_code == 200

    def ids(query: str = "") -> list[str]:
        response = api.get(f"{TICKET}{query}")
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()]

    assert ids() == [third["id"], second["id"], first["id"]]
    assert ids(f"?relatedParty.id={customer['id']}") == [second["id"], first["id"]]
    assert ids(f"?product.id={product['id']}") == [first["id"]]
    assert ids(f"?billingAccount.id={account['id']}") == [second["id"]]
    assert ids("?status=inProgress") == [third["id"]]
    assert ids("?status=acknowledged&severity=minor") == [second["id"]]
    assert ids("?ticketType=request") == [second["id"]] == ids("?priority=low")
    assert ids("?status=closed") == []
    for bad in ("?status=open", "?severity=x", "?sort=name", "?filter=x", "?relatedParty.id=1"):
        assert api.get(f"{TICKET}{bad}").status_code == 400, bad
    page = api.get(f"{TICKET}?limit=1&offset=1")
    assert [t["id"] for t in page.json()] == [second["id"]] and total(page, api) == "3"
    partial = api.get(f"{first['href']}?fields=status,statusChangeHistory").json()
    assert set(partial) == {"id", "href", "@type", "status", "statusChangeHistory"}
    assert api.get(f"{TICKET}/{apidata.NIL}").status_code == 404


def test_the_history_and_the_notes_of_a_ticket_are_append_only(api, line, db_conn):
    customer, _, _ = line
    ticket(api, customer, note=[{"text": "kept"}])
    for table in ("trouble_ticket_note", "trouble_ticket_status_change"):
        for statement in (f"UPDATE {table} SET id = id", f"DELETE FROM {table}"):  # noqa: S608
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                db_conn.execute(statement)
            db_conn.rollback()


# --- interactions and cases refer to each other ---


def test_an_interaction_and_a_ticket_link_each_other(api, line):
    customer, _, _ = line
    case = ticket(api, customer)
    call = interaction(
        api, customer, interactionItem=[{"item": entity("TroubleTicket", case["id"])}]
    )
    assert call["interactionItem"][0]["item"]["entity"]["href"] == case["href"]
    linked = api.patch(case["href"], {"relatedEntity": [entity("PartyInteraction", call["id"])]})
    assert linked.status_code == 200, linked.text
    assert linked.json()["relatedEntity"][0]["entity"]["href"] == call["href"]
    assert api.get(call["interactionItem"][0]["item"]["entity"]["href"]).status_code == 200
    stranger = apidata.customer(api, name="Petr Petrov")
    refused = api.post(
        TICKET, ticket_body(stranger, relatedEntity=[entity("PartyInteraction", call["id"])])
    )
    assert refused.status_code == 422


# --- the audit log ---


def test_care_work_is_in_the_customers_audit_history(api, line):
    customer, account, product = line
    call = interaction(
        api,
        customer,
        status="open",
        interactionItem=[{"item": entity("Product", product["id"])}],
        note=[{"text": "first"}],
    )
    case = ticket(api, customer, relatedEntity=[entity("BillingAccount", account["id"])])
    api.patch(call["href"], {"note": [{"text": "a"}, {"text": "b"}], "status": "completed"})
    move(api, case, "inProgress")
    move(api, case, "pending", "Waiting for the customer")
    api.patch(case["href"], {"note": [{"text": "c"}], "priority": "high"})
    entries = audit(api, **{"customer.id": customer["id"]})[::-1]  # oldest first
    assert [e["action"] for e in entries] == [
        "interaction_logged",
        "ticket_opened",
        "care_note_added",
        "care_note_added",
        "ticket_status_changed",
        "ticket_status_changed",
        "care_note_added",
    ]
    assert {(e["outcome"], e["verification"], e["operator"]) for e in entries} == {
        ("done", "none", OPERATOR)
    }
    logged, opened, note_a, _, started, waiting, note_c = entries
    assert logged["detail"] == {
        "interaction": call["id"],
        "status": "open",
        "direction": "inbound",
        "channel": "phone",
        "notes": 1,
    }
    assert logged["product"]["id"] == product["id"]
    assert logged["billingAccount"]["id"] == account["id"]  # the account of the line
    assert opened["detail"]["ticket"] == case["id"] and opened["detail"]["status"] == "acknowledged"
    assert opened["billingAccount"]["id"] == account["id"] and opened["product"] is None
    noted = api.get(call["href"]).json()["note"]
    assert note_a["detail"] == {"interaction": call["id"], "note": noted[1]["id"]}
    assert note_a["product"]["id"] == product["id"]
    assert started["detail"] == {"ticket": case["id"], "from": "acknowledged", "to": "inProgress"}
    assert started["reason"] is None
    assert waiting["detail"]["to"] == "pending" and waiting["reason"] == "Waiting for the customer"
    assert note_c["detail"]["ticket"] == case["id"]
    # the filters of the audit read know the new actions, and one entry can be read by id
    assert len(audit(api, action="care_note_added")) == 3
    assert [e["id"] for e in audit(api, id=opened["id"])] == [opened["id"]]
    assert audit(api, id=apidata.NIL) == []
    assert len(audit(api, **{"product.id": product["id"]})) == 3


def test_an_interaction_and_a_ticket_can_be_about_an_audit_entry(api, line):
    customer, _, _ = line
    opened = ticket(api, customer)
    (entry,) = audit(api, action="ticket_opened")
    call = interaction(api, customer, interactionItem=[{"item": entity("AuditEntry", entry["id"])}])
    reference = call["interactionItem"][0]["item"]["entity"]
    assert reference["@referredType"] == "AuditEntry"
    assert reference["href"] == f"{AUDIT}?id={entry['id']}"
    assert [e["id"] for e in api.get(reference["href"]).json()] == [entry["id"]]
    linked = api.patch(opened["href"], {"relatedEntity": [entity("AuditEntry", entry["id"])]})
    assert linked.status_code == 200
    assert linked.json()["relatedEntity"][0]["entity"]["id"] == entry["id"]
    stranger = apidata.customer(api, name="Petr Petrov")
    refused = api.post(
        TICKET, ticket_body(stranger, relatedEntity=[entity("AuditEntry", entry["id"])])
    )
    assert refused.status_code == 422  # the entry is about another customer


def test_care_work_raises_no_security_alert(api, db_conn, line):
    customer, _, _ = line
    since = api.clock.now()
    case = ticket(api, customer, note=[{"text": "x"}])
    interaction(api, customer)
    move(api, case, "inProgress")
    api.patch(case["href"], {"note": [{"text": "y"}]})
    assert len(audit(api)) == 4
    assert notify_events._security(db_conn, since) == []
    db_conn.rollback()


# --- events ---


def test_interaction_events(api, db_conn, line):
    customer, _, _ = line
    subscribe(api, "TMF683")
    call = interaction(api, customer, status="open")
    api.patch(call["href"], {"note": [{"text": "x"}]})
    api.patch(call["href"], {"status": "inProgress"})
    api.patch(call["href"], {"status": "completed", "description": "done"})
    api.patch(call["href"], {"status": "completed"})  # no change: no event
    events = stored(db_conn, "TMF683")
    assert [name for name, _ in events] == [
        "PartyInteractionCreateEvent",
        "PartyInteractionAttributeValueChangeEvent",
        "PartyInteractionStatusChangeEvent",
        "PartyInteractionStatusChangeEvent",
        "PartyInteractionAttributeValueChangeEvent",
    ]
    last = events[-1][1]["event"]["partyInteraction"]
    assert last == api.get(call["href"]).json()
    db_conn.rollback()


def test_ticket_events(api, db_conn, line):
    customer, _, _ = line
    subscribe(api, "TMF621")
    case = ticket(api, customer)
    api.patch(case["href"], {"priority": "high"})
    move(api, case, "inProgress")
    api.patch(case["href"], {"status": "resolved", "note": [{"text": "fixed"}]})
    move(api, case, "closed")
    assert move(api, case, "inProgress", "x").status_code == 409  # no event
    events = stored(db_conn, "TMF621")
    assert [name for name, _ in events] == [
        "TroubleTicketCreateEvent",
        "TroubleTicketAttributeValueChangeEvent",
        "TroubleTicketStatusChangeEvent",
        "TroubleTicketStatusChangeEvent",
        "TroubleTicketAttributeValueChangeEvent",
        "TroubleTicketResolvedEvent",
        "TroubleTicketStatusChangeEvent",
    ]
    resolved = events[5][1]["event"]["troubleTicket"]
    assert resolved["status"] == "resolved" and resolved["id"] == case["id"]
    db_conn.rollback()


def test_a_hub_takes_only_the_events_it_asked_for(api, db_conn, line):
    customer, _, _ = line
    subscribe(api, "TMF621", "TroubleTicketResolvedEvent")
    case = ticket(api, customer)
    move(api, case, "inProgress")
    move(api, case, "resolved")
    assert types(db_conn, "TMF621") == ["TroubleTicketResolvedEvent"]
    assert types(db_conn, "TMF683") == []
    db_conn.rollback()
    refused = api.post(
        "/tmf-api/troubleTicket/v5/hub",
        {
            "callback": "http://listener.test/events",
            "query": "eventType=PartyInteractionCreateEvent",
        },
    )
    assert refused.status_code == 400


def test_the_ticket_specification_is_not_implemented(api):
    base = "/tmf-api/troubleTicket/v5/troubleTicketSpecification"
    assert api.client.get(base).status_code == 404
    assert api.client.post(base, json={}).status_code == 404
