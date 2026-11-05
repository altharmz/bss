from datetime import date

import psycopg
import pytest
from alembic.config import Config
from psycopg import sql
from sqlalchemy.exc import IntegrityError

from alembic import command


@pytest.mark.smoke
def test_alembic_upgrade_head_on_clean_database(temp_db_url):
    command.upgrade(Config("alembic.ini"), "head")


def test_0030_preserves_monthly_data_and_refuses_lossy_downgrade(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0029")
    with psycopg.connect(temp_db_url) as conn:
        price_id = conn.execute("""
            INSERT INTO product_offering_price
                (name, lifecycle_status, price_type, recurring_period, amount, currency, valid_for)
            VALUES ('P', 'Active', 'recurring', 'monthly', 10, 'USD', tstzrange(now(), NULL))
            RETURNING id
        """).fetchone()[0]
    command.upgrade(cfg, "0030")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute(
            "SELECT recurring_period, recurring_period_length FROM product_offering_price"
            " WHERE id = %s",
            (price_id,),
        ).fetchone() == ("monthly", 1)
        conn.execute(
            "UPDATE product_offering_price SET recurring_period = 'weekly',"
            " recurring_period_length = 2 WHERE id = %s",
            (price_id,),
        )
    with pytest.raises(Exception, match="non-monthly prices"):
        command.downgrade(cfg, "0029")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("0030",)
        conn.execute(
            "UPDATE product_offering_price SET recurring_period = 'monthly',"
            " recurring_period_length = 1 WHERE id = %s",
            (price_id,),
        )
    command.downgrade(cfg, "0029")
    command.upgrade(cfg, "0030")


def test_alembic_downgrade_then_upgrade_roundtrip(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


PREEXISTING_ROWS = {
    "customer.name": [
        "INSERT INTO party (party_type, family_name) VALUES ('Individual', 'Ivanov')",
        "INSERT INTO customer (party_id, status) SELECT id, 'Created' FROM party",
    ],
    "product_offering_price.lifecycle_status": [
        "INSERT INTO product_specification (name, lifecycle_status) VALUES ('s', 'Active')",
        """INSERT INTO product_offering
               (product_specification_id, name, lifecycle_status, valid_for)
           SELECT id, 'o', 'Active', tstzrange(now(), NULL) FROM product_specification""",
        """INSERT INTO product_offering_price
               (product_offering_id, price_type, amount, currency, valid_for)
           SELECT id, 'oneTime', 1, 'USD', tstzrange(now(), NULL) FROM product_offering""",
    ],
}


@pytest.mark.parametrize("column", sorted(PREEXISTING_ROWS))
def test_0002_fails_loudly_if_the_tables_are_not_empty(temp_db_url, column):
    """0002 adds NOT NULL columns without a default on purpose: rows that predate the API
    would need a backfill decided by hand, so the migration must stop, not guess.

    payment.payment_method follows the same pattern but cannot be isolated: a payment
    needs a billing account, which needs a customer, and customer.name fails first.
    """
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0001")
    with psycopg.connect(temp_db_url) as conn:
        for statement in PREEXISTING_ROWS[column]:
            conn.execute(statement)
    table, name = column.split(".")
    with pytest.raises(IntegrityError, match=f'column "{name}" of relation "{table}"'):
        command.upgrade(cfg, "head")


def test_0004_on_existing_runs_and_accounts(temp_db_url):
    """0004 on a database that already has runs (local seeds) and accounts: a completed run
    stays valid, a failed one gets failure_kind 'run' (the CHECK needs one), the trigger
    default describes old rows and is then dropped, opened_at is backfilled from
    created_at. Downgrading back to 0003 works with the data in place."""
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0003")
    with psycopg.connect(temp_db_url) as conn:
        conn.execute(
            """
            INSERT INTO billing_run (run_date, status, started_at, finished_at, error) VALUES
                ('2026-02-01', 'completed', now(), now(), NULL),
                ('2026-02-02', 'failed', now(), now(), 'db down')
            """
        )
        conn.execute(
            "INSERT INTO party (party_type, given_name, family_name)"
            " VALUES ('Individual', 'Ivan', 'Ivanov')"
        )
        conn.execute(
            "INSERT INTO customer (party_id, name, status) SELECT id, 'I', 'Active' FROM party"
        )
        conn.execute(
            """
            INSERT INTO billing_account (customer_id, name, state, currency, bill_cycle_day)
            SELECT id, 'I main', 'Active', 'USD', 1 FROM customer
            """
        )
    command.upgrade(cfg, "0004")
    with psycopg.connect(temp_db_url) as conn:
        runs = conn.execute(
            "SELECT status, trigger, attempt, failure_kind FROM billing_run ORDER BY run_date"
        ).fetchall()
        assert runs == [("completed", "scheduled", 1, None), ("failed", "scheduled", 1, "run")]
        default = conn.execute(
            "SELECT column_default FROM information_schema.columns"
            " WHERE table_name = 'billing_run' AND column_name = 'trigger'"
        ).fetchone()
        assert default == (None,)
        opened, created = conn.execute(
            "SELECT opened_at, created_at FROM billing_account"
        ).fetchone()
        assert opened == created
    command.downgrade(cfg, "0003")
    with psycopg.connect(temp_db_url) as conn:
        columns = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name IN ('billing_run', 'billing_account')"
            )
        }
        assert columns.isdisjoint({"trigger", "attempt", "failure_kind", "opened_at"})
        assert conn.execute("SELECT count(*) FROM billing_run").fetchone() == (2,)


