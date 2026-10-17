"""Parties (TMF632), customers (TMF629) and billing accounts (TMF666)."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg

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


def create_individual(
    conn: psycopg.Connection, *, family_name: str, given_name: str | None = None
) -> Row:
    return returning(
        conn,
        """
        INSERT INTO party (party_type, given_name, family_name)
        VALUES ('Individual', %s, %s) RETURNING *
        """,
        (given_name, family_name),
    )


def create_organization(conn: psycopg.Connection, *, name: str) -> Row:
    return returning(
        conn,
        """
        INSERT INTO party (party_type, organization_name)
        VALUES ('Organization', %s) RETURNING *
        """,
        (name,),
    )


def get_party(conn: psycopg.Connection, party_id: UUID, lock: Lock = None) -> Row | None:
    return select_by_id(conn, "party", party_id, lock)


def create_customer(
    conn: psycopg.Connection, *, party_id: UUID, name: str, status: str = "Created"
) -> Row:
    return returning(
        conn,
        "INSERT INTO customer (party_id, name, status) VALUES (%s, %s, %s) RETURNING *",
        (party_id, name, status),
    )


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
) -> Row:
    """`opened_at` comes from the injected clock (not the database's now()): a billing run
    for D covers the accounts opened before 00:00 of D."""
    return returning(
        conn,
        """
        INSERT INTO billing_account
            (customer_id, name, state, currency, bill_cycle_day, credit_limit, opened_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (
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
    return update_by_id(conn, "party", party_id, changes)


def list_customers(
    conn: psycopg.Connection, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    return select_page(conn, "customer", where=where, offset=offset, limit=limit)


def update_customer(conn: psycopg.Connection, customer_id: UUID, changes: dict[str, Any]) -> Row:
    return update_by_id(conn, "customer", customer_id, changes)


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
    return update_by_id(conn, "billing_account", account_id, changes)
