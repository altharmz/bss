"""D1: billing accounts whose name does not start with their party's name are renamed
`<given> <family> main` (an individual) or `<organization> corporate` (an organization).
Only `name` is patched. A second run finds nothing to do.

    BSS_URL=... BSS_USER=... BSS_PASSWORD=... python3 rename_accounts.py [--apply]
"""

from typing import Any

from _client import Client, ScriptError, run

ACCOUNTS = "/tmf-api/accountManagement/v5/billingAccount"
CUSTOMERS = "/tmf-api/customer/v5/customer"
PARTIES = {
    "Individual": "/tmf-api/partyManagement/v5/individual",
    "Organization": "/tmf-api/partyManagement/v5/organization",
}


def party_name(client: Client, customer_id: str, cache: dict[str, tuple[str, str]]) -> tuple:
    """(the party's name, the suffix an account name gets) of a customer."""
    if customer_id not in cache:
        engaged = client.get(f"{CUSTOMERS}/{customer_id}")["engagedParty"]
        kind = engaged.get("@referredType")
        if kind not in PARTIES:
            raise ScriptError(f"customer {customer_id} engages a {kind!r}, not a known party")
        party = client.get(f"{PARTIES[kind]}/{engaged['id']}")
        if kind == "Individual":
            cache[customer_id] = (f"{party['givenName']} {party['familyName']}", "main")
        else:
            cache[customer_id] = (party["name"], "corporate")
    return cache[customer_id]


def customer_of(account: dict[str, Any]) -> str | None:
    for related in account.get("relatedParty", []):
        if related.get("role") == "customer":
            return related["partyOrPartyRole"]["id"]
    return None


def work(client: Client) -> None:
    cache: dict[str, tuple[str, str]] = {}
    for account in client.all(ACCOUNTS):
        customer_id = customer_of(account)
        if customer_id is None:
            print(f"skip: {account['name']!r} ({account['id']}) has no customer")
            continue
        name, suffix = party_name(client, customer_id, cache)
        if account["name"].startswith(name):
            continue
        new = f"{name} {suffix}"
        client.patch(
            f"{ACCOUNTS}/{account['id']}",
            {"name": new},
            f"rename account {account['id']} {account['name']!r} -> {new!r}",
        )


if __name__ == "__main__":
    raise SystemExit(run(__doc__.split("\n\n")[0], work))