def test_0005_comments_every_table_and_downgrade_removes_them(temp_db_url):
    """0005 is documentation only: every domain table has a COMMENT (docs/db/ is generated
    from them), and downgrading to 0004 removes every comment it wrote."""
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0005")
    count_tables = """
        SELECT count(*) FILTER (WHERE obj_description(c.oid, 'pg_class') IS NULL),
               count(*) FILTER (WHERE obj_description(c.oid, 'pg_class') IS NOT NULL)
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version'
    """
    count_columns = """
        SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND a.attnum > 0
          AND col_description(c.oid, a.attnum) IS NOT NULL
    """
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute(count_tables).fetchone() == (0, 16)
        assert conn.execute(count_columns).fetchone()[0] > 100
    command.downgrade(cfg, "0004")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute(count_tables).fetchone() == (16, 0)
        assert conn.execute(count_columns).fetchone() == (0,)


def test_0006_on_existing_bills(temp_db_url):
    """0006 on a database with bills (production has them since 2026-10-05): due date =
    bill_date + 30, category from the sign, one issue row per bill dated by its run's
    start, no state changed; every new table and column is commented. Downgrading to 0005
    works with the data in place and restores 0005's comment on customer_bill.state."""
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0005")
    with psycopg.connect(temp_db_url) as conn:
        conn.execute(
            "INSERT INTO party (party_type, given_name, family_name)"
            " VALUES ('Individual', 'Ivan', 'Ivanov')"
        )
        conn.execute(
            "INSERT INTO customer (party_id, name, status) SELECT id, 'I', 'Active' FROM party"
        )
        conn.execute(
            """
            INSERT INTO billing_account
                (customer_id, name, state, currency, bill_cycle_day, opened_at)
            SELECT id, 'I main', 'Active', 'USD', 1, now() FROM customer
            """
        )
        conn.execute(
            """
            INSERT INTO billing_run (run_date, status, started_at, finished_at, trigger)
            VALUES ('2026-10-01', 'completed', '2026-10-01 00:30+03', '2026-10-01 00:31+03',
                    'scheduled'),
                   ('2026-11-01', 'completed', '2026-11-01 00:30+03', '2026-11-01 00:31+03',
                    'scheduled')
            """
        )
        conn.execute(
            """
            INSERT INTO customer_bill (billing_account_id, currency, bill_period, bill_date,
                state, amount_due, billing_run_id)
            SELECT a.id, 'USD', daterange(r.run_date, (r.run_date + interval '1 month')::date),
                   r.run_date, 'new',
                   CASE WHEN r.run_date = '2026-10-01' THEN 300.00 ELSE -116.13 END, r.id
            FROM billing_account a CROSS JOIN billing_run r
            """
        )
        conn.execute(
            "INSERT INTO payment (billing_account_id, currency, amount, received_at,"
            " status, payment_method) SELECT id, 'USD', 10, now(), 'done', 'cash'"
            " FROM billing_account"
        )
    command.upgrade(cfg, "0006")
    with psycopg.connect(temp_db_url) as conn:
        bills = conn.execute(
            "SELECT bill_date, payment_due_date, category, state FROM customer_bill"
            " ORDER BY bill_date"
        ).fetchall()
        assert [(b[0].isoformat(), b[1].isoformat(), b[2], b[3]) for b in bills] == [
            ("2026-10-01", "2026-10-31", "normal", "new"),
            ("2026-11-01", "2026-12-01", "creditNote", "new"),
        ]
        issues = conn.execute(
            """
            SELECT b.bill_date, h.from_state, h.to_state, h.cause, h.changed_at = r.started_at
            FROM customer_bill_state_change h JOIN customer_bill b ON b.id = h.customer_bill_id
            JOIN billing_run r ON r.id = b.billing_run_id ORDER BY b.bill_date
            """
        ).fetchall()
        assert [i[1:] for i in issues] == [(None, "new", "run", True)] * 2
        uncommented = conn.execute(
            """
            SELECT c.relname || coalesce('.' || a.attname, '') FROM pg_class c
            LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0
            WHERE c.relname IN ('bill_allocation', 'customer_bill_state_change')
              AND c.relkind = 'r'
              AND ((a.attname IS NULL AND obj_description(c.oid, 'pg_class') IS NULL)
                OR (a.attname IS NOT NULL AND col_description(c.oid, a.attnum) IS NULL))
            """
        ).fetchall()
        assert uncommented == []
    command.downgrade(cfg, "0005")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT count(*) FROM customer_bill").fetchone() == (2,)
        assert conn.execute(
            "SELECT count(*) FROM pg_tables WHERE tablename IN"
            " ('bill_allocation', 'customer_bill_state_change')"
        ).fetchone() == (0,)
        comment = conn.execute(
            "SELECT col_description('customer_bill'::regclass, attnum) FROM pg_attribute"
            " WHERE attrelid = 'customer_bill'::regclass AND attname = 'state'"
        ).fetchone()
        assert comment == ("TMF678 state; bills are written as new.",)


