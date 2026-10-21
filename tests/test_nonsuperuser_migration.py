"""Migrations must work as the production role, not only as the local superuser.

Mirrors vps-infra's postgres/create-app-db.sh: a LOGIN role with NOSUPERUSER NOCREATEDB
NOCREATEROLE that owns its database, with PUBLIC's database privileges revoked. The
interesting part is CREATE EXTENSION btree_gist, which only works because it is a trusted
extension (PG13+) and the role owns the database.
"""

import secrets
import uuid
from urllib.parse import urlsplit, urlunsplit

import psycopg
from alembic.config import Config
from psycopg import sql

from alembic import command
from app.config import settings


def _url_as(admin_url: str, user: str, password: str, dbname: str) -> str:
    parts = urlsplit(admin_url)
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, f"{user}:{password}@{host}", f"/{dbname}", "", ""))


def test_migrations_run_as_non_superuser_database_owner(monkeypatch):
    admin_url = settings.database_url
    name = f"bss_nosu_{uuid.uuid4().hex[:8]}"
    password = secrets.token_hex(16)
    ident = sql.Identifier(name)

    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                ident, sql.Literal(password)
            )
        )
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(ident, ident))
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(ident))

    role_url = _url_as(admin_url, name, password, name)
    monkeypatch.setenv("DATABASE_URL", role_url)
    try:
        with psycopg.connect(role_url) as conn:
            is_super = conn.execute(
                "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
            ).fetchone()[0]
        assert is_super is False, "the test role must not be a superuser"

        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")

        with psycopg.connect(role_url) as conn:
            owner = conn.execute(
                "SELECT extowner::regrole::text FROM pg_extension WHERE extname = 'btree_gist'"
            ).fetchone()
            tables = conn.execute(
                "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
                " AND tableowner = current_user"
            ).fetchone()[0]
        assert owner == (name,)
        assert tables == 53  # 52 domain tables + alembic_version
    finally:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(ident))
            conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(ident))
