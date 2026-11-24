"""The staging-data scripts (ops/staging-data/) against the local app on a throwaway
database: a dry run prints and writes nothing, `--apply` writes, a second run is a no-op.
They are never run against the stand (docs: ops/staging-data/README.md)."""

import importlib
import sys
from pathlib import Path

import pytest

from tests import apidata
from tests.apidata import ACCOUNT, CATALOG

SCRIPTS = Path(__file__).resolve().parents[2] / "ops" / "staging-data"
MESSAGE_ACCOUNT = "/tmf-api/accountManagement/v5/billingAccount"
CATEGORY, CATALOGS = f"{CATALOG}/category", f"{CATALOG}/productCatalog"
OFFERING, SPEC = f"{CATALOG}/productOffering", f"{CATALOG}/productSpecification"


@pytest.fixture
def scripts(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    for name in ("_client", "rename_accounts", "retire_test_data", "seed_numbers", "catalog_tree"):
        sys.modules.pop(name, None)
    yield lambda name: importlib.import_module(name)
    for name in ("_client", "rename_accounts", "retire_test_data", "seed_numbers", "catalog_tree"):
        sys.modules.pop(name, None)


class Stand:
    """The scripts' client over the test API (already signed in)."""

    def __init__(self, api, scripts) -> None:
        self.api, self.client_module = api, scripts("_client")

    def transport(self, method, path, body, content_type):
        kwargs = {} if body is None else {"json": body}
        response = self.api.request(method, path, headers={"Content-Type": content_type}, **kwargs)
        return (
            response.status_code,
            dict(response.headers),
            response.json() if response.content else None,
        )

    def run(self, module, apply: bool) -> int:
        client = self.client_module.Client(self.transport, apply)
        module.work(client)
        return client.changes


def names(api, url):
    return sorted(r["name"] for r in api.get(f"{url}?limit=100").json())


def check_cycle(stand, module, snapshot, expected: int):
    """A dry run changes nothing, --apply makes `expected` changes, a rerun makes none."""
    before = snapshot()
    assert stand.run(module, apply=False) == expected
    assert snapshot() == before
    assert stand.run(module, apply=True) == expected
    assert snapshot() != before
    assert stand.run(module, apply=False) == 0


def test_rename_accounts(api, scripts):
    stand = Stand(api, scripts)
    john = apidata.customer(api, apidata.individual(api, "John", "Smith"), name="John Smith")
    apidata.account(api, john, name="Main account")
    globex = apidata.customer(api, apidata.organization(api, "Globex"), name="Globex")
    apidata.account(api, globex, name="Billing 1")
    ann = apidata.customer(api, apidata.individual(api, "Ann", "Lee"), name="Ann Lee")
    apidata.account(api, ann, name="Ann Lee main")

    check_cycle(stand, scripts("rename_accounts"), lambda: names(api, ACCOUNT), 2)

    assert names(api, ACCOUNT) == ["Ann Lee main", "Globex corporate", "John Smith main"]


def test_retire_test_data(api, scripts):
    stand = Stand(api, scripts)
    keep = apidata.sellable_offering(api, name="Fiber 100", code="NET-100")
    apidata.sellable_offering(api, name="MCP Coverage plan", code="MCP-1")
    apidata.sellable_offering(api, name="Plain name", code="TMF-E2E-7")
    api.post(
        SPEC,
        {"@type": "ProductSpecification", "name": "MCP Isolated spec", "lifecycleStatus": "Active"},
    )
    idle = apidata.account(api, name="MCP idle")
    busy = apidata.account(api, name="MCP busy")
    apidata.account(api, name="Real customer")
    placed = apidata.order(api, busy, apidata.add_item(keep), requestedStartDate=apidata.START)
    assert placed["state"] == "completed"

    def snapshot():
        return (
            [(o["name"], o["lifecycleStatus"], o["isSellable"]) for o in api.get(OFFERING).json()],
            [(s["name"], s["lifecycleStatus"]) for s in api.get(SPEC).json()],
            [(a["name"], a["state"]) for a in api.get(ACCOUNT).json()],
        )

    # 2 offerings x (lifecycle + sellable in one patch) + 1 specification + 1 account
    check_cycle(stand, scripts("retire_test_data"), snapshot, 4)

    offerings, specs, accounts = snapshot()
    assert sorted(offerings) == [
        ("Fiber 100", "Active", True),
        ("MCP Coverage plan", "Retired", False),
        ("Plain name", "Retired", False),
    ]
    assert ("MCP Isolated spec", "Retired") in specs and ("Fiber", "Active") in specs
    assert dict(accounts) == {"MCP idle": "Closed", "MCP busy": "Active", "Real customer": "Active"}
    assert api.get(f"{ACCOUNT}/{idle['id']}").json()["state"] == "Closed"


def test_seed_numbers(api, scripts):
    stand = Stand(api, scripts)
    mobile = apidata.sellable_offering(api, value=29, name="Mobile S", code="MOB-S")
    apidata.sellable_offering(api, value=129, name="Mobile L", code="MOB-L")
    assert mobile["name"] == "Mobile S"
    numbers = "/tmf-api/resourceInventory/v5/resource"
    api.post(
        numbers,
        {
            "@type": "LogicalResource",
            "name": "MSISDN 12025550105",
            "value": "12025550105",
            "administrativeState": "unlocked",
            "resourceSpecification": {"id": "msisdn", "@type": "ResourceSpecificationRef"},
        },
    )

    def snapshot():
        return (
            sorted(n["value"] for n in api.get(f"{numbers}?limit=100").json()),
            names(api, SPEC),
            names(api, OFFERING),
        )

    # 19 missing numbers, the specification, 2 x (price + offering)
    check_cycle(stand, scripts("seed_numbers"), snapshot, 19 + 1 + 4)

    values, specs, offerings = snapshot()
    assert values == [str(12025550100 + i) for i in range(20)]
    spec = next(s for s in api.get(SPEC).json() if s["name"] == "Mobile Voice & Data (numbered)")
    assert [r["id"] for r in spec["resourceSpecification"]] == ["msisdn"]
    created = {
        o["externalIdentifier"][0]["id"]: o
        for o in api.get(OFFERING).json()
        if o["externalIdentifier"][0]["id"].startswith("MOB-N")
    }
    assert sorted(created) == ["MOB-NL", "MOB-NS"]
    assert created["MOB-NS"]["productSpecification"]["id"] == spec["id"]
    price = api.get(
        f"{CATALOG}/productOfferingPrice/{created['MOB-NL']['productOfferingPrice'][0]['id']}"
    )
    assert price.json()["price"]["value"] == 129


def test_catalog_tree(api, scripts):
    stand = Stand(api, scripts)
    for spec_name, offering_name, code in [
        ("Mobile Voice & Data", "Mobile S", "MOB-S"),
        ("Home Internet", "Fiber 100", "NET-100"),
        ("TV", "TV Basic", "TV-BASE"),
    ]:
        spec = api.post(
            SPEC,
            {"@type": "ProductSpecification", "name": spec_name, "lifecycleStatus": "Active"},
        ).json()
        price = apidata.sellable_offering(api, name=offering_name, code=code)
        api.patch(
            f"{OFFERING}/{price['id']}",
            {"productSpecification": {"id": spec["id"], "@type": "ProductSpecificationRef"}},
        )
    mobile = api.post(
        CATEGORY, {"@type": "Category", "name": "Mobile Plans", "lifecycleStatus": "Active"}
    ).json()
    api.post(
        CATALOGS,
        {
            "@type": "ProductCatalog",
            "name": "Consumer Catalog 2026",
            "lifecycleStatus": "Active",
            "category": [{"id": mobile["id"], "@type": "CategoryRef"}],
        },
    )

    def snapshot():
        return (
            [(c["name"], len(c.get("productOffering", []))) for c in api.get(CATEGORY).json()],
            [(c["name"], len(c.get("category", []))) for c in api.get(CATALOGS).json()],
        )

    # 2 categories, 3 memberships (one per category), the Business catalog, and
    # the Consumer catalog gaining 2 categories
    check_cycle(stand, scripts("catalog_tree"), snapshot, 2 + 3 + 1 + 1)

    categories, catalogs = snapshot()
    assert sorted(categories) == [("Home Internet", 1), ("Mobile Plans", 1), ("TV", 1)]
    assert sorted(catalogs) == [("Business Catalog 2026", 2), ("Consumer Catalog 2026", 3)]


def test_a_script_needs_its_credentials_and_defaults_to_a_dry_run(scripts, monkeypatch, capsys):
    client = scripts("_client")
    for name in ("BSS_URL", "BSS_USER", "BSS_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    assert client.run("x", lambda c: None, []) == 1
    assert "BSS_URL" in capsys.readouterr().err

    seen = []
    monkeypatch.setenv("BSS_URL", "http://localhost:1")
    monkeypatch.setenv("BSS_USER", "u")
    monkeypatch.setenv("BSS_PASSWORD", "p")
    monkeypatch.setattr(client, "http_transport", lambda base: lambda *args: (200, {}, None))
    assert client.run("x", lambda c: seen.append(c.apply), []) == 0
    assert client.run("x", lambda c: seen.append(c.apply), ["--apply"]) == 0
    assert seen == [False, True]