def test_0007_on_existing_abrs(temp_db_url):
    """0007 on a database with ABRs (production has them since 2026-10-05): applied_at =
    the started_at of the run that wrote each one, NOT NULL and commented. Downgrading to
    0006 drops the column with the data in place."""
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0006")
    with psycopg.connect(temp_db_url) as conn:
        conn.execute(
            "INSERT INTO party (party_type, given_name, family_name)"
            " VALUES ('Individual', 'Ivan', 'Ivanov')"
        )
        conn.execute(
            "INSERT INTO customer (party_id, name, status) SELECT id, 'I', 'Active' FROM party"
        )
        conn.execute(
            """
            INSERT INTO billing_account
                (customer_id, name, state, currency, bill_cycle_day, opened_at)
            SELECT id, 'I main', 'Active', 'USD', 1, now() FROM customer
            """
        )
        conn.execute(
            "INSERT INTO product_specification (name, lifecycle_status) VALUES ('S', 'Active')"
        )
        conn.execute(
            """
            INSERT INTO product_offering (name, lifecycle_status, product_specification_id,
                valid_for)
            SELECT 'O', 'Active', id, tstzrange('2026-09-01', NULL) FROM product_specification
            """
        )
        conn.execute(
            """
            INSERT INTO product_order (billing_account_id, state, order_date,
                requested_start_date)
            SELECT id, 'completed', '2026-09-20 10:00+03', '2026-09-20 10:00+03'
            FROM billing_account
            """
        )
        conn.execute(
            """
            INSERT INTO product_order_item (product_order_id, item_id, action, state,
                product_offering_id)
            SELECT o.id, '1', 'add', 'completed', p.id FROM product_order o, product_offering p
            """
        )
        conn.execute(
            """
            INSERT INTO product (billing_account_id, product_offering_id, status, start_date,
                created_by_item_id)
            SELECT o.billing_account_id, i.product_offering_id, 'active', '2026-10-01', i.id
            FROM product_order_item i JOIN product_order o ON o.id = i.product_order_id
            """
        )
        conn.execute(
            """
            INSERT INTO billing_run (run_date, status, started_at, finished_at, trigger)
            VALUES ('2026-10-01', 'completed', '2026-10-01 00:30+03', '2026-10-01 00:31+03',
                    'scheduled'),
                   ('2026-10-02', 'completed', '2026-10-02 00:30+03', '2026-10-02 00:31+03',
                    'scheduled')
            """
        )
        conn.execute(
            """
            INSERT INTO applied_customer_billing_rate (product_id, billing_account_id,
                charge_type, amount, currency, period, billing_run_id)
            SELECT p.id, p.billing_account_id, 'recurring', 10.00, 'USD',
                   daterange(r.run_date, r.run_date + 1), r.id
            FROM product p CROSS JOIN billing_run r
            """
        )
    command.upgrade(cfg, "0007")
    with psycopg.connect(temp_db_url) as conn:
        rows = conn.execute(
            """
            SELECT r.run_date, a.applied_at = r.started_at, a.applied_at <> a.created_at
            FROM applied_customer_billing_rate a JOIN billing_run r ON r.id = a.billing_run_id
            ORDER BY r.run_date
            """
        ).fetchall()
        assert [(d.isoformat(), same, other) for d, same, other in rows] == [
            ("2026-10-01", True, True),
            ("2026-10-02", True, True),
        ]
        column = conn.execute(
            """
            SELECT a.attnotnull, col_description(a.attrelid, a.attnum) FROM pg_attribute a
            WHERE a.attrelid = 'applied_customer_billing_rate'::regclass
              AND a.attname = 'applied_at'
            """
        ).fetchone()
        assert column is not None and column[0] is True
        assert column[1].startswith("TMF678 date: ")
    command.downgrade(cfg, "0006")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT count(*) FROM applied_customer_billing_rate").fetchone() == (2,)
        assert conn.execute(
            "SELECT count(*) FROM pg_attribute WHERE attname = 'applied_at'"
            " AND attrelid = 'applied_customer_billing_rate'::regclass"
        ).fetchone() == (0,)


@pytest.mark.smoke
def test_cloned_test_databases_are_at_the_current_head(db_conn):
    """Most tests get their database from a template migrated once per session
    (conftest.template_db). It must be at the head of the migration scripts."""
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    assert db_conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(head,)]
    tables = db_conn.execute(
        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
    ).fetchone()[0]
    assert tables == 64  # 63 domain tables + alembic_version


def test_0027_offering_changes(temp_db_url):
    """Existing ABRs bill their product's offering; afterwards an ABR without one gets the
    offering on its first day, ranges may overlap but first days are unique, and a modify
    item may be quoted. Downgrading to 0026 refuses while a change exists."""
    from tests import factories

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0026")
    with psycopg.connect(temp_db_url) as conn:
        w = factories.world(conn)
        old = factories.charge(conn, w, factories.days(date(2026, 1, 1), date(2026, 2, 1)))
        other = factories.offering(conn)
    command.upgrade(cfg, "0027")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute(
            "SELECT product_offering_id FROM applied_customer_billing_rate WHERE id = %s",
            (old["id"],),
        ).fetchone() == (w.offering["id"],)
        item = factories.add_order(conn, w.account["id"], other["id"])["items"][0]
        conn.execute(
            "INSERT INTO product_offering_change"
            " (product_id, change_date, product_offering_id, changed_by_item_id)"
            " VALUES (%s, '2026-01-10', %s, %s)",
            (w.product["id"], other["id"], item["id"]),
        )
        for day, offering in (("2026-01-09", w.offering), ("2026-01-10", other)):
            assert conn.execute(
                "SELECT product_offering_on(%s, %s)", (w.product["id"], day)
            ).fetchone() == (offering["id"],)
        new = factories.charge(conn, w, factories.days(date(2026, 1, 10), date(2026, 2, 1)))
        assert new["product_offering_id"] == other["id"]  # overlapping, another first day
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            factories.charge(conn, w, factories.days(date(2026, 1, 10), date(2026, 1, 20)))
    with pytest.raises(Exception, match="offering changes exist"):
        command.downgrade(cfg, "0026")
    with psycopg.connect(temp_db_url) as conn:
        conn.execute("DELETE FROM product_offering_change")
        conn.execute("DELETE FROM applied_customer_billing_rate WHERE id = %s", (new["id"],))
    command.downgrade(cfg, "0026")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT count(*) FROM applied_customer_billing_rate").fetchone() == (1,)
    command.upgrade(cfg, "head")


