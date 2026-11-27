"""FastAPI dependencies shared by every TMF router."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Annotated, Any

import psycopg
from fastapi import Depends, Request

from app import db
from app.api import jsonpatch, tmfjson
from app.api.errors import TmfError, bad_request
from app.clock import Clock, SystemClock

MERGE_PATCH = "application/merge-patch+json"
JSON_PATCH = "application/json-patch+json"
JSON_PATCH_QUERY = "application/json-patch-query+json"
# what every v5 spec lists for PATCH: two merge patches, two JSON Patches
MERGE_TYPES = ("application/json", MERGE_PATCH)
JSON_PATCH_TYPES = (JSON_PATCH, JSON_PATCH_QUERY)
PATCH_TYPES = (*MERGE_TYPES, *JSON_PATCH_TYPES)

Transaction = Callable[[], AbstractContextManager[psycopg.Connection]]


@contextmanager
def _transaction() -> Iterator[psycopg.Connection]:
    """One connection, one transaction: committed when the block ends, rolled back if it
    raises. Handlers open exactly one per request."""
    with db.connection() as conn, conn.transaction():
        yield conn


def get_transaction() -> Transaction:
    return _transaction


def get_clock() -> Clock:
    return SystemClock()


def _content_type(request: Request) -> str:
    return request.headers.get("content-type", "").split(";")[0].strip().lower()


async def _object_body(request: Request) -> dict[str, Any]:
    try:
        data = tmfjson.loads(await request.body())
    except tmfjson.InvalidJSON as exc:
        raise bad_request(f"malformed JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise bad_request("the request body must be a JSON object")
    return data


async def json_body(request: Request) -> dict[str, Any]:
    """The POST body as a dict, parsed without float (see app/api/tmfjson.py)."""
    if _content_type(request) != "application/json":
        raise TmfError(415, "Content-Type must be application/json")
    return await _object_body(request)


async def merge_patch_body(request: Request) -> dict[str, Any]:
    """The PATCH body as a merge patch, whichever of the specs' four formats it came in
    (app/api/jsonpatch.py)."""
    content_type = _content_type(request)
    if content_type in MERGE_TYPES:
        return await _object_body(request)
    if content_type not in JSON_PATCH_TYPES:
        raise TmfError(415, f"PATCH accepts {', '.join(PATCH_TYPES)}")
    try:
        doc = tmfjson.loads(await request.body())
    except tmfjson.InvalidJSON as exc:
        raise bad_request(f"malformed JSON: {exc}") from exc
    ops = jsonpatch.operations(doc)
    current = await _current(request)
    return jsonpatch.merge_patch(current, ops, query=content_type == JSON_PATCH_QUERY)


async def _current(request: Request) -> dict[str, Any]:
    """The resource as its own GET answers it, from the app in-process. Read in its own
    transaction, before the PATCH's: a JSON Patch is applied to what was read, so a
    concurrent change of the same attribute in between is overwritten (as with a merge
    patch sent after a GET)."""
    path = request.url.path
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": request.url.scheme,
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"accept", b"application/json"),
            *[
                (name, value)
                for name, value in request.scope["headers"]
                if name in {b"cookie", b"x-bss-service", b"x-remote-user"}
            ],
        ],
        "client": request.scope.get("client"),
        "server": request.scope.get("server"),
        "state": dict(request.scope.get("state") or {}),
    }
    status, chunks = 500, []

    async def receive() -> dict[str, Any]:  # pragma: no cover - a GET route reads no body
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await request.app(scope, receive, send)
    body = tmfjson.loads(b"".join(chunks))
    if status != 200:
        raise TmfError(status, body.get("message", "the resource could not be read"))
    return body


Tx = Annotated[Transaction, Depends(get_transaction)]
ClockDep = Annotated[Clock, Depends(get_clock)]
JsonBody = Annotated[dict[str, Any], Depends(json_body)]
MergePatchBody = Annotated[dict[str, Any], Depends(merge_patch_body)]
