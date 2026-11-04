"""D3: test offerings and specifications become Retired and not sellable; test billing
accounts with no open product become Closed. Nothing is deleted.

A record is test data when its name or its code matches one of PATTERNS. A billing
account is a test account when its name matches one, or its customer's name does. The
data the GUI test created stays, as the review asks. A second run finds nothing to do.

    BSS_URL=... BSS_USER=... BSS_PASSWORD=... python3 retire_test_data.py [--apply]
"""

import re
from typing import Any

from _client import CATALOG, Client, run

PATTERNS = [
    re.compile(p)
    for p in (
        r"^MCP ",
        r"TMF-E2E-",
        r"Prepaid Verifier",
        r"MCP Coverage",
        r"MCP-DEMO-",
        r"MCP Isolated",
    )
]
ACCOUNTS = "/tmf-api/accountManagement/v5/billingAccount"
PRODUCTS = "/tmf-api/productInventory/v5/product"
CLOSED_PRODUCT = {"terminated", "cancelled", "aborted"}


def is_test(*values: str | None) -> bool:
    return any(v and p.search(v) for v in values for p in PATTERNS)


def codes(offering: dict[str, Any]) -> list[str]:
    return [i.get("id", "") for i in offering.get("externalIdentifier") or []]


def retire(client: Client, kind: str, record: dict[str, Any], changes: dict[str, Any]) -> None:
    client.patch(
        f"{CATALOG}/{kind}/{record['id']}",
        changes,
        f"retire {kind} {record['id']} {record['name']!r}",
    )


def work(client: Client) -> None:
    for offering in client.all(f"{CATALOG}/productOffering"):
        if not is_test(offering["name"], *codes(offering)):
            continue
        changes: dict[str, Any] = {}
        if offering.get("lifecycleStatus") != "Retired":
            changes["lifecycleStatus"] = "Retired"
        if offering.get("isSellable") is not False:
            changes["isSellable"] = False
        if changes:
            retire(client, "productOffering", offering, changes)
    for spec in client.all(f"{CATALOG}/productSpecification"):
        if is_test(spec["name"]) and spec.get("lifecycleStatus") != "Retired":
            retire(client, "productSpecification", spec, {"lifecycleStatus": "Retired"})
    for account in client.all(ACCOUNTS):
        customers = [
            r["partyOrPartyRole"].get("name")
            for r in account.get("relatedParty", [])
            if r.get("role") == "customer"
        ]
        if account.get("state") == "Closed" or not is_test(account["name"], *customers):
            continue
        products = client.all(f"{PRODUCTS}?billingAccount.id={account['id']}")
        if any(p.get("status") not in CLOSED_PRODUCT for p in products):
            print(f"keep: account {account['id']} {account['name']!r} has an open product")
            continue
        client.patch(
            f"{ACCOUNTS}/{account['id']}",
            {"state": "Closed"},
            f"close account {account['id']} {account['name']!r}",
        )


if __name__ == "__main__":
    raise SystemExit(run(__doc__.split("\n\n")[0], work))