def test_0028_last_gaps(temp_db_url):
    """Existing products are named after their offering; a product inserted without a
    name gets its offering's; a modify item carries an offering or a status, not both.
    Downgrading to 0027 refuses while a status item or an alteration price exists."""
    from tests import factories

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0027")
    with psycopg.connect(temp_db_url) as conn:
        w = factories.world(conn)
    command.upgrade(cfg, "0028")
    with psycopg.connect(temp_db_url) as conn:
        name = "SELECT name, details FROM product WHERE id = %s"
        assert conn.execute(name, (w.product["id"],)).fetchone() == ("Fiber 100", {})
        later = factories.product(conn, w.account["id"], w.offering["id"], date(2026, 2, 1))
        assert later["name"] == "Fiber 100"
        item = factories.add_order(conn, w.account["id"], w.offering["id"])["items"][0]
        modify = (
            "UPDATE product_order_item SET action = 'modify', product_id = %s,"
            " product_offering_id = %s, product_status = %s WHERE id = %s"
        )
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(modify, (w.product["id"], w.offering["id"], "suspended", item["id"]))
        conn.execute(modify, (w.product["id"], None, "suspended", item["id"]))
    with pytest.raises(Exception, match="status items exist"):
        command.downgrade(cfg, "0027")
    with psycopg.connect(temp_db_url) as conn:
        conn.execute(
            "UPDATE product_order_item SET action = 'add', product_id = NULL,"
            " product_offering_id = %s, product_status = NULL WHERE id = %s",
            (w.offering["id"], item["id"]),
        )
        conn.execute(
            "INSERT INTO product_offering_price (product_offering_id, name, price_type, amount,"
            " currency, valid_for, lifecycle_status) VALUES (%s, 'Loyalty', 'discount', 10,"
            " 'USD', tstzrange(now(), NULL), 'Active')",
            (w.offering["id"],),
        )
    with pytest.raises(Exception, match="discount, allowance or penalty"):
        command.downgrade(cfg, "0027")
    with psycopg.connect(temp_db_url) as conn:
        conn.execute("DELETE FROM product_offering_price WHERE price_type = 'discount'")
    command.downgrade(cfg, "0027")
    command.upgrade(cfg, "head")


def test_0016_order_cancellation_round_trip(temp_db_url):
    """Existing orders stay as they are (no cancellation date); a cancelled order needs
    one; downgrading to 0015 works with a cancellation stored and keeps the orders."""
    from tests import factories

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0015")
    with psycopg.connect(temp_db_url) as conn:
        account = factories.account(conn)
        order_id = conn.execute(
            "INSERT INTO product_order (billing_account_id, state, order_date)"
            " VALUES (%s, 'completed', %s) RETURNING id",
            (account["id"], factories.NOW),
        ).fetchone()[0]
    command.upgrade(cfg, "0016")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT state, cancellation_date FROM product_order").fetchone() == (
            "completed",
            None,
        )
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE product_order SET state = 'cancelled'")
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE product_order SET cancellation_reason = 'x'")
        conn.execute(
            "UPDATE product_order SET state = 'cancelled', cancellation_date = %s",
            (factories.NOW,),
        )
        conn.execute(
            "INSERT INTO cancel_product_order (product_order_id, creation_date) VALUES (%s, %s)",
            (order_id, factories.NOW),
        )
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            conn.execute(
                "INSERT INTO cancel_product_order (product_order_id, creation_date)"
                " VALUES (%s, %s)",
                (order_id, factories.NOW),
            )
    command.downgrade(cfg, "0015")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT to_regclass('cancel_product_order')").fetchone() == (None,)
        assert conn.execute("SELECT count(*) FROM product_order").fetchone() == (1,)
    # upgrading again: the order cancelled before the downgrade gets a date back
    command.upgrade(cfg, "0016")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute(
            "SELECT state, cancellation_date IS NOT NULL FROM product_order"
        ).fetchone() == ("cancelled", True)


