"""Local-only MCP smoke of the prepaid tools: one top-up and one purchase, each with an
explicit idempotency_key and replayed once (the retry must return the first answer).

Usage (make mcp-smoke does this with a throwaway write token it revokes afterwards):
    MCP_SMOKE_TOKEN=... python3 scripts/mcp-prepaid-smoke.py http://localhost:8090

Creates demo data in the LOCAL stack (a catalog entry, a customer, a prepaid account)
and retires the catalog entries at the end; refuses any host but localhost, so it can
never reach the deployed instance.
"""

import json
import os
import sys
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from itertools import count
from urllib.parse import urlsplit

BASE = (sys.argv[1] if len(sys.argv) > 1 else "").rstrip("/")
TOKEN = os.environ.get("MCP_SMOKE_TOKEN", "")
ids = count(1)
# a price may not start in the past, so the plan starts at the next UTC midnight and the
# purchase is a future-start order for noon the day after (whole business dates, any
# zone; accepted now, the hold is taken now)
TOMORROW = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
AFTER = (datetime.now(UTC) + timedelta(days=2)).strftime("%Y-%m-%dT12:00:00Z")


def fail(message: str) -> None:
    sys.exit(f"\033[1;31mMCP PREPAID SMOKE FAILED:\033[0m {message}")


if urlsplit(BASE).hostname not in ("localhost", "127.0.0.1") or not TOKEN:
    fail("needs a localhost base URL and MCP_SMOKE_TOKEN (local only)")


def call(tool: str, arguments: dict) -> dict:
    message = {
        "jsonrpc": "2.0",
        "id": next(ids),
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    request = urllib.request.Request(  # noqa: S310 - BASE is checked to be http(s)://localhost
        f"{BASE}/mcp",
        data=json.dumps(message).encode(),
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - as above
        result = json.load(response)["result"]
    return {"isError": result["isError"], **json.loads(result["content"][0]["text"])}


def ok(tool: str, arguments: dict, status: int = 201) -> dict:
    answer = call(tool, arguments)
    if answer.get("status") != status:
        fail(f"{tool} answered {answer}")
    return answer["body"]


probe = call("get_context", {})
if probe.get("status") == 401:
    # make start-task writes BSS_SERVICE_SECRET for new tasks; older task files lack it
    fail(
        "the local app does not accept the connector: BSS_SERVICE_SECRET is missing from this"
        " task's secret file. Add a line BSS_SERVICE_SECRET=<openssl rand -hex 32> to"
        " ~/.config/bss/tasks/<task>/.env, then make up"
    )

spec = ok(
    "create_specification",
    {"body": {"@type": "ProductSpecification", "name": "Smoke", "lifecycleStatus": "Active"}},
)
price = ok(
    "create_price",
    {
        "body": {
            "@type": "ProductOfferingPrice",
            "name": "Monthly",
            "lifecycleStatus": "Active",
            "priceType": "recurring",
            "recurringChargePeriodType": "monthly",
            "price": {"unit": "USD", "value": "10.00"},
            "validFor": {"startDateTime": TOMORROW},
        }
    },
)
offering = ok(
    "create_offering",
    {
        "body": {
            "@type": "ProductOffering",
            "name": "Smoke prepaid plan",
            "lifecycleStatus": "Active",
            "validFor": {"startDateTime": TOMORROW},
            "productSpecification": {"id": spec["id"]},
            "productOfferingPrice": [{"id": price["id"]}],
        }
    },
)
person = ok("create_individual", {"body": {"givenName": "Smoke", "familyName": "Prepaid"}})
customer = ok(
    "create_customer",
    {
        "body": {
            "@type": "Customer",
            "name": "Smoke Prepaid",
            "engagedParty": {"id": person["id"], "@referredType": "Individual"},
        }
    },
)
account = ok(
    "create_account",
    {
        "body": {
            "@type": "BssBillingAccount",
            "@baseType": "BillingAccount",
            "name": "Smoke prepaid",
            "billCycleDay": 1,
            "currency": "USD",
            "ratingType": "prepaid",
            "relatedParty": [
                {
                    "@type": "RelatedPartyRefOrPartyRoleRef",
                    "role": "customer",
                    "partyOrPartyRole": {
                        "id": customer["id"],
                        "name": customer["name"],
                        "@type": "PartyRoleRef",
                        "@referredType": "Customer",
                    },
                }
            ],
        }
    },
)
buckets = call("list_buckets", {"filters": {"partyAccount.id": account["id"]}})
if buckets.get("status") != 200 or len(buckets["body"]) != 1:
    fail(f"the prepaid account has no single bucket: {buckets}")
bucket = buckets["body"][0]

run = uuid.uuid4().hex[:12]

# one top-up, sent twice with the same key: the second is the first's answer
topup = {
    "idempotency_key": f"smoke-topup-{run}",
    "body": {
        "usageType": "monetary",
        "amount": {"amount": "20.00", "units": "USD"},
        "bucket": {"id": bucket["id"]},
        "partyAccount": {"id": account["id"]},
    },
}
first = ok("create_topup_balance", topup)
if first["status"] != "completed":
    fail(f"the top-up is {first['status']}, not completed")
if ok("create_topup_balance", topup) != first:
    fail("the top-up retry did not replay the first answer")
print(f"  ok  create_topup_balance 20.00 USD -> {first['id']} completed, retry replayed")

# one purchase, the same way: accepted (inProgress), the billing worker completes it
purchase = {
    "idempotency_key": f"smoke-buy-{run}",
    "body": {
        "@type": "ProductOrder",
        "requestedStartDate": AFTER,
        "billingAccount": {"id": account["id"], "@type": "BillingAccountRef"},
        "productOrderItem": [
            {
                "@type": "ProductOrderItem",
                "id": "1",
                "action": "add",
                "productOffering": {"id": offering["id"], "@type": "ProductOfferingRef"},
            }
        ],
    },
}
order = ok("create_order", purchase)
if order["state"] == "rejected":
    fail(f"the purchase was rejected: {order}")
if ok("create_order", purchase) != order:
    fail("the purchase retry did not replay the first answer")
print(f"  ok  create_order -> {order['id']} {order['state']}, retry replayed")

# retire what the run created, so the local catalog does not fill up with smoke entries
ok(
    "patch_offering",
    {"offering_id": offering["id"], "body": {"lifecycleStatus": "Retired", "isSellable": False}},
    status=200,
)
ok(
    "patch_specification",
    {"spec_id": spec["id"], "body": {"lifecycleStatus": "Retired"}},
    status=200,
)
print("  ok  the smoke offering and specification are Retired and not sellable")
print("mcp prepaid smoke: all checks passed")
