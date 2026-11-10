"""TMF630 list conventions: offset/limit paging, fields= selection, equality filters and
the X-Result-Count header.

No list counts its matching rows (#312, decided by the human 2026-11-10; a deliberate
deviation from TMF630's X-Total-Count, docs/decisions/tmf-api.md). A list reads limit + 1
rows and, when there are more, answers `Link: <the same request at the next offset>;
rel="next"` (RFC 8288).

Lists always answer 200, including partial pages: none of the vendored specs declares 206
for a list operation.
"""

from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi import Request

from app.api.errors import bad_request
from app.api.tmfjson import TmfJSONResponse

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# Always returned, whatever fields= says.
ALWAYS = ("id", "href", "@type")
# Selectable on every resource (TMF Extensible); present when the resource has a value.
META = ("@baseType", "@schemaLocation")

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
    next_link: str | None = None  # the Link header value when there is a next page


def next_link(request: Request, offset: int, limit: int) -> str:
    """`Link` value for the next page: the same request with offset moved by limit."""
    params = [(k, v) for k, v in request.query_params.multi_items() if k != "offset"]
    params.append(("offset", str(offset + limit)))
    return f'<{request.url.path}?{urlencode(params)}>; rel="next"'


def split_page(rows: list[Any], limit: int) -> tuple[list[Any], bool]:
    """Rows read with LIMIT limit + 1: the page, and whether there are more."""
    return rows[:limit], len(rows) > limit


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
    if len(request.query_params.getlist("fields")) > 1:
        raise bad_request("fields given more than once")
    raw = request.query_params.get("fields")
    if raw is None:
        return None
    names = frozenset(name.strip() for name in raw.split(",") if name.strip())
    unknown = sorted(names - set(attributes) - set(ALWAYS) - set(META))
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
    for name in ("offset", "limit"):
        if len(request.query_params.getlist(name)) > 1:
            raise bad_request(f"{name} given more than once")
    for name, raw in request.query_params.multi_items():
        if name in ("offset", "limit", "fields"):
            continue
        if name not in filters:
            allowed = ", ".join(sorted(filters)) or "none"
            raise bad_request(f"unknown query parameter {name} (filters: {allowed})")
        column, convert = filters[name]
        if column in where:
            raise bad_request(f"{name} given more than once")
        if "," in raw and (
            convert is not as_str or name not in ("name", "givenName", "familyName")
        ):
            raise bad_request(f"{name}: comma-separated filters are not supported")
        try:
            where[column] = convert(raw)
        except ValueError:
            raise bad_request(f"invalid value for {name}") from None
    offset = _int_param(request, "offset", 0, 0)
    return ListQuery(
        offset=offset,
        limit=limit,
        where=where,
        fields=parse_fields(request, attributes),
        next_link=next_link(request, offset, limit),
    )


def parse_page(request: Request, extra: Collection[str] = ()) -> tuple[int, int]:
    """offset and limit with the same rules as parse_list_query, for lists that take no
    fields=; any other query parameter is a 400, except the `extra` ones the caller reads
    itself (a filter)."""
    for name in request.query_params:
        if name not in ("offset", "limit", *extra):
            raise bad_request(f"unknown query parameter {name}")
    limit = _int_param(request, "limit", DEFAULT_LIMIT, 1)
    if limit > MAX_LIMIT:
        raise bad_request(f"limit must be at most {MAX_LIMIT}")
    return _int_param(request, "offset", 0, 0), limit


def no_query(request: Request) -> None:
    """Query handling for DELETE: none is allowed."""
    for name in request.query_params:
        raise bad_request(f"unknown query parameter {name}")


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


def paging_headers(count: int, more: bool, link: str | None) -> dict[str, str]:
    headers = {"X-Result-Count": str(count)}
    if more and link:
        headers["Link"] = link
    return headers


def list_response(items: list[dict[str, Any]], more: bool, query: ListQuery) -> TmfJSONResponse:
    """A page of a list: `more` says whether rows follow it (no total is ever counted)."""
    return TmfJSONResponse(
        [select_fields(item, query.fields) for item in items],
        headers=paging_headers(len(items), more, query.next_link),
    )


def page_response(
    request: Request, items: list[Any], more: bool, offset: int, limit: int
) -> TmfJSONResponse:
    """The same for lists that take no fields= (parse_page)."""
    return TmfJSONResponse(
        items, headers=paging_headers(len(items), more, next_link(request, offset, limit))
    )