def test_0017_0018_events_round_trip(temp_db_url):
    """Hubs, events and deliveries; downgrading to 0016 drops them all and no other data."""
    from tests import factories

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0018")
    with psycopg.connect(temp_db_url) as conn:
        factories.account(conn)
        hub = conn.execute(
            "INSERT INTO event_subscription (api, callback, subscribed_at)"
            " VALUES ('TMF629', 'https://x.example/e', now()) RETURNING id"
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO event_subscription (api, callback, query, subscribed_at)"
                " VALUES ('TMF629', 'https://x.example/e', 'eventType=X', now())"
            )  # a query needs its parsed event types
        event = conn.execute(
            "INSERT INTO event (api, event_type, resource, resource_id, occurred_at, payload)"
            " VALUES ('TMF629', 'CustomerCreateEvent', 'customer', 'c1', now(), '{}')"
            " RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO event_delivery (event_id, subscription_id, next_attempt_at)"
            " VALUES (%s, %s, now())",
            (event, hub),
        )
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE event_delivery SET state = 'delivered'")  # needs finished_at
    command.downgrade(cfg, "0016")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute(
            "SELECT to_regclass('event_subscription'), to_regclass('event'),"
            " to_regclass('event_delivery'), to_regtype('event_delivery_state')"
        ).fetchone() == (None, None, None, None)
        assert conn.execute("SELECT count(*) FROM billing_account").fetchone() == (1,)
    command.upgrade(cfg, "0018")


def test_0019_0020_round_trip(temp_db_url):
    """Cycle specifications and the order's descriptive attributes: both CHECK what billing
    and TMF allow; downgrading to 0018 drops them and keeps accounts and orders."""
    from tests import factories

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0020")
    with psycopg.connect(temp_db_url) as conn:
        account = factories.account(conn)
        spec = conn.execute(
            "INSERT INTO billing_cycle_specification (name) VALUES ('Business') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "UPDATE billing_account SET cycle_specification_id = %s WHERE id = %s",
            (spec, account["id"]),
        )
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO billing_cycle_specification (name, frequency) VALUES ('W', 'weekly')"
            )
        order = conn.execute(
            "INSERT INTO product_order (billing_account_id, state, order_date, priority)"
            " VALUES (%s, 'rejected', %s, '1') RETURNING id",
            (account["id"], factories.NOW),
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE product_order SET priority = '9' WHERE id = %s", (order,))
    command.downgrade(cfg, "0018")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT to_regclass('billing_cycle_specification')").fetchone() == (
            None,
        )
        assert conn.execute("SELECT count(*) FROM product_order").fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM billing_account").fetchone() == (1,)
    command.upgrade(cfg, "0020")


def test_0009_catalog_round_trip(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0008")
    with psycopg.connect(temp_db_url) as conn:
        conn.execute(
            "INSERT INTO product_specification (name, lifecycle_status) VALUES ('Legacy', 'Active')"
        )
    command.upgrade(cfg, "0009")
    with psycopg.connect(temp_db_url) as conn:
        root = conn.execute("INSERT INTO category (name) VALUES ('Root') RETURNING id").fetchone()[
            0
        ]
        catalog = conn.execute(
            "INSERT INTO product_catalog (name) VALUES ('Catalog') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO catalog_category (catalog_id, category_id) VALUES (%s, %s)",
            (catalog, root),
        )
        assert conn.execute("SELECT name FROM product_specification").fetchall() == [("Legacy",)]
    command.downgrade(cfg, "0008")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute(
            "SELECT to_regclass('category'), to_regclass('product_catalog')"
        ).fetchone() == (None, None)
        assert conn.execute("SELECT name FROM product_specification").fetchall() == [("Legacy",)]
    command.upgrade(cfg, "0009")


def test_0013_financial_accounts_round_trip(temp_db_url):
    """Existing billing accounts stay unlinked; a link survives only while 0013 does, and
    downgrading to 0012 keeps the accounts."""
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0012")
    with psycopg.connect(temp_db_url) as conn:
        party = conn.execute(
            "INSERT INTO party (party_type, organization_name)"
            " VALUES ('Organization', 'Romashka') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO customer (party_id, name, status) VALUES (%s, 'R', 'Active')", (party,)
        )
        conn.execute(
            "INSERT INTO billing_account"
            " (customer_id, name, state, currency, bill_cycle_day, opened_at)"
            " SELECT id, 'Main', 'Active', 'USD', 1, now() FROM customer"
        )
    command.upgrade(cfg, "0013")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT financial_account_id FROM billing_account").fetchall() == [
            (None,)
        ]
        financial = conn.execute(
            "INSERT INTO financial_account (party_id, name) VALUES (%s, 'Group') RETURNING id",
            (party,),
        ).fetchone()[0]
        conn.execute("UPDATE billing_account SET financial_account_id = %s", (financial,))
    command.downgrade(cfg, "0012")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT to_regclass('financial_account')").fetchone() == (None,)
        assert conn.execute("SELECT name FROM billing_account").fetchall() == [("Main",)]
    command.upgrade(cfg, "head")


def test_0014_party_accounts_round_trip(temp_db_url):
    """A party account may only join a financial account of its own party (composite FK);
    downgrading to 0013 drops party accounts and their enum, keeping financial accounts."""
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0014")
    with psycopg.connect(temp_db_url) as conn:
        owner, other = (
            conn.execute(
                "INSERT INTO party (party_type, organization_name) VALUES ('Organization', %s)"
                " RETURNING id",
                (name,),
            ).fetchone()[0]
            for name in ("Romashka", "Other")
        )
        financial = conn.execute(
            "INSERT INTO financial_account (party_id, name) VALUES (%s, 'Group') RETURNING id",
            (owner,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO party_account (kind, party_id, name, financial_account_id)"
            " VALUES ('SettlementAccount', %s, 'Payouts', %s)",
            (owner, financial),
        )
    with psycopg.connect(temp_db_url) as conn, pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO party_account (kind, party_id, name, financial_account_id)"
            " VALUES ('PartyAccount', %s, 'Foreign', %s)",
            (other, financial),
        )
    command.downgrade(cfg, "0013")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute(
            "SELECT to_regclass('party_account'), to_regtype('party_account_kind')"
        ).fetchone() == (None, None)
        assert conn.execute("SELECT name FROM financial_account").fetchall() == [("Group",)]
    command.upgrade(cfg, "head")


