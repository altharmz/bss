"""FastAPI dependencies shared by every TMF router."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Annotated, Any

import psycopg
from fastapi import Depends, Request

from app import db
from app.api import tmfjson
from app.api.errors import TmfError, bad_request
from app.clock import Clock, SystemClock

MERGE_PATCH = "application/merge-patch+json"

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
    if _content_type(request) != MERGE_PATCH:
        raise TmfError(415, f"PATCH accepts {MERGE_PATCH} only")
    return await _object_body(request)


Tx = Annotated[Transaction, Depends(get_transaction)]
ClockDep = Annotated[Clock, Depends(get_clock)]
JsonBody = Annotated[dict[str, Any], Depends(json_body)]
MergePatchBody = Annotated[dict[str, Any], Depends(merge_patch_body)]
