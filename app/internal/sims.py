"""SIM and eSIM stock for operators, under /internal/v1/sim. Ours, not TMF
(docs/decisions/sim.md): TMF639 reads and filters the stocked SIMs; the bulk import and
the eSIM generation have no TMF operation.

`POST /sim/batch` stocks a batch: JSON `{batch, simType, items: [{iccid, imsi,
smdpAddress, matchingId}]}` or `text/csv` (header row `iccid,imsi[,smdpAddress,
matchingId]`, query `batch` and `simType`). All or nothing. Ki and OPc are never accepted
or stored. `POST /sim/esim-generate` asks the network adapter for eSIM profiles.
"""

import csv
import io
import re
from typing import Any

from fastapi import APIRouter, Request

from app.api.common import utc
from app.api.deps import ClockDep, Tx, _content_type, _object_body
from app.api.errors import TmfError, bad_request, conflict, unprocessable
from app.api.tmfjson import TmfJSONResponse
from app.integrations.network import NetworkTemporary, adapter
from app.internal.context import no_query
from app.repository import sims as repo
from app.repository._sql import Row

BASE = "/internal/v1/sim"
router = APIRouter(prefix=BASE)

MAX_ROWS = 5000
BATCH = re.compile(r"^[A-Za-z0-9._-]{1,60}$")
ICCID = re.compile(r"^89[0-9]{16,18}$")
IMSI = re.compile(r"^[0-9]{14,15}$")
COLUMNS = ("iccid", "imsi", "smdpAddress", "matchingId")
SECRETS = {"ki", "opc", "op", "k", "opk"}  # carrier secrets: never stored
SHOWN = 8  # problems listed in an error


def batch_body(row: Row) -> dict[str, Any]:
    counts = {state: row["counts"].get(state, 0) for state in repo.STATES}
    return {
        "name": row["name"],
        "simType": row["sim_type"],
        "source": row["source"],
        "createdAt": utc(row["created_at"]),
        "total": sum(counts.values()),
        "counts": counts,
    }


async def stock_body(request: Request) -> dict[str, Any]:
    """The import as `{batch, simType, items}`, from JSON or CSV."""
    kind = _content_type(request)
    if kind == "application/json":
        return await _object_body(request)
    if kind != "text/csv":
        raise TmfError(415, "Content-Type must be application/json or text/csv")
    given = request.query_params
    text = (await request.body()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in reader.fieldnames or []]
    reader.fieldnames = header
    items = []
    for row in reader:
        if None in row:
            raise unprocessable(f"line {reader.line_num}: more values than columns")
        items.append({k: (v or "").strip() or None for k, v in row.items()})
    return {"batch": given.get("batch"), "simType": given.get("simType"), "items": items}


def _check_header(items: list[dict[str, Any]]) -> None:
    names = {key for item in items for key in item}
    secret = sorted(n for n in names if n.lower().replace("_", "") in SECRETS)
    if secret:
        raise unprocessable(
            f"{', '.join(secret)}: Ki and OPc stay with the carrier and are never accepted"
        )
    unknown = sorted(names - set(COLUMNS))
    if unknown:
        raise unprocessable(f"unknown columns {', '.join(unknown)}; use {', '.join(COLUMNS)}")


