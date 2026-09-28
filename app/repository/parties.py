"""Parties (TMF632), customers (TMF629) and billing accounts (TMF666)."""

from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_one, returning


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


def get_party(conn: psycopg.Connection, party_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM party WHERE id = %s", (party_id,))


def create_customer(conn: psycopg.Connection, *, party_id: UUID, status: str = "Created") -> Row:
    return returning(
        conn,
        "INSERT INTO customer (party_id, status) VALUES (%s, %s) RETURNING *",
        (party_id, status),
    )


def get_customer(conn: psycopg.Connection, customer_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM customer WHERE id = %s", (customer_id,))


def create_billing_account(
    conn: psycopg.Connection,
    *,
    customer_id: UUID,
    name: str,
    currency: str,
    bill_cycle_day: int,
    state: str = "Active",
) -> Row:
    return returning(
        conn,
        """
        INSERT INTO billing_account (customer_id, name, state, currency, bill_cycle_day)
        VALUES (%s, %s, %s, %s, %s) RETURNING *
        """,
        (customer_id, name, state, currency, bill_cycle_day),
    )


def get_billing_account(conn: psycopg.Connection, account_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM billing_account WHERE id = %s", (account_id,))