def test_0015_catalog_jobs_round_trip(temp_db_url):
    """A stored job is finished: a failed one has its errorLog and only an export has a
    query; downgrading to 0014 drops the table and both enums."""
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0015")
    insert = (
        "INSERT INTO catalog_job (kind, status, content_type, path, query, error_log,"
        " creation_date, completion_date) VALUES (%s, %s, 'application/json', '/x', %s, %s,"
        " now(), now())"
    )
    with psycopg.connect(temp_db_url) as conn:
        conn.execute(insert, ("ExportJob", "Succeeded", "productCatalog.id=1", None))
        conn.execute(insert, ("ImportJob", "Failed", None, "why"))
    for values in (
        ("ImportJob", "Failed", None, None),  # failed without a log
        ("ImportJob", "Succeeded", None, "log"),  # a log on success
        ("ImportJob", "Succeeded", "q", None),  # a query on an import
    ):
        with psycopg.connect(temp_db_url) as conn, pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(insert, values)
    command.downgrade(cfg, "0014")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute(
            "SELECT to_regclass('catalog_job'), to_regtype('catalog_job_kind'),"
            " to_regtype('catalog_job_status')"
        ).fetchone() == (None, None, None)
    command.upgrade(cfg, "head")


def test_0008_preserves_legacy_orders_without_inventing_quotes(temp_db_url):
    from tests import factories

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0007")
    with psycopg.connect(temp_db_url) as conn:
        account = factories.account(conn)
        offering = factories.offering(conn)
        order_id = conn.execute(
            "INSERT INTO product_order (billing_account_id, state, order_date)"
            " VALUES (%s, 'completed', %s) RETURNING id",
            (account["id"], factories.NOW),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO product_order_item (product_order_id, item_id, action, state,"
            " product_offering_id) VALUES (%s, '1', 'add', 'completed', %s)",
            (order_id, offering["id"]),
        )
    command.upgrade(cfg, "0008")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute(
            "SELECT quoted_price_id, quoted_price_name, quoted_amount, quoted_currency"
            " FROM product_order_item"
        ).fetchone() == (None, None, None, None)
        comments = conn.execute(
            "SELECT col_description(attrelid, attnum) FROM pg_attribute"
            " WHERE attrelid = 'product_order_item'::regclass AND attname LIKE 'quoted_%'"
        ).fetchall()
        assert len(comments) == 4 and all(row[0] for row in comments)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE product_order_item SET quoted_amount = 1")
    command.downgrade(cfg, "0007")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT count(*) FROM product_order_item").fetchone() == (1,)
        assert conn.execute(
            "SELECT count(*) FROM pg_attribute WHERE attrelid = 'product_order_item'::regclass"
            " AND attname LIKE 'quoted_%' AND NOT attisdropped"
        ).fetchone() == (0,)
    command.upgrade(cfg, "0008")


def test_0021_meta_payment_method_and_item_position(temp_db_url):
    """Existing payments answer what they answered (the one word as name and @type);
    existing items keep the order they were listed in (item_id as text); the meta
    columns refuse an empty value; downgrading to 0020 keeps every row."""
    from tests import factories

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0020")
    with psycopg.connect(temp_db_url) as conn:
        account = factories.account(conn)
        conn.execute(
            "INSERT INTO payment (billing_account_id, currency, amount, received_at,"
            " payment_method, status) VALUES (%s, 'USD', 10, %s, 'cash', 'done')",
            (account["id"], factories.NOW),
        )
        order_id = conn.execute(
            "INSERT INTO product_order (billing_account_id, state, order_date)"
            " VALUES (%s, 'rejected', %s) RETURNING id",
            (account["id"], factories.NOW),
        ).fetchone()[0]
        for item_id in ("2", "10", "1"):
            conn.execute(
                "INSERT INTO product_order_item (product_order_id, item_id, action, state)"
                " VALUES (%s, %s, 'modify', 'rejected')",
                (order_id, item_id),
            )
    command.upgrade(cfg, "0021")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute(
            "SELECT payment_method_name, payment_method_type FROM payment"
        ).fetchone() == ("cash", "cash")
        positions = conn.execute(
            "SELECT item_id FROM product_order_item ORDER BY position"
        ).fetchall()
        assert [row[0] for row in positions] == ["1", "10", "2"]
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "UPDATE payment SET payment_method_name = NULL, payment_method_type = NULL"
            )
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE party SET base_type = ''")
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            conn.execute("UPDATE product_order_item SET position = 1")
    command.downgrade(cfg, "0020")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT count(*) FROM product_order_item").fetchone() == (3,)
        assert conn.execute("SELECT payment_method FROM payment").fetchone() == ("cash",)
        assert conn.execute(
            "SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid"
            " WHERE a.attname = 'base_type' AND NOT a.attisdropped"
            " AND c.relnamespace = 'public'::regnamespace"
        ).fetchone() == (0,)
    command.upgrade(cfg, "0021")


