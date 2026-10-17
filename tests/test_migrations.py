import psycopg
import pytest
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from alembic import command


def test_alembic_upgrade_head_on_clean_database(temp_db_url):
    command.upgrade(Config("alembic.ini"), "head")


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
           SELECT id, 'oneTime', 1, 'RUB', tstzrange(now(), NULL) FROM product_offering""",
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
            SELECT id, 'I main', 'Active', 'RUB', 1 FROM customer
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
            SELECT id, 'I main', 'Active', 'RUB', 1, now() FROM customer
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
            SELECT a.id, 'RUB', daterange(r.run_date, (r.run_date + interval '1 month')::date),
                   r.run_date, 'new',
                   CASE WHEN r.run_date = '2026-10-01' THEN 300.00 ELSE -116.13 END, r.id
            FROM billing_account a CROSS JOIN billing_run r
            """
        )
        conn.execute(
            "INSERT INTO payment (billing_account_id, currency, amount, received_at,"
            " status, payment_method) SELECT id, 'RUB', 10, now(), 'done', 'cash'"
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
            SELECT id, 'I main', 'Active', 'RUB', 1, now() FROM customer
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
            SELECT p.id, p.billing_account_id, 'recurring', 10.00, 'RUB',
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


def test_cloned_test_databases_are_at_the_current_head(db_conn):
    """Most tests get their database from a template migrated once per session
    (conftest.template_db). It must be at the head of the migration scripts."""
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    assert db_conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(head,)]
    tables = db_conn.execute(
        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
    ).fetchone()[0]
    assert tables == 36  # 35 domain tables + alembic_version


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
            " SELECT id, 'Main', 'Active', 'RUB', 1, now() FROM customer"
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
