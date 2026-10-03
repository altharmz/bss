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


def test_cloned_test_databases_are_at_the_current_head(db_conn):
    """Most tests get their database from a template migrated once per session
    (conftest.template_db). It must be at the head of the migration scripts."""
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    assert db_conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(head,)]
    tables = db_conn.execute(
        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
    ).fetchone()[0]
    assert tables == 17  # 16 domain tables + alembic_version
