"""Online signup: from an email to a live prepaid eSIM line (#377, docs/decisions/self-care.md
"Online signup").

A signup is a resumable record (`care_signup`). The requests here verify the email with a code,
take the person and the service address, and take the first payment: the identity check is a
command the worker sends (app/worker/signup.py), and so is the TMF622 order once the gateway
confirms the first top-up. The signup creates the party, the customer, the prepaid billing
account, the self-care login and the saved card; the order alone creates the product.

No session: the signup's id (a random UUID) is what the client holds, and every step after the
first needs the email verified with the emailed code.
"""

import logging
import secrets
from datetime import date, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

from app.api import account as tmf_account
from app.api import customer as tmf_customer
from app.api import party as tmf_party
from app.api import payment_method as cards
from app.api.common import Money, money, parse, parse_id, utc
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import TmfError, conflict, not_found, problems_error, unprocessable
from app.api.tmfjson import TmfJSONResponse
from app.billing.engine_tax import service_address_of
from app.care.auth import normal
from app.config import settings
from app.events import emit
from app.integrations.notify import NotifyError
from app.integrations.notify import adapter as sender
from app.integrations.payment import CardRejected
from app.integrations.payment import adapter as gateway
from app.internal import accounts as views
from app.internal import payments
from app.internal.context import no_query
from app.internal.users import client_address
from app.network import kind as offering_kind
from app.repository import care_logins, orders, parties
from app.repository import notifications as messages
from app.repository import payment_commands as commands
from app.repository import self_care as scope
from app.repository import signups as repo
from app.repository._sql import Row, fetch_one
from app.users import check_password, hash_password, password_policy

log = logging.getLogger("app.care")

BASE = "/self-care/v1/signup"
router = APIRouter(prefix=BASE)

CODE_VALID = timedelta(minutes=30)
CODE_ATTEMPTS = 5
PER_EMAIL_PER_DAY = 3
PER_ADDRESS_PER_HOUR = 10
LOGIN_CREATED_BY = "self-care-signup"

Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+$"),
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Start(Body):
    email: Email
    offeringId: str = Field(max_length=64)


class Verify(Body):
    code: SecretStr = Field(max_length=16)


class Address(Body):
    street1: str = Field(max_length=200)
    street2: str | None = Field(default=None, max_length=200)
    city: str = Field(max_length=100)
    stateOrProvince: str = Field(max_length=2)
    postCode: str = Field(max_length=10)


class Details(Body):
    givenName: Name
    familyName: Name
    birthDate: date = Field(strict=False)
    serviceAddress: Address


class Payment(Body):
    token: str = Field(min_length=1, max_length=255)
    password: SecretStr = Field(max_length=1024)


# --- the view ---


def _ref(uid: Any) -> dict[str, str] | None:
    return None if uid is None else {"id": str(uid)}


def esim_of(conn: psycopg.Connection, product_id: UUID | None) -> Row | None:
    """The eSIM profile the line holds (reserved or active), with its number."""
    if product_id is None:
        return None
    return fetch_one(
        conn,
        "SELECT s.iccid, s.esim_smdp_address, s.esim_matching_id FROM subscriber_number s"
        " WHERE s.sim_product_id = %s AND s.sim_type = 'esim'"
        " AND s.sim_state IN ('reserved', 'active') LIMIT 1",
        (product_id,),
    )


def activation_code(sim: Row) -> str:
    """The LPA activation code (sim.md): also the QR payload."""
    return f"LPA:1${sim['esim_smdp_address']}${sim['esim_matching_id']}"


def number_of(conn: psycopg.Connection, product_id: UUID | None) -> str | None:
    if product_id is None:
        return None
    row = fetch_one(
        conn,
        "SELECT n.value FROM number_assignment a JOIN subscriber_number n"
        " ON n.id = a.subscriber_number_id WHERE a.product_id = %s"
        " AND a.resource_specification_id = 'msisdn' AND a.cancelled_at IS NULL"
        " ORDER BY a.effective_from DESC LIMIT 1",
        (product_id,),
    )
    return None if row is None else row["value"]


