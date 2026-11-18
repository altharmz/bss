"""Wholesale reconciliation with the network partner's invoices, under /internal/v1/wholesale.
Ours, not TMF: TMF has no wholesale reconciliation API, and a partner invoice is not a TMF678
customer bill (docs/decisions/wholesale.md).

`POST /invoice?invoiceNumber=...` takes the invoice file as the raw body (`text/csv`, the
format of app/integrations/network/wholesale.py), stores its lines matched to products,
reconciles it in the same transaction and answers the summary. `GET /invoice`, `/invoice/{id}`,
`/invoice/{id}/discrepancies` and `/invoice/{id}/lines` read it back; the reads are MCP tools,
the upload is not (a file is the UI's).
"""

import re
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request

from app.api.common import money, operator_of, parse_id, utc
from app.api.deps import ClockDep, Tx
from app.api.errors import TmfError, bad_request, conflict, not_found
from app.api.listing import page_response, parse_page
from app.api.tmfjson import TmfJSONResponse
from app.integrations.network import wholesale as invoice_format
from app.internal.context import no_query
from app.repository import wholesale as repo
from app.repository._sql import Row

BASE = "/internal/v1/wholesale"
router = APIRouter(prefix=BASE)
MAX_BYTES = 64 * 1024 * 1024
INVOICE_NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,63}$")
CENT = Decimal("0.01")


async def csv_body(request: Request) -> bytes:
    """The raw invoice file: `text/csv`, at most 64 MB."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "text/csv":
        raise TmfError(415, "Content-Type must be text/csv")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
        raise TmfError(413, f"the invoice file is larger than {MAX_BYTES // (1024 * 1024)} MB")
    body = await request.body()
    if len(body) > MAX_BYTES:
        raise TmfError(413, f"the invoice file is larger than {MAX_BYTES // (1024 * 1024)} MB")
    return body


CsvBody = Annotated[bytes, Depends(csv_body)]


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def _href(invoice_id: Any) -> str:
    return f"{BASE}/invoice/{invoice_id}"


def _period(row: Row) -> dict[str, Any]:
    period = row["period"]
    return {"start": period.lower, "end": period.upper}


def invoice_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    currency = row["currency"]
    totals = repo.totals(conn, row["id"])
    revenue = cents(totals["revenue"])
    return {
        "id": str(row["id"]),
        "href": _href(row["id"]),
        "invoiceNumber": row["invoice_number"],
        "period": _period(row),
        "currency": currency,
        "total": money(row["total"], currency),
        "lineCount": row["line_count"],
        "state": row["state"],
        "importedAt": utc(row["imported_at"]),
        "importedBy": row["imported_by"],
        "discrepancies": repo.discrepancy_counts(conn, row["id"]),
        "cost": money(totals["cost"], currency),
        "revenue": money(revenue, currency),
        "margin": money(revenue - totals["cost"], currency),
        "matchedProducts": totals["matched"],
        "unmatchedNumbers": totals["unmatched"],
    }


def _ref(uid: Any) -> dict[str, str] | None:
    return None if uid is None else {"id": str(uid)}


def discrepancy_body(row: Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "kind": row["kind"],
        "product": _ref(row["product_id"]),
        "msisdn": row["msisdn"],
        "lineNumber": row["line_number"],
        "usageKind": row["usage_kind"],
        "expected": row["expected"],
        "invoiced": row["invoiced"],
        "detail": row["detail"],
    }


def line_body(row: Row, currency: str) -> dict[str, Any]:
    revenue = cents(row["revenue"])
    return {
        "product": _ref(row["product_id"]),
        "msisdn": row["msisdn"],
        "iccid": row["iccid"],
        "invoiceLines": row["lines"],
        "cost": money(row["cost"], currency),
        "revenue": money(revenue, currency),
        "margin": money(revenue - row["cost"], currency),
    }


def _invoice(conn: psycopg.Connection, raw: str) -> Row:
    row = repo.get(conn, parse_id("wholesaleInvoice", raw))
    if row is None:
        raise not_found("wholesaleInvoice", raw)
    return row


def _one(request: Request, name: str) -> str | None:
    given = request.query_params.getlist(name)
    if len(given) > 1:
        raise bad_request(f"{name} given more than once")
    return given[0] if given else None


@router.post("/invoice", status_code=201)
def import_wholesale_invoice(request: Request, body: CsvBody, tx: Tx, clock: ClockDep):
    for name in request.query_params:
        if name != "invoiceNumber":
            raise bad_request(f"unknown query parameter {name}")
    number = _one(request, "invoiceNumber")
    if number is None or not INVOICE_NUMBER.match(number):
        raise bad_request(
            "invoiceNumber is required: 1 to 64 letters, digits and . _ / - (query parameter)"
        )
    try:
        lines = invoice_format.parse_invoice(body)
    except invoice_format.TooLarge as error:
        raise TmfError(413, str(error)) from None
    except invoice_format.InvoiceError as error:
        raise TmfError(422, str(error)) from None
    try:
        with tx() as conn:
            row = repo.insert_invoice(
                conn,
                invoice_number=number,
                lines=lines,
                operator=operator_of(request) or "unknown",
                now=clock.now(),
            )
            repo.insert_lines(conn, row["id"], lines)
            repo.reconcile(conn, row["id"])
            row = repo.get(conn, row["id"])
            assert row is not None
            return TmfJSONResponse(invoice_body(conn, row), status_code=201)
    except psycopg.errors.UniqueViolation as error:
        raise conflict(
            f"invoice {number} is already imported: a corrected invoice is a new import"
            " under another number"
        ) from error


@router.get("/invoice")
def list_wholesale_invoices(request: Request, tx: Tx):
    offset, limit = parse_page(request)
    with tx() as conn:
        rows, more = repo.page(conn, offset, limit)
        items = [invoice_body(conn, row) for row in rows]
    return page_response(request, items, more, offset, limit)


@router.get("/invoice/{invoice_id}")
def get_wholesale_invoice(invoice_id: str, request: Request, tx: Tx):
    no_query(request)
    with tx() as conn:
        return TmfJSONResponse(invoice_body(conn, _invoice(conn, invoice_id)))


@router.get("/invoice/{invoice_id}/discrepancies")
def list_wholesale_discrepancies(invoice_id: str, request: Request, tx: Tx):
    offset, limit = parse_page(request, extra=("kind",))
    kind = _one(request, "kind")
    if kind is not None and kind not in repo.KINDS:
        raise bad_request(f"kind must be one of {', '.join(repo.KINDS)}")
    with tx() as conn:
        row = _invoice(conn, invoice_id)
        rows, more = repo.discrepancies(conn, row["id"], kind, offset, limit)
    return page_response(request, [discrepancy_body(r) for r in rows], more, offset, limit)


@router.get("/invoice/{invoice_id}/lines")
def list_wholesale_lines(invoice_id: str, request: Request, tx: Tx):
    offset, limit = parse_page(request, extra=("sort",))
    sort = _one(request, "sort") or "msisdn"
    if sort not in repo.SORTS:
        raise bad_request(f"sort must be one of {', '.join(repo.SORTS)}")
    with tx() as conn:
        row = _invoice(conn, invoice_id)
        rows, more = repo.line_margins(conn, row["id"], sort, offset, limit)
    items = [line_body(r, row["currency"]) for r in rows]
    return page_response(request, items, more, offset, limit)
