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


def test_cloned_test_databases_are_at_the_current_head(db_conn):
    """Most tests get their database from a template migrated once per session
    (conftest.template_db). It must be at the head of the migration scripts."""
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    assert db_conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(head,)]
    tables = db_conn.execute(
        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
    ).fetchone()[0]
    assert tables == 15  # 14 domain tables + alembic_version
