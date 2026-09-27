from alembic.config import Config

from alembic import command


def test_alembic_upgrade_head_on_clean_database(temp_db_url):
    command.upgrade(Config("alembic.ini"), "head")


def test_alembic_downgrade_then_upgrade_roundtrip(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
