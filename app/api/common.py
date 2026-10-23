"""TMF630 building blocks shared by every API: request models, Money, TimePeriod,
references, and the helpers that turn database rows into response bodies."""

import re
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Request
from psycopg.types.range import Range
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    create_model,
    field_validator,
)
from pydantic.alias_generators import to_camel

from app.api import specschema
from app.api.errors import (
    Problem,
    bad_request,
    conflict,
    not_found,
    problems_error,
    unprocessable,
    validation_error,
    validation_problems,
)
from app.api.tmfjson import TmfJSONResponse
from app.config import settings
from app.repository._sql import Row, select_by_id, update_by_id

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
    at_base_type: str | None = Field(default=None, alias="@baseType", min_length=1)
    at_schema_location: str | None = Field(default=None, alias="@schemaLocation", min_length=1)


class Ref(TmfModel):
    """A reference to another resource. Only `id` is used; href and name are accepted as
    sent. `@referredType` is "the actual type of the target instance" (TMF630; the vendored
    specs' EntityRef): optional, but when sent it must be the type the response will state
    (422 otherwise) - a contradicting one is refused, never silently answered with another
    value. Where the type is fixed, use the subclass that names it (below); where it
    depends on the target (a party), the handler calls `check_referred_type` once it has
    loaded it."""

    # the one type this reference may name; None: checked by the handler
    referred_type: ClassVar[str | None] = None

    id: UUID
    href: str | None = None
    name: str | None = None
    at_referred_type: str | None = Field(default=None, alias="@referredType")

    @field_validator("at_referred_type")
    @classmethod
    def _actual_type(cls, value: str | None) -> str | None:
        if value is not None and cls.referred_type is not None and value != cls.referred_type:
            raise ValueError(f"must be {cls.referred_type} (the type of the referred resource)")
        return value


class BillingAccountRef(Ref):
    referred_type = "BillingAccount"


class RatedBillingAccountRef(BillingAccountRef):
    """The v5 specs' BillingAccountRef also states the account's ratingType: when sent
    it must be the account's mode (docs/decisions/accounts.md), else a 422
    (`account.check_rated`)."""

    rating_type: Literal["postpaid", "prepaid"] | None = None


class CustomerRef(Ref):
    referred_type = "Customer"


class CustomerBillRef(Ref):
    referred_type = "CustomerBill"


class ProductRef(Ref):
    referred_type = "Product"


class ProductOfferingRef(Ref):
    referred_type = "ProductOffering"


class ProductOfferingPriceRef(Ref):
    referred_type = "ProductOfferingPrice"


class ProductSpecificationRef(Ref):
    referred_type = "ProductSpecification"


def check_referred_type(reference: Ref, actual_type: str, name: str) -> None:
    """422 when a reference's `@referredType` contradicts the target's actual type."""
    sent = reference.at_referred_type
    if sent is not None and sent != actual_type:
        raise unprocessable(
            f"{name}.@referredType: must be {actual_type} (the type of the referred"
            f" resource), not {sent}"
        )


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


def documents_model(name: str, api: str, documents: tuple[str, ...]) -> type[TmfModel]:
    """A base model whose fields are attributes that are documents to us - stored as
    sent, no behaviour (a party's contact media, ...). Typed Any: `parse_documented`
    checks them against the spec. Being fields, they appear in the model's JSON schema
    (the MCP tools are built from it)."""
    fields: dict[str, Any] = {
        documents_field(document): (
            Any,
            Field(
                default=None,
                alias=document,
                description=f"{api} {document}, as the spec defines it",
            ),
        )
        for document in documents
    }
    return create_model(name, __base__=TmfModel, **fields)


