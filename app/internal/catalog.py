"""POST /internal/v1/productOffering/{id}/priceChange: change an offering's recurring
price atomically (D-W21). Ours - TMF620 cannot express it.

In TMF620 a price change is two requests: end the current price, attach a successor. The
successor cannot be attached first (it would overlap the still open-ended price: the
no-overlap EXCLUDE constraint), and ending first is refused while a product is live,
because that product would be left without a price (D-W18). TMF has no cross-request
transaction, so the change is one internal operation: in one transaction it ends the
current price exactly at midnight of `validFrom` and creates the successor, attached,
from that moment - never a gap, never an overlap. All of it happens or none of it.

Existing products pick up the new price from their first billing period that starts on
or after `validFrom` (the core prices each period at its first billable day); new orders
from `validFrom`.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from psycopg.types.range import Range
from pydantic import Field, field_validator

from app.api.common import Money, TmfModel, money, parse, parse_id
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import conflict, not_found, unprocessable
from app.api.tmfjson import TmfJSONResponse
from app.config import settings
from app.repository import catalog

BASE = "/internal/v1/productOffering"
TMF620 = "/tmf-api/productCatalogManagement/v5"
router = APIRouter(prefix=BASE)


def _price_href(price_id: UUID) -> str:
    return f"{TMF620}/productOfferingPrice/{price_id}"


class PriceChange(TmfModel):
    amount: Decimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    valid_from: date

    @field_validator("amount", mode="before")
    @classmethod
    def _exact(cls, value: Any) -> Decimal:
        # the rules of TMF Money: a JSON number, at most 2 decimals, never rounded
        return Money.model_validate({"unit": "XXX", "value": value}).value

    @field_validator("valid_from", mode="before")
    @classmethod
    def _business_date(cls, value: Any) -> Any:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError("must be a business date YYYY-MM-DD")
        return value


@router.post("/{offering_id}/priceChange")
def price_change(offering_id: str, body: JsonBody, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    data = parse(PriceChange, body)
    if data.amount < 0:
        raise unprocessable("amount must not be negative")
    tz = ZoneInfo(settings.billing_tz)
    tomorrow = clock.today() + timedelta(days=1)
    if data.valid_from < tomorrow:
        raise unprocessable(
            f"validFrom must be {tomorrow} or later (the next midnight in {tz.key}):"
            " a price never starts in the past, and products are priced at midnight"
        )
    switch = datetime.combine(data.valid_from, time.min, tzinfo=tz)

    with tx() as conn:
        uid = parse_id("productOffering", offering_id)
        offering = catalog.lock_offering(conn, uid)
        if offering is None:
            raise not_found("productOffering", offering_id)
        prices = catalog.recurring_prices_from(
            conn, offering_id=uid, currency=data.currency, at=switch
        )
        if not prices or prices[-1]["valid_for"].upper is not None:
            raise conflict(
                f"productOffering has no open-ended recurring price in {data.currency} to"
                " change; attach the first price through TMF620"
            )
        old = prices[-1]
        if len(prices) > 1 or old["valid_for"].lower >= switch:
            raise conflict(
                f"a recurring price in {data.currency} already starts on or after"
                f" {data.valid_from}: change the price from a later date"
            )
        # end first, then insert: the EXCLUDE constraint is checked per statement
        ended = catalog.update_product_offering_price(
            conn, old["id"], {"valid_for": Range(old["valid_for"].lower, switch, "[)")}
        )
        new = catalog.create_product_offering_price(
            conn,
            product_offering_id=uid,
            price_type="recurring",
            recurring_period="monthly",
            amount=data.amount,
            currency=data.currency,
            valid_for=Range(switch, None, "[)"),
            name=f"{offering['name']} monthly from {data.valid_from}",
            lifecycle_status=old["lifecycle_status"],
        )
    new_href = _price_href(new["id"])
    return TmfJSONResponse(
        {
            "productOffering": {"id": str(uid), "href": f"{TMF620}/productOffering/{uid}"},
            "endedPrice": {
                "id": str(ended["id"]),
                "href": _price_href(ended["id"]),
                "endsOn": data.valid_from,  # the first day it no longer prices
            },
            "newPrice": {
                "id": str(new["id"]),
                "href": new_href,
                "price": money(new["amount"], new["currency"]),
                "validFrom": data.valid_from,
            },
        },
        status_code=201,
        headers={"Location": new_href},
    )
