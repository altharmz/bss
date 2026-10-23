"""Parties (TMF632), customers (TMF629) and billing accounts (TMF666)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.repository._sql import (
    Lock,
    Row,
    fetch_all,
    money,
    returning,
    select_by_id,
    select_page,
    update_by_id,
)


def _jsonb(value: dict[str, Any]) -> Jsonb:
    # tmfjson: numbers in a document stay exact decimals, never floats
    from app.api import tmfjson  # lazily: importing app.api loads every API module

    return Jsonb(value, dumps=tmfjson.dumps)


def create_individual(
    conn: psycopg.Connection,
    *,
    family_name: str,
    given_name: str | None = None,
    status: str | None = None,
    details: dict[str, Any] | None = None,
    id: UUID | None = None,
) -> Row:
    """`id` None: a new one (a client may choose it). `status`, `details` (the TMF632
    attributes that are documents to us, app/api/party.py): written only when given, so
    the tests' factories also work on a schema before 0022."""
    row = returning(
        conn,
        """
        INSERT INTO party (id, party_type, given_name, family_name)
        VALUES (COALESCE(%s::uuid, gen_random_uuid()), 'Individual', %s, %s) RETURNING *
        """,
        (id, given_name, family_name),
    )
    return _set_given(conn, "party", row, status=status, details=details)


def create_organization(
    conn: psycopg.Connection,
    *,
    name: str,
    status: str | None = None,
    details: dict[str, Any] | None = None,
    id: UUID | None = None,
) -> Row:
    row = returning(
        conn,
        """
        INSERT INTO party (id, party_type, organization_name)
        VALUES (COALESCE(%s::uuid, gen_random_uuid()), 'Organization', %s) RETURNING *
        """,
        (id, name),
    )
    return _set_given(conn, "party", row, status=status, details=details)


def _set_given(conn: psycopg.Connection, table: str, row: Row, **values: Any) -> Row:
    changes = {column: value for column, value in values.items() if value}
    return update_by_id(conn, table, row["id"], with_details(changes)) if changes else row


def get_party(conn: psycopg.Connection, party_id: UUID, lock: Lock = None) -> Row | None:
    return select_by_id(conn, "party", party_id, lock)


def create_customer(
    conn: psycopg.Connection,
    *,
    party_id: UUID,
    name: str,
    status: str = "Created",
    details: dict[str, Any] | None = None,
    id: UUID | None = None,
) -> Row:
    """`details`: the TMF629 attributes that are documents to us (app/api/customer.py),
    written only when given (as for a party)."""
    row = returning(
        conn,
        "INSERT INTO customer (id, party_id, name, status)"
        " VALUES (COALESCE(%s::uuid, gen_random_uuid()), %s, %s, %s) RETURNING *",
        (id, party_id, name, status),
    )
    return _set_given(conn, "customer", row, details=details)


def get_customer(conn: psycopg.Connection, customer_id: UUID, lock: Lock = None) -> Row | None:
    return select_by_id(conn, "customer", customer_id, lock)


def create_billing_account(
    conn: psycopg.Connection,
    *,
    customer_id: UUID,
    name: str,
    currency: str,
    bill_cycle_day: int,
    opened_at: datetime,
    state: str = "Active",
    credit_limit: Decimal | None = None,
    id: UUID | None = None,
) -> Row:
    """`opened_at` comes from the injected clock (not the database's now()): a billing run
    for D covers the accounts opened before 00:00 of D."""
    return returning(
        conn,
        """
        INSERT INTO billing_account
            (id, customer_id, name, state, currency, bill_cycle_day, credit_limit, opened_at)
        VALUES (COALESCE(%s::uuid, gen_random_uuid()), %s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (
            id,
            customer_id,
            name,
            state,
            currency,
            bill_cycle_day,
            None if credit_limit is None else money(credit_limit),
            opened_at,
        ),
    )


def get_billing_account(
    conn: psycopg.Connection, account_id: UUID, lock: Lock = None
) -> Row | None:
    return select_by_id(conn, "billing_account", account_id, lock)


# What a billing account's history is made of: while any of these refers to it, the
# account stays (close it instead). Only its presentation preferences go with it.
ACCOUNT_HISTORY = {
    "balanceAction": ("balance_action", "billing_account_id"),
    "fundingHold": ("funding_hold", "billing_account_id"),
    "prepaidBucket": ("prepaid_bucket", "billing_account_id"),
    "prepaidCommand": ("prepaid_command", "billing_account_id"),
    "productOrder": ("product_order", "billing_account_id"),
    "product": ("product", "billing_account_id"),
    "appliedCustomerBillingRate": ("applied_customer_billing_rate", "billing_account_id"),
    "customerBill": ("customer_bill", "billing_account_id"),
    "customerBillOnDemand": ("customer_bill_on_demand", "billing_account_id"),
    "payment": ("payment", "billing_account_id"),
    "refund": ("refund", "billing_account_id"),
    "billAllocation": ("bill_allocation", "billing_account_id"),
    "billingRun": ("billing_run_account", "billing_account_id"),
}


def list_parties(
    conn: psycopg.Connection, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    return select_page(conn, "party", where=where, offset=offset, limit=limit)


def update_party(conn: psycopg.Connection, party_id: UUID, changes: dict[str, Any]) -> Row:
    return update_by_id(conn, "party", party_id, with_details(changes))


def with_details(changes: dict[str, Any]) -> dict[str, Any]:
    """Changes with a `details` document made a jsonb parameter (exact numbers)."""
    if "details" not in changes:
        return changes
    return {**changes, "details": _jsonb(changes["details"])}


def list_customers(
    conn: psycopg.Connection, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    return select_page(conn, "customer", where=where, offset=offset, limit=limit)


def update_customer(conn: psycopg.Connection, customer_id: UUID, changes: dict[str, Any]) -> Row:
    return update_by_id(conn, "customer", customer_id, with_details(changes))


def accounts_of_customer(conn: psycopg.Connection, customer_id: UUID) -> list[Row]:
    """All of a customer's accounts, without list pagination truncating navigation."""
    return fetch_all(
        conn,
        "SELECT * FROM billing_account WHERE customer_id = %s ORDER BY created_at, id",
        (customer_id,),
    )


def list_billing_accounts(
    conn: psycopg.Connection, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    return select_page(conn, "billing_account", where=where, offset=offset, limit=limit)


def update_billing_account(
    conn: psycopg.Connection, account_id: UUID, changes: dict[str, Any]
) -> Row:
    return update_by_id(conn, "billing_account", account_id, with_details(changes))


def prepaid_close_blockers(conn: psycopg.Connection, account_id: UUID, today: date) -> list[str]:
    """What keeps a prepaid account from closing: held money, unfinished commands and
    products not yet ended on `today`. Call with the account locked FOR UPDATE."""
    row = conn.execute(
        """
        SELECT (SELECT count(*) FROM funding_hold WHERE billing_account_id = %(id)s
                   AND released_at IS NULL AND amount > 0),
               (SELECT count(*) FROM prepaid_command WHERE billing_account_id = %(id)s
                   AND state <> 'completed'),
               (SELECT count(*) FROM product WHERE billing_account_id = %(id)s
                   AND status NOT IN ('terminated', 'cancelled', 'aborted')
                   AND (termination_date IS NULL OR termination_date > %(today)s))
        """,
        {"id": account_id, "today": today},
    ).fetchone()
    names = ("held funds", "unfinished prepaid commands", "products not yet ended")
    return [f"{count} {name}" for count, name in zip(row, names, strict=True) if count]
