"""Collect posted unbilled ABRs after the daily run has seen the request day's orders."""

import logging
from datetime import timedelta
from decimal import Decimal

from psycopg.types.range import Range

from app.billing.adapter import issue_bills, settle_account
from app.billing.core import cycle_period
from app.billing.model import BillingError
from app.config import settings
from app.events import emit
from app.repository import billing
from app.repository._sql import fetch_all, fetch_one

log = logging.getLogger("app.worker")


def process_demands(conn, *, clock, tz, checkpoint=None, heartbeat=None):
    from app.worker.runs import ensure_ownership

    ensure_ownership(conn)
    pending = fetch_all(
        conn,
        "SELECT id, billing_account_id FROM customer_bill_on_demand"
        " WHERE state = 'inProgress' ORDER BY requested_at, id",
    )
    done = []
    for request in pending:
        if checkpoint is not None and checkpoint():
            break
        with conn.transaction():
            account = billing.load_account(conn, request["billing_account_id"])
            demand = fetch_one(
                conn,
                "SELECT * FROM customer_bill_on_demand WHERE id = %s FOR UPDATE",
                (request["id"],),
            )
            if demand["state"] != "inProgress":
                continue
            run = fetch_one(
                conn,
                """
                SELECT r.*, a.status AS account_status FROM billing_run r
                LEFT JOIN billing_run_account a
                    ON a.run_id = r.id AND a.billing_account_id = %s
                WHERE r.kind = 'daily' AND r.run_date >= %s AND r.run_date <= %s
                ORDER BY r.run_date DESC LIMIT 1
            """,
                (account.id, demand["ready_on"], clock.now().astimezone(tz).date()),
            )
            if run is None or run["account_status"] != "ok":
                if heartbeat is not None:
                    heartbeat()
                continue
            now = clock.now()
            abrs = fetch_all(
                conn,
                """
                SELECT a.* FROM applied_customer_billing_rate a
                JOIN billing_run r ON r.id = a.billing_run_id
                WHERE (r.kind = 'daily' OR %s = 'prepaid')
                  AND a.billing_account_id = %s AND a.customer_bill_id IS NULL
                  AND r.run_date <= %s
                ORDER BY a.applied_at, a.id FOR UPDATE OF a
            """,
                (account.rating_type, account.id, run["run_date"]),
            )
            if not abrs:
                conn.execute(
                    "UPDATE customer_bill_on_demand SET state = 'rejected',"
                    " description = 'No unbilled charges remain', updated_at = %s WHERE id = %s",
                    (now, demand["id"]),
                )
            else:
                try:
                    with conn.transaction():
                        _fulfill(conn, account, demand, run, abrs, now)
                except BillingError:
                    log.exception("on-demand bill %s failed validation", demand["id"])
                    conn.execute(
                        "UPDATE customer_bill_on_demand SET state = 'terminatedWithError',"
                        " description = 'Bill validation failed', updated_at = %s WHERE id = %s",
                        (now, demand["id"]),
                    )
            emit.bill_demand(conn, demand["id"], at=now)  # done, rejected or failed
            done.append(demand["id"])
        if heartbeat is not None:
            heartbeat()
    return done


def _fulfill(conn, account, demand, run, abrs, now):
    day = run["run_date"]
    bill = billing.insert_bill(
        conn,
        billing_account_id=account.id,
        currency=account.currency,
        bill_period=Range(day, cycle_period(day, account.bill_cycle_day).end, "[)"),
        bill_date=day,
        state="new",
        amount_due=sum((billing.abr_total(a) for a in abrs), Decimal(0)),
        billing_run_id=run["id"],
        payment_due_date=day + timedelta(days=settings.bill_payment_term_days),
        run_type="offCycle",
    )
    billing.record_bill_issue(conn, bill["id"], now=now)
    billing.attach_abrs_to_bill(conn, bill["id"], account.id, [a["id"] for a in abrs])
    issue_bills(conn, account, now=now)
    settle_account(conn, account, now=now, cause="run")
    conn.execute(
        "UPDATE customer_bill_on_demand SET state = 'done', customer_bill_id = %s,"
        " updated_at = %s WHERE id = %s",
        (bill["id"], now, demand["id"]),
    )
