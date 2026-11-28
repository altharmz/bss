"""TMF677 synchronous usage consumption reports."""

from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Request
from pydantic import Field

from app.api import allowance_buckets, balance, listing, tmfjson
from app.api.common import BillingAccountRef, ProductRef, Ref, TmfModel, parse, parse_id
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import not_found, unprocessable
from app.repository import usage
from app.repository._sql import fetch_one

BASE = "/tmf-api/usageConsumption/v5"
router = APIRouter(prefix=BASE)
ATTRIBUTES = ("creationDate", "bucket", "product", "partyAccount", "logicalResource")


class LogicalResourceRef(Ref):
    referred_type = "LogicalResource"


class ReportQuery(TmfModel):
    at_type: Literal["UsageConsumptionReport"] = Field(
        default="UsageConsumptionReport", alias="@type"
    )
    product: list[ProductRef] = Field(default_factory=list)
    party_account: list[BillingAccountRef] = Field(default_factory=list)
    logical_resource: list[LogicalResourceRef] = Field(default_factory=list)


@router.post("/usageConsumptionReport", status_code=201)
def create_usage_consumption_report(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> tmfjson.TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    query = parse(ReportQuery, body)
    selectors = [
        (key, item)
        for key, refs in (
            ("product", query.product),
            ("partyAccount", query.party_account),
            ("logicalResource", query.logical_resource),
        )
        for item in refs
    ]
    if len(selectors) != 1:
        raise unprocessable(
            "UsageConsumptionReport requires one product, account or logical resource reference"
        )
    key, selector = selectors[0]
    ref_type = {
        "product": "UsageConsumptionProductRef",
        "partyAccount": "PartyAccountRef",
        "logicalResource": "LogicalResourceRef",
    }[key]
    if selector.at_type is not None and selector.at_type != ref_type:
        raise unprocessable(f"{key}.@type must be {ref_type}")
    uid = uuid4()
    href = f"{BASE}/usageConsumptionReport/{uid}"
    report = {
        "id": str(uid),
        "href": href,
        "@type": "UsageConsumptionReport",
        "creationDate": clock.now(),
        "bucket": [],
        key: [{"id": str(selector.id), "@type": ref_type}],
    }
    with tx() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        day = clock.today()
        if key == "product":
            owner = usage.product_row(conn, selector.id)
        elif key == "logicalResource":
            owner = fetch_one(
                conn,
                """SELECT p.billing_account_id FROM number_assignment a
                JOIN product p ON p.id = a.product_id WHERE a.subscriber_number_id = %s
                AND a.resource_specification_id = 'msisdn' AND a.cancelled_at IS NULL
                AND a.valid_for @> %s::date""",
                (selector.id, day),
            )
        else:
            owner = fetch_one(
                conn,
                "SELECT id AS billing_account_id FROM billing_account WHERE id = %s",
                (selector.id,),
            )
        buckets = (
            allowance_buckets.current(conn, day, balance.BASE, owner["billing_account_id"])
            if owner
            else ()
        )
        for bucket in buckets:
            refs = [bucket[key]] if key == "partyAccount" else bucket[key]
            if any(r["id"] == str(selector.id) for r in refs):
                report["bucket"].append(
                    {
                        k: bucket[k]
                        for k in (
                            "id",
                            "href",
                            "@type",
                            "name",
                            "usageType",
                            "isShared",
                            "product",
                            "logicalResource",
                            "partyAccount",
                        )
                    }
                    | {
                        "bucketCounter": [
                            {
                                "@type": "BucketCounter",
                                "counterType": "remaining",
                                "value": bucket["remainingValue"],
                                "consumptionPeriod": bucket["validFor"],
                            }
                        ]
                    }
                )
        conn.execute(
            "INSERT INTO usage_consumption_report (id, body) VALUES (%s, %s::jsonb)",
            (uid, tmfjson.dumps(report)),
        )
    return tmfjson.TmfJSONResponse(
        listing.select_fields(report, fields), status_code=201, headers={"Location": href}
    )


@router.get("/usageConsumptionReport/{report_id}")
def get_usage_consumption_report(
    report_id: str, request: Request, tx: Tx
) -> tmfjson.TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = fetch_one(
            conn,
            "SELECT body::text AS body FROM usage_consumption_report WHERE id = %s",
            (parse_id("usageConsumptionReport", report_id),),
        )
    if row is None:
        raise not_found("usageConsumptionReport", report_id)
    return tmfjson.TmfJSONResponse(listing.select_fields(tmfjson.loads(row["body"]), fields))