def test_0022_party_status_and_details(temp_db_url):
    """Existing parties get no status and no documents; a status must be one of its
    party type's; downgrading to 0021 keeps every row."""
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0021")
    with psycopg.connect(temp_db_url) as conn:
        conn.execute(
            "INSERT INTO party (party_type, given_name, family_name) VALUES"
            " ('Individual', 'Ivan', 'Ivanov')"
        )
        conn.execute(
            "INSERT INTO party (party_type, organization_name) VALUES ('Organization', 'R')"
        )
        conn.execute(
            "INSERT INTO customer (party_id, name, status) SELECT id, 'C', 'Created' FROM party"
        )
    command.upgrade(cfg, "0022")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT DISTINCT status, details FROM party").fetchall() == [(None, {})]
        assert conn.execute("SELECT DISTINCT details FROM customer").fetchall() == [({},)]
        for party_type, wrong in (("Individual", "closed"), ("Organization", "deceased")):
            with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute(
                    "UPDATE party SET status = %s WHERE party_type = %s", (wrong, party_type)
                )
        conn.execute("UPDATE party SET status = 'validated'")
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE customer SET details = '[]'")
    command.downgrade(cfg, "0021")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT count(*) FROM party").fetchone() == (2,)
        assert conn.execute("SELECT count(*) FROM customer").fetchone() == (2,)
    command.upgrade(cfg, "0022")


def test_0023_account_details(temp_db_url):
    """Existing accounts and cycle specifications get no description, type or documents
    (an account then answers its anchor party alone); details must be an object;
    downgrading to 0022 keeps every row."""
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0022")
    with psycopg.connect(temp_db_url) as conn:
        party = conn.execute(
            "INSERT INTO party (party_type, organization_name)"
            " VALUES ('Organization', 'Romashka') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO customer (party_id, name, status) VALUES (%s, 'R', 'Active')", (party,)
        )
        conn.execute(
            "INSERT INTO billing_account"
            " (customer_id, name, state, currency, bill_cycle_day, opened_at)"
            " SELECT id, 'Main', 'Active', 'USD', 1, now() FROM customer"
        )
        conn.execute(
            "INSERT INTO financial_account (party_id, name) VALUES (%s, 'Group')", (party,)
        )
        conn.execute(
            "INSERT INTO party_account (kind, party_id, name) VALUES ('PartyAccount', %s, 'P')",
            (party,),
        )
        conn.execute("INSERT INTO billing_cycle_specification (name) VALUES ('Business')")
    command.upgrade(cfg, "0023")
    tables = (
        "billing_account",
        "financial_account",
        "party_account",
        "billing_cycle_specification",
    )
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute(
            "SELECT description, account_type, details FROM billing_account"
        ).fetchall() == [(None, None, {})]
        for table in tables:
            name = sql.Identifier(table)
            select = sql.SQL("SELECT details FROM {}").format(name)
            assert conn.execute(select).fetchall() == [({},)], table
            with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute(sql.SQL("UPDATE {} SET details = '[]'").format(name))
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE billing_account SET account_type = ''")
        conn.execute('UPDATE billing_account SET details = \'{"paymentStatus": "due"}\'')
    command.downgrade(cfg, "0022")
    with psycopg.connect(temp_db_url) as conn:
        for table in tables:
            count = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            assert conn.execute(count).fetchone() == (1,), table
    command.upgrade(cfg, "head")


def test_0024_catalog_details_and_optional_attributes(temp_db_url):
    """An existing offering code becomes the externalIdentifier it was answered as;
    other entries get empty documents. Afterwards an offering may lack a specification
    and validFor, and only an unattached price may lack an amount (with its currency).
    Downgrading to 0023 keeps the rows that fit the old schema."""
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0023")
    with psycopg.connect(temp_db_url) as conn:
        spec = conn.execute(
            "INSERT INTO product_specification (name, lifecycle_status)"
            " VALUES ('Fiber', 'Active') RETURNING id"
        ).fetchone()[0]
        for code in ("MOB-L", None):
            conn.execute(
                "INSERT INTO product_offering"
                " (product_specification_id, name, lifecycle_status, valid_for, code)"
                " VALUES (%s, 'O', 'Active', tstzrange(now(), NULL), %s)",
                (spec, code),
            )
        offering = conn.execute("SELECT id FROM product_offering LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT INTO product_offering_price (product_offering_id, name, lifecycle_status,"
            " price_type, recurring_period, amount, currency, valid_for)"
            " VALUES (%s, 'P', 'Active', 'recurring', 'monthly', 5, 'USD', tstzrange(now(), NULL))",
            (offering,),
        )
        conn.execute("INSERT INTO product_catalog (name) VALUES ('Main')")
    command.upgrade(cfg, "0024")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute(
            "SELECT code, details FROM product_offering ORDER BY code"
        ).fetchall() == [
            (
                "MOB-L",
                {
                    "externalIdentifier": [
                        {
                            "id": "MOB-L",
                            "externalIdentifierType": "offeringCode",
                            "owner": "bss",
                            "@type": "ExternalIdentifier",
                        }
                    ]
                },
            ),
            (None, {}),
        ]
        for table in ("product_specification", "product_offering_price", "product_catalog"):
            select = sql.SQL("SELECT details FROM {}").format(sql.Identifier(table))
            assert conn.execute(select).fetchall() == [({},)], table
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE product_offering_price SET amount = NULL, currency = NULL")
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "UPDATE product_offering_price SET product_offering_id = NULL, amount = NULL"
            )
        with conn.transaction():  # unattached, no amount: fine; the offering needs nothing
            conn.execute(
                "INSERT INTO product_offering_price (name, lifecycle_status, price_type,"
                " valid_for) VALUES ('D', 'Active', 'oneTime', tstzrange(now(), NULL))"
            )
            conn.execute(
                "INSERT INTO product_offering (name, lifecycle_status) VALUES ('Bare', 'Active')"
            )
            conn.execute("DELETE FROM product_offering WHERE name = 'Bare'")
            conn.execute("DELETE FROM product_offering_price WHERE name = 'D'")
    command.downgrade(cfg, "0023")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT count(*) FROM product_offering").fetchone() == (2,)
        assert conn.execute("SELECT count(*) FROM product_offering_price").fetchone() == (1,)
    command.upgrade(cfg, "head")


