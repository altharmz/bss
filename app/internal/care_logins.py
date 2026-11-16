"""A customer's self-care login, for operators: see it, invite the customer, disable it
(docs/decisions/self-care.md). Ours, not TMF.

`POST` creates the login (or restarts an inactive one) and emails a link code to set the
password; 409 while self-care is disabled or the customer has an active login. `DELETE`
disables the login and ends its sessions at once. No password or code is ever returned.
"""

from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import StringConstraints

from app.api.common import TmfModel, operator_of, parse, parse_id
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.tmfjson import TmfJSONResponse
from app.care import auth
from app.internal.context import no_query

router = APIRouter(prefix="/internal/v1")

Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+$"),
]


class CareLoginInvite(TmfModel):
    email: Email


@router.get("/customer/{customer_id}/careLogin")
def get_care_login(customer_id: str, request: Request, tx: Tx, clock: ClockDep):
    no_query(request)
    uid = parse_id("customer", customer_id)
    return TmfJSONResponse(auth.get_login(tx, uid, customer_id, clock.now()))


@router.post("/customer/{customer_id}/careLogin")
def invite_care_login(customer_id: str, body: JsonBody, request: Request, tx: Tx, clock: ClockDep):
    no_query(request)
    data = parse(CareLoginInvite, body)
    uid = parse_id("customer", customer_id)
    operator = operator_of(request) or "unknown"
    view = auth.invite(tx, uid, customer_id, auth.normal(data.email), operator, clock.now())
    return TmfJSONResponse(view, status_code=201)


@router.delete("/customer/{customer_id}/careLogin")
def disable_care_login(customer_id: str, request: Request, tx: Tx, clock: ClockDep):
    no_query(request)
    uid = parse_id("customer", customer_id)
    return TmfJSONResponse(auth.disable(tx, uid, customer_id, clock.now()))