def view(
    conn: psycopg.Connection, row: Row, *, operator: bool = False, esim: Row | None = None
) -> dict[str, Any]:
    """A signup as the customer (or an operator) sees it. `esim`: the activation code, which
    the customer's GET returns once."""
    offering = fetch_one(
        conn, "SELECT id, name FROM product_offering WHERE id = %s", (row["product_offering_id"],)
    )
    check = (
        None if row["identity_check_id"] is None else repo.get_check(conn, row["identity_check_id"])
    )
    command = (
        None if row["payment_command_id"] is None else commands.get(conn, row["payment_command_id"])
    )
    order = (
        None if row["product_order_id"] is None else orders.get_order(conn, row["product_order_id"])
    )
    payment = None
    if command is not None:
        payment = {
            "state": command["state"],
            "amount": money(command["amount"], command["currency"]),
            "declineReason": command["decline_reason"],
        }
    elif row["payment_error"] is not None:
        payment = {"state": "declined", "amount": None, "declineReason": row["payment_error"]}
    body: dict[str, Any] = {
        "id": str(row["id"]),
        "email": row["email"],
        "state": row["state"],
        "emailVerified": row["email_verified_at"] is not None,
        "offering": {"id": str(offering["id"]), "name": offering["name"]},
        "givenName": row["given_name"],
        "familyName": row["family_name"],
        "birthDate": row["birth_date"],
        "serviceAddress": row["service_address"],
        "identity": None
        if check is None
        else {"state": check["state"], "reason": row["identity_reason"]},
        "payment": payment,
        "customer": _ref(row["customer_id"]),
        "billingAccount": _ref(row["billing_account_id"]),
        "line": None
        if row["product_id"] is None
        else {"id": str(row["product_id"]), "number": number_of(conn, row["product_id"])},
        "order": None if order is None else {"id": str(order["id"]), "state": order["state"]},
        "failure": None
        if row["failure_code"] is None
        else {"code": row["failure_code"], "reason": row["failure_reason"]},
        "esim": None
        if esim is None
        else {
            "iccid": esim["iccid"],
            "activationCode": activation_code(esim),
            "qrPayload": activation_code(esim),
        },
        "expiresAt": utc(row["updated_at"] + repo.EXPIRE_AFTER)
        if row["state"] in repo.EXPIRING and row["payment_command_id"] is None
        else None,
        "createdAt": utc(row["created_at"]),
        "updatedAt": utc(row["updated_at"]),
    }
    if operator:
        body["clientAddress"] = row["client_address"]
        body["identityCheck"] = (
            None
            if check is None
            else {
                "id": str(check["id"]),
                "state": check["state"],
                "attempts": check["attempts"],
                "lastError": check["last_error"],
            }
        )
        body["decidedBy"] = row["decided_by"]
        body["decidedAt"] = None if row["decided_at"] is None else utc(row["decided_at"])
        body["activationShown"] = row["activation_shown_at"] is not None
    return body