def test_0025_taxes(temp_db_url):
    """Existing ABRs, payments and refunds have no tax. The upgrade refuses a stored price
    tax billing could not apply, and a taxed price on an offering already charged (its
    charges would not match it). Downgrading to 0024 keeps every row."""
    from datetime import date
    from decimal import Decimal

    from app.repository import payments
    from tests import factories

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0024")
    with psycopg.connect(temp_db_url) as conn:
        w = factories.world(conn)
        factories.charge(conn, w, factories.days(date(2026, 1, 1), date(2026, 2, 1)))
        payments.insert_payment(
            conn,
            billing_account_id=w.account["id"],
            amount=Decimal("100.00"),
            currency="USD",
            received_at=w.run["started_at"],
            payment_method="cash",
        )
        price = (
            "INSERT INTO product_offering_price (product_offering_id, name, lifecycle_status,"
            " price_type, recurring_period, amount, currency, valid_for, details)"
            " VALUES (%s, 'Taxed', 'Active', 'recurring', 'monthly', 10, 'USD',"
            " tstzrange(now(), NULL), %s)"
        )
        vat = '{"tax": [{"@type": "TaxItem", "taxCategory": "VAT", "taxRate": 20}]}'
        conn.execute(price, (w.offering["id"], vat))
    with pytest.raises(RuntimeError, match="already charged"):
        command.upgrade(cfg, "0025")
    with psycopg.connect(temp_db_url) as conn:
        conn.execute("UPDATE product_offering_price SET product_offering_id = NULL")
        conn.execute(
            'UPDATE product_offering_price SET details = \'{"tax": [{"taxCategory": "VAT"}]}\''
        )
    with pytest.raises(RuntimeError, match="without taxRate"):
        command.upgrade(cfg, "0025")
    with psycopg.connect(temp_db_url) as conn:
        conn.execute("UPDATE product_offering_price SET details = %s", (vat,))
    command.upgrade(cfg, "0025")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute(
            "SELECT applied_tax, tax_amount FROM applied_customer_billing_rate"
        ).fetchall() == [([], Decimal("0.00"))]
        assert conn.execute("SELECT tax_amount FROM payment").fetchall() == [(Decimal("0.00"),)]
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE applied_customer_billing_rate SET tax_amount = 1")
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE payment SET tax_amount = amount + 1")
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE payment SET tax_amount = -1")
    command.downgrade(cfg, "0024")
    with psycopg.connect(temp_db_url) as conn:
        for table in ("applied_customer_billing_rate", "payment", "product_offering_price"):
            select = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            assert conn.execute(select).fetchone() == (1,), table
    command.upgrade(cfg, "head")


def test_0026_order_details_drafts_and_nested_items(temp_db_url):
    """Existing orders were acknowledged, with no documents, top-level items and an
    account. Afterwards only a draft (or a cancelled order) may lack an account, and a
    nested item's parent is in its own order. Downgrading to 0025 refuses a draft."""
    from tests import factories

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0025")
    with psycopg.connect(temp_db_url) as conn:
        w = factories.world(conn)
        other = factories.add_order(conn, w.account["id"], w.offering["id"])
    command.upgrade(cfg, "0026")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute(
            "SELECT DISTINCT requested_initial_state, details FROM product_order"
        ).fetchall() == [("acknowledged", {})]
        assert conn.execute(
            "SELECT DISTINCT parent_id, details FROM product_order_item"
        ).fetchall() == [(None, {})]
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE product_order SET billing_account_id = NULL")
        (item,) = conn.execute(
            "SELECT id FROM product_order_item WHERE product_order_id = %s", (other["id"],)
        ).fetchone()
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
            conn.execute(
                "UPDATE product_order_item SET parent_id = %s WHERE product_order_id <> %s",
                (item, other["id"]),
            )
        conn.execute(
            "UPDATE product_order SET state = 'draft', billing_account_id = NULL WHERE id = %s",
            (other["id"],),
        )
    with pytest.raises(Exception, match="null value"):
        command.downgrade(cfg, "0025")
    with psycopg.connect(temp_db_url) as conn:
        conn.execute("DELETE FROM product_order_item WHERE product_order_id = %s", (other["id"],))
        conn.execute("DELETE FROM product_order WHERE id = %s", (other["id"],))
    command.downgrade(cfg, "0025")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT count(*) FROM product_order").fetchone() == (1,)
    command.upgrade(cfg, "head")