def validate(body: dict[str, Any], *, generated: bool = False) -> tuple[str, str, list[dict]]:
    allowed = {"batch", "simType", "items"}
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise bad_request(f"unknown attribute {unknown[0]}")
    name, sim_type, items = body.get("batch"), body.get("simType"), body.get("items")
    if not isinstance(name, str) or not BATCH.fullmatch(name):
        raise unprocessable("batch must be 1..60 letters, digits, '.', '_' or '-'")
    if sim_type not in repo.TYPES:
        raise unprocessable(f"simType must be one of {', '.join(repo.TYPES)}")
    if not isinstance(items, list) or not items:
        raise unprocessable("items must list at least one SIM")
    if len(items) > MAX_ROWS:
        raise unprocessable(f"a batch holds at most {MAX_ROWS} SIMs")
    if not all(isinstance(item, dict) for item in items):
        raise unprocessable("every item must be an object")
    _check_header(items)
    problems, seen_imsi, seen_iccid = [], set(), set()
    for number, item in enumerate(items, 1):
        iccid, imsi = item.get("iccid"), item.get("imsi")
        smdp, matching = item.get("smdpAddress"), item.get("matchingId")
        if not isinstance(iccid, str) or not ICCID.fullmatch(iccid):
            problems.append(f"item {number}: iccid must be 18..20 digits starting with 89")
        if not isinstance(imsi, str) or not IMSI.fullmatch(imsi):
            problems.append(f"item {number}: imsi must be 14 or 15 digits")
        if sim_type == "esim":
            if not all(isinstance(v, str) and v for v in (smdp, matching)):
                problems.append(f"item {number}: an eSIM profile needs smdpAddress and matchingId")
            elif "$" in smdp or "$" in matching:
                problems.append(f"item {number}: smdpAddress and matchingId must not contain '$'")
        elif smdp is not None or matching is not None:
            problems.append(f"item {number}: smdpAddress and matchingId are for eSIM profiles")
        if iccid in seen_iccid or imsi in seen_imsi:
            problems.append(f"item {number}: iccid or imsi repeated in the batch")
        seen_iccid.add(iccid)
        seen_imsi.add(imsi)
    if problems:
        extra = len(problems) - SHOWN
        raise unprocessable(
            "; ".join(problems[:SHOWN]) + (f"; and {extra} more problems" if extra > 0 else "")
        )
    return name, sim_type, items


def stock(conn, name: str, sim_type: str, items: list[dict], source: str, now) -> Row:
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("sim_batch:" + name,))
    if repo.batch_exists(conn, name):
        raise conflict(f"batch {name} already exists")
    clash = repo.existing(conn, [i["imsi"] for i in items], [i["iccid"] for i in items])
    if clash:
        listed = ", ".join(f"imsi {r['value']}" for r in clash[:SHOWN])
        raise conflict(f"already in inventory: {listed}; nothing was imported")
    repo.create_batch(conn, name, sim_type, source, now)
    prefix = "eSIM" if sim_type == "esim" else "SIM"
    repo.insert_many(
        conn,
        [
            {
                "name": f"{prefix} {i['iccid']}",
                "value": i["imsi"],
                "iccid": i["iccid"],
                "sim_type": sim_type,
                "batch": name,
                "smdp": i.get("smdpAddress"),
                "matching": i.get("matchingId"),
                "now": now,
            }
            for i in items
        ],
    )
    return repo.batch(conn, name)


@router.get("/batch")
def list_batches(request: Request, tx: Tx) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        rows = repo.batches(conn)
    items = [batch_body(row) for row in rows]
    return TmfJSONResponse(
        items, headers={"X-Total-Count": str(len(items)), "X-Result-Count": str(len(items))}
    )


@router.post("/batch", status_code=201)
async def import_batch(request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    body = await stock_body(request)
    if _content_type(request) == "text/csv":
        for name in request.query_params:
            if name not in ("batch", "simType"):
                raise bad_request(f"unknown query parameter {name}")
    else:
        no_query(request)
    name, sim_type, items = validate(body)
    with tx() as conn:
        row = stock(conn, name, sim_type, items, "import", clock.now())
    return TmfJSONResponse(batch_body(row), status_code=201)


@router.post("/esim-generate", status_code=201)
async def generate_esim(request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    no_query(request)
    body = await _object_body(request)
    unknown = sorted(set(body) - {"batch", "count"})
    if unknown:
        raise bad_request(f"unknown attribute {unknown[0]}")
    name, count = body.get("batch"), body.get("count")
    if not isinstance(name, str) or not BATCH.fullmatch(name):
        raise unprocessable("batch must be 1..60 letters, digits, '.', '_' or '-'")
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= MAX_ROWS:
        raise unprocessable(f"count must be a whole number from 1 to {MAX_ROWS}")
    try:
        profiles = adapter().generate_esim_profiles(name, count)
    except NetworkTemporary as error:
        raise TmfError(503, str(error) or "the network could not generate profiles") from None
    items = [
        {
            "iccid": p.iccid,
            "imsi": p.imsi,
            "smdpAddress": p.smdp_address,
            "matchingId": p.matching_id,
        }
        for p in profiles
    ]
    _, _, items = validate({"batch": name, "simType": "esim", "items": items})
    with tx() as conn:
        row = stock(conn, name, "esim", items, "generated", clock.now())
    return TmfJSONResponse(batch_body(row), status_code=201)
