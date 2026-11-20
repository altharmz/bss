"""Online signups, for operators: the list, one signup and the decision on one in review
(docs/decisions/self-care.md "Online signup"). Ours, not TMF.

Every failure leaves the signup in the list with its state and reason. A signup whose identity
check needs a person (`identityReview`: the service said so, or it could not be reached) waits
for `POST /careSignup/{id}/decision`: approve lets the customer pay, reject ends it.
"""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import Field

from app.api.common import TmfModel, operator_of, parse, parse_id
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import TmfError, not_found
from app.api.listing import page_response, parse_page
from app.api.tmfjson import TmfJSONResponse
from app.care import auth
from app.care import signup as signups  # module refs: app.care imports app.internal
from app.internal.context import no_query
from app.internal.subscribers import one_param
from app.repository import signups as repo

router = APIRouter(prefix="/internal/v1")

STATES = (
    "started",
    "identityPending",
    "identityPassed",
    "identityFailed",
    "identityReview",
    "paid",
    "activating",
    "active",
    "failed",
    "expired",
)


class Decision(TmfModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, min_length=1, max_length=500)


@router.get("/careSignup")
def list_care_signups(request: Request, tx: Tx):
    """Signups, newest first, filtered by state or email; paged with Link rel=next."""
    offset, limit = parse_page(request, extra=("state", "email"))
    state = one_param(request, "state", STATES)
    email = one_param(request, "email")
    with tx() as conn:
        rows, more = repo.page(
            conn,
            state=state,
            email=None if email is None else auth.normal(email),
            offset=offset,
            limit=limit,
        )
        items = [signups.view(conn, row, operator=True) for row in rows]
    return page_response(request, items, more, offset, limit)


@router.get("/careSignup/{signup_id}")
def get_care_signup(signup_id: str, request: Request, tx: Tx):
    no_query(request)
    with tx() as conn:
        row = repo.get(conn, parse_id("careSignup", signup_id))
        if row is None:
            raise not_found("careSignup", signup_id)
        return TmfJSONResponse(signups.view(conn, row, operator=True))


@router.post("/careSignup/{signup_id}/decision")
def decide_care_signup(signup_id: str, body: JsonBody, request: Request, tx: Tx, clock: ClockDep):
    """Approve (the customer may pay) or reject (the signup ends, nothing was created) a signup
    in identityReview; 409 in any other state."""
    no_query(request)
    data = parse(Decision, body)
    now = clock.now()
    with tx() as conn:
        row = repo.get(conn, parse_id("careSignup", signup_id), lock=True)
        if row is None:
            raise not_found("careSignup", signup_id)
        if row["state"] != "identityReview":
            raise TmfError(
                409, f"only a signup in identityReview is decided: it is {row['state']}",
                code="signupState",
            )  # fmt: skip
        row = repo.update(
            conn,
            row["id"],
            now,
            state="identityPassed" if data.decision == "approve" else "identityFailed",
            identity_reason=data.reason or row["identity_reason"],
            decided_by=operator_of(request) or "unknown",
            decided_at=now,
        )
        return TmfJSONResponse(signups.view(conn, row, operator=True))
