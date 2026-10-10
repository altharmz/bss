"""TMF630 building blocks shared by every API: request models, Money, TimePeriod,
references, and the helpers that turn database rows into response bodies."""

import re
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import Request
from psycopg.types.range import Range
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic.alias_generators import to_camel

from app.api.errors import bad_request, not_found, unprocessable, validation_error
from app.api.tmfjson import TmfJSONResponse
from app.config import settings

CENT = Decimal("0.01")
# numeric(12,2): at most 10 digits before the decimal point
MONEY_LIMIT = Decimal(10) ** 10

# Never settable by a client, on POST or PATCH.
SERVER_MANAGED = ("id", "href")


class TmfModel(BaseModel):
    """Base of every request model. Attribute names are camelCase on the wire. Unknown
    business attributes are rejected (we never silently drop data); the TMF meta
    attributes are always accepted."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    at_type: str | None = Field(default=None, alias="@type")
    at_base_type: str | None = Field(default=None, alias="@baseType")
    at_schema_location: str | None = Field(default=None, alias="@schemaLocation")


class Ref(TmfModel):
    """A reference to another resource. Only `id` matters; the rest is accepted as sent."""

    id: UUID
    href: str | None = None
    name: str | None = None
    at_referred_type: str | None = Field(default=None, alias="@referredType")


class Money(TmfModel):
    unit: str = Field(pattern=r"^[A-Z]{3}$")
    value: Decimal

    @field_validator("value", mode="before")
    @classmethod
    def _exact_cents(cls, value: Any) -> Decimal:
        # The body parser yields int or Decimal for JSON numbers. A float here would mean
        # the amount already went through binary floating point.
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            raise ValueError("must be a JSON number")
        amount = Decimal(value)
        if not amount.is_finite() or abs(amount) >= MONEY_LIMIT:
            raise ValueError("out of range for an amount")
        try:
            cents = amount.quantize(CENT)
        except InvalidOperation as exc:
            raise ValueError("out of range for an amount") from exc
        if cents != amount:
            raise ValueError("must have at most 2 decimal places (amounts are never rounded)")
        return cents


class TimePeriod(TmfModel):
    start_date_time: AwareDatetime | None = None
    end_date_time: AwareDatetime | None = None

    def to_range(self, name: str = "validFor") -> Range:
        """Half-open [start, end); no endDateTime means open-ended."""
        if self.start_date_time is None:
            raise unprocessable(f"{name}.startDateTime is required")
        if self.end_date_time is not None and self.end_date_time <= self.start_date_time:
            raise unprocessable(f"{name}.endDateTime must be after startDateTime")
        return Range(self.start_date_time, self.end_date_time, "[)")


def parse[M: BaseModel](
    model: type[M], body: dict[str, Any], *, read_only: tuple[str, ...] = ()
) -> M:
    """Validate a request body. Read-only attributes are a 400, unknown attributes a 400,
    everything else Pydantic objects to a 422."""
    for name in (*SERVER_MANAGED, *read_only):
        if name in body:
            raise bad_request(f"{name} is read-only")
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise validation_error(exc) from exc


def check_type(body: dict[str, Any], *resource_types: str) -> None:
    """PATCH may repeat @type (the spec's _MVO schemas require it) but not change it."""
    if "@type" in body and body["@type"] not in resource_types:
        raise bad_request(f"@type is read-only (this resource is a {resource_types[0]})")


def not_null(data: BaseModel, *names: str) -> None:
    """In a merge patch, null means "remove". These attributes cannot be removed."""
    for name in names:
        if name in data.model_fields_set and getattr(data, name) is None:
            raise unprocessable(f"{to_camel(name)} cannot be null")


# Set (overwritten) by the shared Caddy from the basic-auth user; absent locally. A value
# outside this shape is reported as no operator rather than echoed.
OPERATOR = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")


def operator_of(request: Request) -> str | None:
    """The operator from X-Remote-User; display only.

    docs/decisions/internal-api.md, "Trust in X-Remote-User".
    """
    value = request.headers.get("x-remote-user", "").strip()
    return value if OPERATOR.match(value) else None


def parse_id(resource: str, raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError:
        raise not_found(resource, raw) from None


# --- rows -> response bodies ---


def utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def money(amount: Decimal, currency: str) -> dict[str, Any]:
    return {"unit": currency, "value": amount}


def time_period(value: Range) -> dict[str, Any]:
    body = {"startDateTime": utc(value.lower)}
    if value.upper is not None:
        body["endDateTime"] = utc(value.upper)
    return body


def billing_tz() -> ZoneInfo:
    return ZoneInfo(settings.billing_tz)


def to_billing_date(value: datetime) -> date:
    """TMF sends date-times where we store dates: the calendar date in BILLING_TZ.
    2026-10-01T23:30:00Z is 2026-10-02 in Europe/Moscow."""
    return value.astimezone(billing_tz()).date()


def from_billing_date(value: date) -> datetime:
    """A stored date goes out as midnight of that day in BILLING_TZ."""
    return utc(datetime.combine(value, time.min, tzinfo=billing_tz()))


def date_period(value: Range) -> dict[str, Any]:
    """A daterange [start, end) as a TimePeriod of BILLING_TZ midnights."""
    return {
        "startDateTime": from_billing_date(value.lower),
        "endDateTime": from_billing_date(value.upper),
    }


def ref(
    resource_type: str,
    href: str,
    row_id: UUID,
    name: str | None = None,
    *,
    ref_type: str | None = None,
) -> dict[str, Any]:
    """A TMF reference. `@type` is `<Resource>Ref` unless the spec names it otherwise."""
    body: dict[str, Any] = {"id": str(row_id), "href": href}
    if name is not None:
        body["name"] = name
    body["@type"] = ref_type or f"{resource_type}Ref"
    body["@referredType"] = resource_type
    return body


def created(body: dict[str, Any]) -> TmfJSONResponse:
    return TmfJSONResponse(body, status_code=201, headers={"Location": body["href"]})


def columns(data: BaseModel, *, exclude: frozenset[str] = frozenset()) -> dict[str, Any]:
    """The attributes a PATCH body actually carried, as column -> value. The TMF meta
    attributes are accepted but are not columns."""
    names = {n for n in data.model_fields_set if not n.startswith("at_")} - exclude
    return data.model_dump(include=names)


def without_nulls(body: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in body.items() if value is not None}
