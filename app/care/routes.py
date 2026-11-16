"""The self-care API for customers, under /self-care/v1 (docs/decisions/self-care.md).

Ours, not TMF. 404 for every route while BSS_SELF_CARE_ENABLED is off (app/care/auth.py
`authenticate`, called by app/auth.py for every /self-care path). Writes need the
`X-Bss-Request: 1` header, as the admin UI's do. The contract is
spec/self-care/bss-self-care.oas.yaml.
"""

from typing import Annotated

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

from app.api.common import parse, parse_id, utc
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import not_found
from app.api.tmfjson import TmfJSONResponse
from app.care import auth
from app.care.deps import CareActor
from app.config import settings
from app.internal.context import no_query
from app.repository import care_logins as repo

BASE = "/self-care/v1"
router = APIRouter(prefix=BASE)

Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+$"),
]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SignIn(Body):
    email: Email
    password: SecretStr = Field(max_length=1024)


class Verify(Body):
    email: Email
    code: SecretStr = Field(max_length=16)


class NewPassword(Body):
    code: SecretStr = Field(max_length=128)
    newPassword: SecretStr = Field(max_length=1024)


class Reset(Body):
    email: Email


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/session", status_code=202)
def start_care_session(request: Request, body: JsonBody, tx: Tx, clock: ClockDep):
    no_query(request)
    data = parse(SignIn, body)
    expires = auth.start_sign_in(
        tx, request, auth.normal(data.email), data.password.get_secret_value(), clock.now()
    )
    return _no_store(TmfJSONResponse({"codeExpiresAt": utc(expires)}, status_code=202))


@router.post("/session/verify")
def verify_care_session(request: Request, body: JsonBody, tx: Tx, clock: ClockDep):
    no_query(request)
    data = parse(Verify, body)
    token, session = auth.finish_sign_in(
        tx, request, auth.normal(data.email), data.code.get_secret_value(), clock.now()
    )
    login = session["login"]
    actor = auth.CareSession(
        login["id"], login["customer_id"], login["email"], auth.digest(token), session["expires_at"]
    )
    response = TmfJSONResponse(auth.session_view(tx, actor))
    response.set_cookie(
        auth.COOKIE,
        token,
        httponly=True,
        secure=settings.app_env not in {"dev", "test"},
        samesite="strict",
        max_age=settings.care_session_absolute_seconds,
        path=auth.COOKIE_PATH,
    )
    return _no_store(response)


@router.get("/session")
def get_care_session(request: Request, tx: Tx, actor: CareActor):
    no_query(request)
    return _no_store(TmfJSONResponse(auth.session_view(tx, actor)))


@router.delete("/session")
def delete_care_session(request: Request, tx: Tx, clock: ClockDep, actor: CareActor):
    no_query(request)
    auth.sign_out(tx, actor, clock.now())
    response = Response(status_code=204)
    response.delete_cookie(auth.COOKIE, path=auth.COOKIE_PATH)
    return response


@router.post("/password", status_code=204)
def set_care_password(request: Request, body: JsonBody, tx: Tx, clock: ClockDep):
    no_query(request)
    data = parse(NewPassword, body)
    auth.set_password(
        tx, request, data.code.get_secret_value(), data.newPassword.get_secret_value(), clock.now()
    )
    response = Response(status_code=204)
    response.delete_cookie(auth.COOKIE, path=auth.COOKIE_PATH)
    return response


@router.post("/password/reset", status_code=204)
def reset_care_password(request: Request, body: JsonBody, tx: Tx, clock: ClockDep):
    no_query(request)
    data = parse(Reset, body)
    auth.request_reset(tx, request, auth.normal(data.email), clock.now())
    return Response(status_code=204)


@router.get("/customer/{customer_id}")
def get_care_customer(customer_id: str, request: Request, tx: Tx, actor: CareActor):
    """The signed-in customer's own record; any other id is a 404."""
    no_query(request)
    wanted = parse_id("customer", customer_id)
    with tx() as conn:
        row = repo.own_customer(conn, actor.customer_id, wanted)
    if row is None:
        raise not_found("customer", customer_id)
    return _no_store(
        TmfJSONResponse({"id": str(row["id"]), "name": row["name"], "status": row["status"]})
    )
