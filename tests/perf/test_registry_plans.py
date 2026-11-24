"""The registry lists' SQL shapes (#330): the Subscribers and Parties screens' pages.

On the 100k load-test dataset a text comparison of an enum filter hid its statistics and
planned a full scan, the join to the customer or party ran for every skipped row of a deep
page, and an `id IN (... UNION ...)` search hashed the whole table. The plans here are
read on an empty database, so they check the shape, not the timing
(docs/loadtest-plan-sweep.md has the measurements)."""

import json
from collections.abc import Callable, Iterator

import pytest

from app.repository import party_search, subscribers
from tests import apidata
from tests.perf import guard

JOINS = ("Nested Loop", "Hash Join", "Merge Join")


def nodes(plan: dict, ancestors: tuple[str, ...] = ()) -> Iterator[tuple[dict, tuple[str, ...]]]:
    yield plan, ancestors
    for child in plan.get("Plans", []):
        yield from nodes(child, (*ancestors, plan["Node Type"]))


@pytest.fixture
def plan_of(db_conn, monkeypatch) -> Callable[..., dict]:
    """The plan of the statement a search function sends, with sequential scans off."""

    def plan(module, search: Callable, **kwargs) -> dict:
        statements = guard.sent(monkeypatch, module, run=False)
        search(db_conn, offset=5000, limit=20, **kwargs)
        query, params = statements[-1]
        return guard.plan(db_conn, query, params, off=guard.NO_SEQSCAN, json=True)

    return plan


def page_before_join(plan: dict) -> bool:
    limits = [ancestors for node, ancestors in nodes(plan) if node["Node Type"] == "Limit"]
    return bool(limits) and all(any(j in a for j in JOINS) for a in limits)


SUBSCRIBERS = {"q": None, "state": None, "rating_type": None}
PARTIES = {"kind": None, "q": None, "status": None}
CUSTOMERS = {"q": None, "status": None}


@pytest.mark.parametrize(
    ("module", "search", "args"),
    [
        (subscribers, subscribers.search, SUBSCRIBERS),
        (subscribers, subscribers.search, {**SUBSCRIBERS, "state": "Active"}),
        (party_search, party_search.search_customers, CUSTOMERS),
        (party_search, party_search.search_customers, {**CUSTOMERS, "status": "Active"}),
        (party_search, party_search.search_customers, {**CUSTOMERS, "q": "ivan"}),
    ],
)
def test_a_registry_page_is_cut_before_its_join(plan_of, module, search, args):
    """The join ran for every skipped row of a deep page: 20-330 ms at offset 5000 (#330).
    (A Subscribers search pages its matches, already few or walked, with the join.)"""
    plan = plan_of(module, search, **args)
    assert page_before_join(plan), json.dumps(plan, indent=1)


@pytest.mark.parametrize(
    ("module", "search", "args"),
    [
        (subscribers, subscribers.search, {**SUBSCRIBERS, "state": "Active"}),
        (party_search, party_search.search_customers, {**CUSTOMERS, "status": "Active"}),
        (party_search, party_search.search_parties, {**PARTIES, "kind": "Individual"}),
    ],
)
def test_an_enum_filter_is_compared_as_the_enum(plan_of, module, search, args):
    """As text the filter had no statistics (#330); as the enum the planner can also prove
    it from a partial index's predicate (the party name indexes are per kind)."""
    plan = json.dumps(plan_of(module, search, **args))
    assert "::text = " not in plan, plan


@pytest.mark.parametrize(
    ("module", "search", "args"),
    [
        (subscribers, subscribers.search, {**SUBSCRIBERS, "q": "ab12"}),
        (party_search, party_search.search_parties, {**PARTIES, "q": "ab12"}),
        (party_search, party_search.search_customers, {**CUSTOMERS, "q": "ab12"}),
    ],
)
def test_a_search_page_reads_its_matches_without_a_set_of_every_match(
    plan_of, module, search, args
):
    """No `id IN (... UNION ...)`: its hash of every match cost 20-60 ms on a narrow and
    1-1.6 s on a broad search at 100k rows (#330)."""
    plan = plan_of(module, search, **args)
    kinds = {node["Node Type"] for node, _ in nodes(plan)}
    assert not kinds & {"Aggregate", "Unique", "SetOp", "Hash"}, plan
    assert not any("Semi" in node.get("Join Type", "") for node, _ in nodes(plan)), plan


def test_a_search_pages_the_same_both_ways(api, match_path):
    """Paging a search: every match once, oldest first, with the next link until the last."""
    for given in ("Ivan", "Ivo", "Iva", "Anna"):
        apidata.individual(api, given, "Petrov")
    seen = []
    for offset in range(3):
        page = api.get(f"/internal/v1/party?q=iv&limit=1&offset={offset}")
        assert page.status_code == 200, page.text
        seen += [row["name"] for row in page.json()]
        assert ("Link" in page.headers) == (offset < 2)
    assert seen == ["Ivan Petrov", "Ivo Petrov", "Iva Petrov"]
