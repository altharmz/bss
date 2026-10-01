"""TMF630 list conventions: offset/limit paging, fields= selection, equality filters and
the X-Total-Count / X-Result-Count headers.

Lists always answer 200, including partial pages: none of the vendored specs declares 206
for a list operation.
"""

from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Request

from app.api.errors import bad_request
from app.api.tmfjson import TmfJSONResponse

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# Always returned, whatever fields= says.
ALWAYS = ("id", "href", "@type")

Converter = Callable[[str], Any]


def as_str(raw: str) -> str:
    return raw


def as_uuid(raw: str) -> UUID:
    return UUID(raw)


def as_bool(raw: str) -> bool:
    if raw not in ("true", "false"):
        raise ValueError("expected true or false")
    return raw == "true"


@dataclass(frozen=True)
class ListQuery:
    offset: int
    limit: int
    where: dict[str, Any]
    fields: frozenset[str] | None


def _int_param(request: Request, name: str, default: int, minimum: int) -> int:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise bad_request(f"{name} must be an integer") from None
    if value < minimum:
        raise bad_request(f"{name} must be at least {minimum}")
    return value


def parse_fields(request: Request, attributes: Collection[str]) -> frozenset[str] | None:
    raw = request.query_params.get("fields")
    if raw is None:
        return None
    names = frozenset(name.strip() for name in raw.split(",") if name.strip())
    unknown = sorted(names - set(attributes) - set(ALWAYS))
    if unknown:
        raise bad_request(f"fields: unknown attribute(s) {', '.join(unknown)}")
    return names


def parse_list_query(
    request: Request,
    *,
    filters: dict[str, tuple[str, Converter]],
    attributes: Collection[str],
) -> ListQuery:
    """`filters` maps a query parameter to (database column, converter). Any other query
    parameter is a 400 - a silently ignored filter would return the wrong list."""
    limit = _int_param(request, "limit", DEFAULT_LIMIT, 1)
    if limit > MAX_LIMIT:
        raise bad_request(f"limit must be at most {MAX_LIMIT}")
    where: dict[str, Any] = {}
    for name, raw in request.query_params.multi_items():
        if name in ("offset", "limit", "fields"):
            continue
        if name not in filters:
            allowed = ", ".join(sorted(filters)) or "none"
            raise bad_request(f"unknown query parameter {name} (filters: {allowed})")
        column, convert = filters[name]
        if column in where:
            raise bad_request(f"{name} given more than once")
        try:
            where[column] = convert(raw)
        except ValueError:
            raise bad_request(f"invalid value for {name}") from None
    return ListQuery(
        offset=_int_param(request, "offset", 0, 0),
        limit=limit,
        where=where,
        fields=parse_fields(request, attributes),
    )


def parse_page(request: Request) -> tuple[int, int]:
    """offset and limit with the same rules as parse_list_query, for lists that take no
    filters and no fields=; any other query parameter is a 400."""
    for name in request.query_params:
        if name not in ("offset", "limit"):
            raise bad_request(f"unknown query parameter {name}")
    limit = _int_param(request, "limit", DEFAULT_LIMIT, 1)
    if limit > MAX_LIMIT:
        raise bad_request(f"limit must be at most {MAX_LIMIT}")
    return _int_param(request, "offset", 0, 0), limit


def retrieve_fields(request: Request, attributes: Collection[str]) -> frozenset[str] | None:
    """Query handling for GET by id: only fields= is allowed."""
    for name in request.query_params:
        if name != "fields":
            raise bad_request(f"unknown query parameter {name}")
    return parse_fields(request, attributes)


def select_fields(body: dict[str, Any], fields: frozenset[str] | None) -> dict[str, Any]:
    if fields is None:
        return body
    return {key: value for key, value in body.items() if key in fields or key in ALWAYS}


def list_response(items: list[dict[str, Any]], total: int, query: ListQuery) -> TmfJSONResponse:
    return TmfJSONResponse(
        [select_fields(item, query.fields) for item in items],
        headers={"X-Total-Count": str(total), "X-Result-Count": str(len(items))},
    )