def documents_field(document: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", document).lower()


def parse_documented[M: BaseModel](
    model: type[M],
    body: dict[str, Any],
    documents: tuple[str, ...],
    api: str,
    schema: str,
    found: list[Problem] | None = None,
) -> tuple[M, dict[str, Any]]:
    """`parse`, for a model with document fields (`documents_model`): those are checked
    against the spec schema that declares them (e.g. Individual_FVO), everything else by
    the model, and every problem of both is in the one error (with `found`: problems the
    handler found in attributes it took out of the body first). Returns the model and the
    documents sent, normalized (date-times in UTC); a null stays None (in a merge patch:
    remove it)."""
    for name in SERVER_MANAGED:
        if name in body:
            raise bad_request(f"{name} is read-only")
    data, documents, invalid, problems = check_documented(model, body, documents, api, schema)
    problems = [*invalid, *(found or ()), *problems]
    if problems or data is None:
        raise problems_error(problems)
    return data, documents


def check_documented[M: BaseModel](
    model: type[M],
    body: dict[str, Any],
    documents: tuple[str, ...],
    api: str,
    schema: str,
    path: str = "",
) -> tuple[M | None, dict[str, Any], list[Problem], list[Problem]]:
    """`parse_documented`'s checks without raising, for a body inside another (an
    offering's inline price): the model (None if invalid), the documents sent, and the
    model's and the spec's problems, reported under `path`."""
    sent = {name: body[name] for name in documents if name in body}
    values, problems = specschema.check(
        api, schema, {name: value for name, value in sent.items() if value is not None}
    )
    try:
        data: M | None = model.model_validate(body)
        invalid: list[Problem] = []
    except ValidationError as exc:
        data = None
        invalid = validation_problems(exc.errors(include_url=False, include_context=False))
    if path:
        invalid, problems = (
            [(f"{path}.{loc}" if loc else path, msg, unknown) for loc, msg, unknown in found]
            for found in (invalid, problems)
        )
    return data, {name: values.get(name) for name in sent}, invalid, problems


def merge_documents(current: dict[str, Any], sent: dict[str, Any]) -> dict[str, Any]:
    """A merge patch of the stored documents: an attribute sent replaces the stored one
    whole, null removes it."""
    merged = current | sent
    return {name: value for name, value in merged.items() if value is not None}


def check_type(body: dict[str, Any], *resource_types: str) -> None:
    """POST/PATCH may name the resource's type, never silently substitute another."""
    if "@type" in body and body["@type"] not in resource_types:
        raise bad_request(f"@type is read-only (this resource is a {resource_types[0]})")


def not_null(data: BaseModel, *names: str) -> None:
    """In a merge patch, null means "remove". These attributes cannot be removed."""
    for name in names:
        if name in data.model_fields_set and getattr(data, name) is None:
            raise unprocessable(f"{to_camel(name)} cannot be null")


# Set (overwritten) by the shared Caddy from the basic-auth user, or by the MCP connector
# as "mcp:<user or token name>"; absent locally. A basic-auth user cannot contain ":", so
# an operator with ":" always comes from MCP. A value outside this shape is reported as no
# operator rather than echoed.
OPERATOR = re.compile(r"^[A-Za-z0-9._@:-]{1,64}$")


def operator_of(request: Request) -> str | None:
    """The operator established by authentication, never an unverified header."""
    value = getattr(request.state, "operator", "")
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
    row_id: UUID | str,
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


# --- nothing a client sends is dropped or rewritten (docs/decisions/tmf-api.md,
# "Nothing dropped or rewritten") ---

META_COLUMNS = {"at_base_type": "base_type", "at_schema_location": "schema_location"}


def meta_columns(data: BaseModel) -> dict[str, str | None]:
    """The `@baseType`/`@schemaLocation` a body carried, as column -> value (in a PATCH,
    null removes)."""
    return {
        column: getattr(data, name)
        for name, column in META_COLUMNS.items()
        if name in data.model_fields_set
    }


def with_meta(conn: psycopg.Connection, table: str, row: Row, data: BaseModel) -> Row:
    """Store the body's `@baseType`/`@schemaLocation` on the row just created or changed."""
    values = meta_columns(data)
    return update_by_id(conn, table, row["id"], values) if values else row


def meta_body(row: Row) -> dict[str, Any]:
    """`@baseType`/`@schemaLocation` as stored (absent when the client sent none)."""
    return without_nulls(
        {"@baseType": row.get("base_type"), "@schemaLocation": row.get("schema_location")}
    )


def take_id(body: dict[str, Any]) -> UUID | None:
    """A client-chosen `id` on POST (the specs' Addressable_FVO has one). Ours are UUIDs,
    so it must be one: our restriction of the spec's string."""
    if "id" not in body:
        return None
    raw = body.pop("id")
    try:
        if not isinstance(raw, str):
            raise ValueError
        return UUID(raw)
    except ValueError:
        raise unprocessable("id must be a UUID (this server's ids are UUIDs)") from None


def claim_id(conn: psycopg.Connection, table: str, uid: UUID | None) -> None:
    """409 when a client-chosen id is in use. The lock serializes two requests for the
    same id, so the second sees the first one's row instead of a key violation."""
    if uid is None:
        return
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{table}:{uid}",))
    if select_by_id(conn, table, uid) is not None:
        raise conflict(f"id {uid} is already in use")


# Test instrumentation only (tests/tmf_gaps.py): called with the names each
# take_server_set took, because a server-set attribute is compared whole and so cannot
# be probed below its top level over HTTP. None in the app.
on_server_set: Callable[[set[str]], None] | None = None


def take_server_set(body: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    """Attributes the specs let a client send but the server sets (a job's status, an
    account's balance, ...): taken out of the body before it is parsed, then compared
    with what the server holds (`check_server_set`)."""
    taken = {name: body.pop(name) for name in names if name in body}
    if on_server_set is not None:
        on_server_set(set(taken))
    return taken


def check_server_set(sent: dict[str, Any], actual: dict[str, Any]) -> None:
    """422 unless every server-set attribute sent equals the resource's value: accepted,
    never overridden. For a POST, `actual` is the new resource (the 422 rolls it back)."""
    for name, value in sent.items():
        if not same(value, actual.get(name)):
            raise unprocessable(f"{name} is set by the server: send its current value or omit it")


def same(sent: Any, actual: Any) -> bool:
    """Whether a value from a request equals one of a response body. An object sent may
    leave attributes out (a reference with only `id`); date-times compare as instants,
    numbers as numbers."""
    if isinstance(sent, dict):
        return isinstance(actual, dict) and all(
            key in actual and same(value, actual[key]) for key, value in sent.items()
        )
    if isinstance(sent, list):
        return (
            isinstance(actual, list) and len(sent) == len(actual) and all(map(same, sent, actual))
        )
    if isinstance(actual, datetime):
        try:
            parsed = datetime.fromisoformat(sent) if isinstance(sent, str) else None
        except ValueError:
            return False
        return parsed is not None and parsed.tzinfo is not None and parsed == actual
    if isinstance(actual, (int, Decimal)) and not isinstance(actual, bool):
        return (
            isinstance(sent, (int, Decimal))
            and not isinstance(sent, bool)
            and Decimal(sent) == Decimal(actual)
        )
    if isinstance(actual, UUID):
        return sent == str(actual)
    return sent == actual