def _json(body: Any, status_code: int = 200) -> TmfJSONResponse:
    response = TmfJSONResponse(body, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


# --- the email code ---

SUBJECT = "Confirm your email"
TEXT = (
    "Hello,\n\nyour code to confirm this email for your new line is {code}. It is valid for 30"
    " minutes. If you did not sign up, ignore this message."
)


def send_email(
    tx, *, email: str, event: str, subject: str, text: str, masked: str, customer_id, now
) -> bool:
    """Email now and log the message with its secret masked (the self-care codes' rule)."""
    uid = uuid4()
    ref, error = None, None
    try:
        ref = sender().send_email(str(uid), email, subject, text)
    except NotifyError as exc:
        error = str(exc) or "the sender refused the message"
    except Exception as exc:  # noqa: BLE001 - the log keeps why
        log.exception("signup email %s", uid)
        error = str(exc) or type(exc).__name__
    with tx() as conn:
        messages.record_sent(
            conn,
            uid=uid,
            event_type=event,
            receiver_name=None,
            receiver_email=email,
            customer_id=customer_id,
            subject=subject,
            content=masked,
            ref=ref,
            error=error,
            now=now,
        )
    return error is None


# --- helpers ---


def _signup(conn: psycopg.Connection, raw: str, now: datetime, *, lock: bool = True) -> Row:
    """The signup, locked; one that ran out of time is marked expired first."""
    row = repo.get(conn, parse_id("signup", raw), lock=lock)
    if row is None:
        raise not_found("signup", raw)
    if repo.expired(row, now):
        row = repo.update(conn, row["id"], now, state="expired")
    return row


def _expired_or(row: Row, message: str) -> TmfError:
    if row["state"] == "expired":
        return TmfError(409, "the signup expired: start again", code="signupExpired")
    return TmfError(409, message, code="signupState")


def plan(conn: psycopg.Connection, raw: str, now: datetime) -> Row:
    """A plan on sale now (sellable, Active, valid); any other id is a 422."""
    try:
        uid = UUID(raw)
    except ValueError:
        uid = None
    for offering in scope.offerings(conn, now):
        if (
            offering["id"] == uid
            and offering_kind.kind_of(offering["details"]) == offering_kind.PLAN
        ):
            return offering
    raise TmfError(422, f"offeringId {raw} is not a plan on sale", code="offeringUnavailable")


def _limit(conn: psycopg.Connection, email: str, address: str, now: datetime) -> None:
    """3 signups a day per email and 10 an hour per address."""
    for key in (f"signup-email|{email}", f"signup-address|{address}"):
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
    if repo.count_since(conn, "email", email, now - timedelta(days=1)) >= PER_EMAIL_PER_DAY:
        raise TmfError(
            429, "too many signups for this email today", headers={"Retry-After": "3600"}
        )
    if (
        repo.count_since(conn, "client_address", address, now - timedelta(hours=1))
        >= PER_ADDRESS_PER_HOUR
    ):
        raise TmfError(429, "too many signups from this address", headers={"Retry-After": "3600"})


# --- the steps ---


@router.post("", status_code=201)
def start_signup(request: Request, body: JsonBody, tx: Tx, clock: ClockDep):
    """Start a signup for a plan: a 6-digit code goes to the email (30 minutes)."""
    no_query(request)
    data = parse(Start, body)
    now = clock.now()
    email = normal(data.email)
    code = f"{secrets.randbelow(10**6):06d}"
    with tx() as conn:
        _limit(conn, email, client_address(request), now)
        offering = plan(conn, data.offeringId, now)
        row = repo.insert(
            conn,
            email=email,
            offering_id=offering["id"],
            client_address=client_address(request),
            code_hash=hash_password(code),
            code_expires_at=now + CODE_VALID,
            now=now,
        )
    sent = send_email(
        tx,
        email=email,
        event="care_signup_code",
        subject=SUBJECT,
        text=TEXT.format(code=code),
        masked=TEXT.format(code="******"),
        customer_id=None,
        now=now,
    )
    if not sent:
        raise TmfError(503, "the code could not be sent; try again later", code="codeNotSent")
    with tx() as conn:
        return _json(view(conn, row), 201)


@router.get("/{signup_id}")
def get_signup(signup_id: str, request: Request, tx: Tx, clock: ClockDep):
    """The signup's state. Once the line is active, the eSIM activation code and its QR
    payload are returned once (the email has them too)."""
    no_query(request)
    now = clock.now()
    with tx() as conn:
        row = _signup(conn, signup_id, now)
        esim = None
        if row["state"] == "active" and row["activation_shown_at"] is None:
            esim = esim_of(conn, row["product_id"])
            if esim is not None:
                conn.execute(
                    "UPDATE care_signup SET activation_shown_at = %s WHERE id = %s",
                    (now, row["id"]),
                )
        return _json(view(conn, row, esim=esim))


@router.post("/{signup_id}/verify")
def verify_signup(signup_id: str, request: Request, body: JsonBody, tx: Tx, clock: ClockDep):
    """The emailed code verifies the email. 5 wrong codes spend it; a verified signup answers
    with its state again."""
    no_query(request)
    data = parse(Verify, body)
    now = clock.now()
    with tx() as conn:
        row = _signup(conn, signup_id, now)
        if row["email_verified_at"] is not None:
            return _json(view(conn, row))
        if row["state"] != "started":
            raise _expired_or(row, f"the signup is {row['state']}")
        usable = row["email_code_expires_at"] > now and row["email_code_attempts"] < CODE_ATTEMPTS
        right = check_password(data.code.get_secret_value(), row["email_code_hash"])
        if not (usable and right):
            repo.code_failed(conn, row["id"])
            wrong = True
        else:
            wrong = False
            if care_logins.login_by_email(conn, row["email"]) is not None:
                raise TmfError(
                    409,
                    "this email already has a self-care login: sign in or reset the password",
                    code="emailInUse",
                )
            row = repo.update(conn, row["id"], now, email_verified_at=now)
            body_ = view(conn, row)
    if wrong:
        raise TmfError(401, "the code is wrong, used or expired", code="codeInvalid")
    return _json(body_)


@router.put("/{signup_id}/details")
def put_signup_details(signup_id: str, request: Request, body: JsonBody, tx: Tx, clock: ClockDep):
    """The person and the service address (a US address, taxes.md): the identity check is
    queued for the worker; poll GET. The same details again answer with the state."""
    no_query(request)
    data = parse(Details, body)
    now = clock.now()
    address = _address(data.serviceAddress)
    if data.birthDate >= clock.today():
        raise unprocessable("birthDate must be in the past")
    with tx() as conn:
        row = _signup(conn, signup_id, now)
        same = (
            row["given_name"] == data.givenName
            and row["family_name"] == data.familyName
            and row["birth_date"] == data.birthDate
            and row["service_address"] == address
        )
        if row["state"] != "started" or row["email_verified_at"] is None:
            if row["identity_check_id"] is not None and same:
                return _json(view(conn, row))
            if row["state"] == "started":
                raise TmfError(409, "verify the email first", code="emailNotVerified")
            raise _expired_or(row, f"the details cannot change: the signup is {row['state']}")
        check = repo.insert_check(
            conn,
            row["id"],
            {
                "givenName": data.givenName,
                "familyName": data.familyName,
                "birthDate": data.birthDate.isoformat(),
                "address": address,
            },
            now,
        )
        row = repo.update(
            conn,
            row["id"],
            now,
            given_name=data.givenName,
            family_name=data.familyName,
            birth_date=data.birthDate,
            service_address=address,
            identity_check_id=check["id"],
            state="identityPending",
        )
        return _json(view(conn, row))


def _address(sent: Address) -> dict[str, Any]:
    medium = {"@type": "GeographicAddressContactMedium", "country": "US"}
    medium |= {k: v for k, v in sent.model_dump().items() if v is not None}
    address, problems = service_address_of(
        [{"@type": "Contact", "contactType": "serviceAddress", "contactMedium": [medium]}]
    )
    if problems:
        raise problems_error([("serviceAddress", message, False) for _, message in problems])
    assert address is not None
    return address


@router.post("/{signup_id}/payment", status_code=202)
def pay_signup(signup_id: str, request: Request, body: JsonBody, tx: Tx, clock: ClockDep):
    """After a passed identity check: the password and a card token from the gateway's form.
    Creates the party, customer, prepaid billing account (with the service address), the
    self-care login and the saved card, and charges the card for the plan's first charge. Poll
    GET: the worker places the order when the gateway confirms. A declined card leaves the
    signup to pay again; a repeat while a charge is in flight answers with the state."""
    no_query(request)
    cards.refuse_card_data(body)
    data = parse(Payment, body)
    now = clock.now()
    with tx() as conn:
        row = _signup(conn, signup_id, now)
        if row["state"] in ("paid", "activating", "active") or (
            row["state"] == "identityPassed" and row["payment_command_id"] is not None
        ):
            return _json(view(conn, row), 202)
        if row["state"] != "identityPassed":
            raise _expired_or(row, f"the signup cannot be paid: it is {row['state']}")
        email = row["email"]
    password = data.password.get_secret_value()
    password_policy(email, password)
    try:
        card = gateway().card(data.token)
    except CardRejected as exc:
        raise TmfError(
            422, f"the gateway did not accept the card: {exc.reason}", code="cardRejected"
        ) from None
    if card.expires is not None and card.expires <= now:
        raise unprocessable("the card has expired")
    password_hash = hash_password(password)
    try:
        with tx() as conn:
            row = _signup(conn, signup_id, now)
            if row["state"] != "identityPassed" or row["payment_command_id"] is not None:
                return _json(view(conn, row), 202)  # a concurrent repeat got here first
            row = _create_parties(conn, row, password_hash, now, clock.today())
            account = parties.get_billing_account(conn, row["billing_account_id"], "update")
            method = _card(conn, account, data.token, card, now)
            today = clock.today()
            quote = views.quote(conn, account, _offering(conn, row), today, today)
            due = quote["shortfall"]["value"]
            if due <= 0:
                row = repo.update(
                    conn, row["id"], now, payment_method_id=method["id"], state="paid",
                    payment_error=None,
                )  # fmt: skip
                return _json(view(conn, row), 202)
            command, _ = payments.queue_card_topup(
                conn,
                account,
                Money(value=due, unit=account["currency"]),
                method["id"],
                f"signup-{row['id']}-{uuid4()}",
                now,
            )
            row = repo.update(
                conn, row["id"], now, payment_method_id=method["id"],
                payment_command_id=command["id"], payment_error=None,
            )  # fmt: skip
            return _json(view(conn, row), 202)
    except psycopg.errors.UniqueViolation as exc:
        raise conflict("this email already has a self-care login") from exc


def _offering(conn: psycopg.Connection, row: Row) -> Row:
    found = fetch_one(
        conn, "SELECT * FROM product_offering WHERE id = %s", (row["product_offering_id"],)
    )
    assert found is not None
    return found


def _create_parties(
    conn: psycopg.Connection, row: Row, password_hash: str, now: datetime, today: date
) -> Row:
    """The party, customer, prepaid account and login of the signup, once (a payment again
    after a declined card finds them)."""
    if row["billing_account_id"] is not None:
        conn.execute(
            "UPDATE customer_login SET password_hash = %s, updated_at = %s WHERE id = %s",
            (password_hash, now, row["customer_login_id"]),
        )
        return row
    name = f"{row['given_name']} {row['family_name']}"
    party = parties.create_individual(
        conn,
        given_name=row["given_name"],
        family_name=row["family_name"],
        details={
            "birthDate": row["birth_date"].isoformat(),
            "contactMedium": [
                {
                    "@type": "EmailContactMedium",
                    "contactType": "email",
                    "preferred": True,
                    "emailAddress": row["email"],
                }
            ],
        },
    )
    emit.created(conn, tmf_party.API, "individual", tmf_party.party_body(party), at=now)
    customer = parties.create_customer(conn, party_id=party["id"], name=name, status="Active")
    emit.created(
        conn, tmf_customer.API, "customer", tmf_customer.customer_body(conn, customer), at=now
    )
    account = parties.create_billing_account(
        conn,
        customer_id=customer["id"],
        name=f"{name} prepaid",
        currency=settings.ui_currency,
        bill_cycle_day=min(today.day, 28),
        rating_type="prepaid",
        opened_at=now,
    )
    parties.create_bucket(conn, account["id"], now)
    contact = [
        {
            "@type": "Contact",
            "contactType": "serviceAddress",
            "contactMedium": [
                {"@type": "GeographicAddressContactMedium", **row["service_address"]}
            ],
        }
    ]
    account = parties.update_billing_account(
        conn,
        account["id"],
        {
            "details": {
                "relatedParty": [
                    {
                        "@type": "RelatedPartyRefOrPartyRoleRef",
                        "role": "customer",
                        "partyOrPartyRole": {
                            "id": str(customer["id"]),
                            "href": tmf_customer.customer_href(customer["id"]),
                            "name": name,
                            "@type": "PartyRoleRef",
                            "@referredType": "Customer",
                        },
                    }
                ],
                "contact": contact,
            },
            "service_address": tmf_account.service_address_column(contact),
        },
    )
    emit.created(
        conn, tmf_account.API, "billingAccount", tmf_account.account_body(conn, account), at=now
    )
    login = care_logins.create_verified(
        conn, customer["id"], row["email"], password_hash, LOGIN_CREATED_BY, now
    )
    return repo.update(
        conn,
        row["id"],
        now,
        party_id=party["id"],
        customer_id=customer["id"],
        billing_account_id=account["id"],
        customer_login_id=login["id"],
    )


def _card(conn: psycopg.Connection, account: Row, token: str, card, now) -> Row:
    """The saved card of the token on the account: the one saved before, else a new one."""
    existing = fetch_one(
        conn,
        "SELECT * FROM payment_method WHERE billing_account_id = %s AND gateway_token = %s"
        " AND status = 'active'",
        (account["id"], token),
    )
    if existing is not None:
        return existing
    brand = card.brand or "card"
    row = cards.save_card(
        conn,
        account,
        name=f"{brand} ending {card.last_four or '????'}",
        description=None,
        brand=card.brand,
        card_type=None,
        last_four=card.last_four,
        expiration_date=card.expires,
        token=token,
        preferred=True,  # a card given again after a decline replaces the declined one
        now=now,
    )
    return row
