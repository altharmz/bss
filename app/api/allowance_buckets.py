"""Current funded allowances projected from the same summary as the usage screens."""

from decimal import Decimal
from itertools import groupby
from operator import itemgetter
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from app.api.account import account_href
from app.api.common import ref
from app.api.inventory import product_href
from app.api.resource_inventory import BASE as RESOURCE_BASE
from app.billing.core import midnight
from app.config import settings
from app.network import usage
from app.repository import buckets
from app.repository._sql import fetch_all


def current(conn, day, base, account_id=None):
    candidates = fetch_all(
        conn,
        """SELECT p.id, p.name, p.billing_account_id FROM product p
           WHERE EXISTS (SELECT 1 FROM funded_interval f
                         WHERE f.product_id = p.id AND f.period @> %s::date)
           AND (%s::uuid IS NULL OR p.billing_account_id = %s)
           ORDER BY p.billing_account_id, p.id""",
        (day, account_id, account_id),
    )
    for _, products in groupby(candidates, key=itemgetter("billing_account_id")):
        yield from _account_buckets(conn, day, base, list(products))


def _account_buckets(conn, day, base, candidates):
    numbers = {
        n["product_id"]: n for n in buckets.numbers(conn, [p["id"] for p in candidates], day)
    }
    result = {}
    tz = ZoneInfo(settings.billing_tz)
    for product in candidates:
        pid, aid = product["id"], product["billing_account_id"]
        summary = usage.summary(conn, pid, day)
        for entry in summary["allowances"]:
            if entry["included"] <= 0:
                continue
            kind, zone = entry["kind"], entry["zone"]
            period = entry["pool"] or summary["period"]
            start, end = period["startDate"], period["endDate"]
            owner = f"pool/{aid}" if entry["pooled"] else f"product/{pid}"
            uid = str(uuid5(NAMESPACE_URL, f"{base}/{owner}/{start}/{zone}/{kind}"))
            if uid not in result:
                divisor, unit = {
                    "data": (usage.MIB, "MB"),
                    "voice": (60, "min"),
                    "sms": (1, "msg"),
                }[kind]
                remaining = Decimal(entry["remaining"]) / divisor
                result[uid] = {
                    "id": uid,
                    "href": f"{base}/bucket/{uid}",
                    "@type": "Bucket",
                    "name": f"{zone} {kind}",
                    "usageType": kind,
                    "status": "active",
                    "remainingValue": {"amount": remaining, "units": unit},
                    "validFor": {
                        "startDateTime": midnight(start, tz),
                        "endDateTime": midnight(end, tz),
                    },
                    "isShared": entry["pooled"],
                    "product": [],
                    "logicalResource": [],
                    "partyAccount": ref(
                        "BillingAccount", account_href(aid), aid, ref_type="PartyAccountRef"
                    ),
                }
            body = result[uid]
            body["product"].append(ref("Product", product_href(pid), pid, product["name"]))
            if number := numbers.get(pid):
                nid = number["id"]
                body["logicalResource"].append(
                    ref("LogicalResource", f"{RESOURCE_BASE}/resource/{nid}", nid, number["name"])
                )
    return result.values()
