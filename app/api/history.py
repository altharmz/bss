"""TMF654 v4 balanceActionHistory: every top-up, reservation, adjustment and transfer, read-only.

An entry has the id of its action (the `balance_action` row) and is that action's body re-labelled:
`@type` stays the action's own subclass with `@baseType: BalanceActionHistory`, and `href`
names the history. Each action appears once; a cancellation changes its status and a
reversal is not an action. The schema asks every entry for `receiverLogicalResource`, which
only a transfer has; the other kinds omit it rather than invent a number (docs/decisions/prepaid.md,
the single schema exception). Actions that an order or a renewal owns are listed too.
"""

from fastapi import APIRouter, Request

from app.api import listing
from app.api.adjust import adjust_body
from app.api.balance import BASE
from app.api.common import parse_id
from app.api.deps import Tx
from app.api.errors import not_found
from app.api.reserve import reserve_body
from app.api.tmfjson import TmfJSONResponse
from app.api.topup import topup_body
from app.api.transfer import transfer_body
from app.repository import balance_actions as repo
from app.repository._sql import Row

RESOURCE = "balanceActionHistory"
router = APIRouter(prefix=BASE)

# Each kind's body and the TMF654 subclass it is.
KINDS = {
    "topup": (topup_body, "TopupBalance"),
    "reservation": (reserve_body, "ReserveBalance"),
    "adjustment": (adjust_body, "AdjustBalance"),
    "transfer": (transfer_body, "TransferBalance"),
}
ATTRIBUTES = (
    "confirmationDate",
    "description",
    "isAutoTopup",
    "reason",
    "requestedDate",
    "adjustType",
    "amount",
    "balanceTopup",
    "bucket",
    "channel",
    "costOwner",
    "logicalResource",
    "partyAccount",
    "paymentMethod",
    "product",
    "receiver",
    "receiverBucket",
    "receiverBucketUsageType",
    "receiverLogicalResource",
    "receiverProduct",
    "relatedParty",
    "requestor",
    "status",
    "transferCost",
    "usageType",
    "validFor",
)


def history_body(conn, row: Row) -> dict:
    build, subclass = KINDS[row["kind"]]
    body = build(conn, row)
    body["href"] = f"{BASE}/{RESOURCE}/{row['id']}"
    body["@type"] = subclass
    body["@baseType"] = "BalanceActionHistory"
    return body


@router.get(f"/{RESOURCE}")
def list_history(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "id": ("id", listing.as_uuid),
            "status": ("status", listing.as_str),
            "bucket.id": ("bucket_id", listing.as_uuid),
            "receiverBucket.id": ("receiver_bucket_id", listing.as_uuid),
            "partyAccount.id": ("billing_account_id", listing.as_uuid),
        },
        attributes=ATTRIBUTES,
    )
    with tx() as conn:
        rows, more = repo.page(conn, query.where, query.offset, query.limit, kind=None)
        bodies = [history_body(conn, row) for row in rows]
    return listing.list_response(bodies, more, query)


@router.get(f"/{RESOURCE}/{{history_id}}")
def get_history(history_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = repo.get(conn, parse_id(RESOURCE, history_id), kind=None)
        if row is None:
            raise not_found(RESOURCE, history_id)
        body = history_body(conn, row)
    return TmfJSONResponse(listing.select_fields(body, fields))
