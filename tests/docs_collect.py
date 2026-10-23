"""Inventory for the generated reference docs (`make docs` -> docs/api/, docs/db/).

Run in the app container: `python -m tests.docs_collect > .docs/collected.json`. Prints
one JSON object to stdout; scripts/docs/generate.py (host, standard library only) turns
it into Markdown. Everything but the JSON goes to stderr.

- operations: every operation of app.openapi() - FastAPI's public interface - as
              {method, path, name, params};
- filters:    for every TMF list operation, the query filters the handler accepts. The
              handlers read query parameters themselves (app/api/listing.py), so
              app.openapi() does not know them; they are read from the code's own 400
              answer to an unknown parameter ("filters: ..."), on a TestClient that never
              opens a database connection (the filters are parsed before the
              transaction);
- specs:      for every spec in spec/ (tests/contract.py SPEC_FILES): title, version,
              server URL, and every operation with its parameters, request schema and
              response schemas (as $ref names);
- extension:  the BssBillingAccount JSON Schema the app serves;
- db:         the catalog of a throwaway database migrated to head (tables with their
              COMMENTs, columns, constraints, indexes, triggers, enums), dropped again.

Lives in tests/ (like tests/progress_collect.py) because the app container mounts it; it
is a tool, not a test.
"""

import contextlib
import json
import os
import sys
from typing import Any

import psycopg
import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import sql

from alembic import command
from app.config import settings
from tests import scratch_db
from tests.contract import SPEC_DIR, SPEC_FILES

METHODS = ("get", "post", "put", "patch", "delete")
PROBE = "docs_probe"


def _ref_name(schema: dict[str, Any] | None) -> str | None:
    """The schema name a request/response points at, or a short description."""
    if not schema:
        return None
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if schema.get("type") == "array" and isinstance(schema.get("items"), dict):
        inner = _ref_name(schema["items"])
        return f"array of {inner}" if inner else "array"
    return schema.get("type", "object")


def _content_schema(body: dict[str, Any]) -> str | None:
    for media in (body.get("content") or {}).values():
        return _ref_name(media.get("schema"))
    return _ref_name(body.get("schema"))  # swagger 2


def _resolve(doc: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while isinstance(node, dict) and "$ref" in node and node["$ref"].startswith("#/"):
        target: Any = doc
        for part in node["$ref"][2:].split("/"):
            target = target[part]
        node = target
    return node


def _spec(key: str, file: str) -> dict[str, Any]:
    path = SPEC_DIR / file
    text = path.read_text()
    doc = json.loads(text) if file.endswith(".json") else yaml.load(text, Loader=yaml.CSafeLoader)
    if "servers" in doc:
        server = doc["servers"][0]["url"]
    else:
        server = doc.get("basePath", "")
    operations = []
    for spec_path, item in doc.get("paths", {}).items():
        shared = item.get("parameters", [])
        for method in METHODS:
            op = item.get(method)
            if op is None:
                continue
            params = []
            for raw in (*shared, *op.get("parameters", [])):
                param = _resolve(doc, raw)
                if param.get("in") == "body":  # swagger 2 request body
                    continue
                params.append(
                    {
                        "name": param.get("name"),
                        "in": param.get("in"),
                        "required": bool(param.get("required")),
                        "description": " ".join((param.get("description") or "").split()),
                    }
                )
            request = None
            if "requestBody" in op:
                request = _content_schema(_resolve(doc, op["requestBody"]))
            for raw in op.get("parameters", []):
                param = _resolve(doc, raw)
                if param.get("in") == "body":
                    request = _ref_name(param.get("schema"))
            responses = {
                str(status): _content_schema(_resolve(doc, response)) if response else None
                for status, response in op.get("responses", {}).items()
            }
            operations.append(
                {
                    "method": method.upper(),
                    "path": spec_path,
                    "operationId": op.get("operationId"),
                    "summary": " ".join((op.get("summary") or "").split()),
                    "params": params,
                    "request": request,
                    "responses": responses,
                }
            )
    info = doc.get("info", {})
    return {
        "key": key,
        "file": f"spec/{file}",
        "title": info.get("title"),
        "version": info.get("version"),
        "server": server,
        "operations": sorted(operations, key=lambda op: (op["path"], op["method"])),
    }


def openapi_operations() -> list[dict[str, Any]]:
    from app.main import app

    out = []
    for path, ops in app.openapi()["paths"].items():
        for method, op in ops.items():
            out.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "name": op.get("operationId", ""),
                    "params": [
                        {"name": p["name"], "in": p["in"], "required": p.get("required", False)}
                        for p in op.get("parameters", [])
                    ],
                }
            )
    return sorted(out, key=lambda op: (op["path"], op["method"]))


def list_filters(operations: list[dict[str, Any]]) -> dict[str, list[str]]:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.errors import install
    from app.main import app
    from app.routes import routes

    probe = FastAPI()
    install(probe)
    for route, _ in routes(app):
        if "GET" in getattr(route, "methods", set()) and route.path.startswith("/tmf-api/"):
            probe.add_api_route(route.path, route.endpoint, methods=["GET"])
    client = TestClient(probe, raise_server_exceptions=True)
    out = {}
    for op in operations:
        if op["method"] != "GET" or "{" in op["path"] or not op["path"].startswith("/tmf-api/"):
            continue
        response = client.get(op["path"], params={PROBE: "1"})
        message = response.json().get("message", "") if response.status_code == 400 else ""
        marker = f"unknown query parameter {PROBE} (filters: "
        if message.startswith(marker) and message.endswith(")"):
            listed = message[len(marker) : -1]
            out[op["path"]] = [] if listed == "none" else listed.split(", ")
    return out


CATALOG_TABLES = """
    SELECT c.oid, c.relname AS name, obj_description(c.oid, 'pg_class') AS comment
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r'
    ORDER BY c.relname
"""
CATALOG_COLUMNS = """
    SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type,
           a.attnotnull AS not_null, pg_get_expr(d.adbin, d.adrelid) AS default,
           col_description(a.attrelid, a.attnum) AS comment,
           t.typtype = 'e' AS is_enum, t.typname AS type_name
    FROM pg_attribute a
    JOIN pg_type t ON t.oid = a.atttypid
    LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
    WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
    ORDER BY a.attnum
"""
CATALOG_CONSTRAINTS = """
    SELECT conname AS name, contype AS kind, pg_get_constraintdef(oid) AS definition,
           confrelid::regclass::text AS references
    FROM pg_constraint WHERE conrelid = %s
    ORDER BY contype, conname
"""
CATALOG_INDEXES = """
    SELECT i.relname AS name, pg_get_indexdef(i.oid) AS definition,
           EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = i.oid) AS backs_constraint
    FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid
    WHERE x.indrelid = %s
    ORDER BY i.relname
"""
CATALOG_TRIGGERS = """
    SELECT tgname AS name, pg_get_triggerdef(oid) AS definition
    FROM pg_trigger WHERE tgrelid = %s AND NOT tgisinternal ORDER BY tgname
"""
CATALOG_ENUMS = """
    SELECT t.typname AS name, array_agg(e.enumlabel ORDER BY e.enumsortorder) AS labels
    FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname = 'public'
    GROUP BY t.typname ORDER BY t.typname
"""


def _rows(conn: psycopg.Connection, query: str, *args: Any) -> list[dict[str, Any]]:
    cur = conn.execute(query, args or None)
    names = [col.name for col in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def database_catalog() -> dict[str, Any]:
    admin = settings.database_url
    name = scratch_db.name("docs")
    ident = sql.Identifier(name)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(ident))
    url = admin.rsplit("/", 1)[0] + f"/{name}"
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url  # alembic/env.py reads it
    try:
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")
        with psycopg.connect(url) as conn:
            tables = []
            for table in _rows(conn, CATALOG_TABLES):
                oid = table.pop("oid")
                table["columns"] = _rows(conn, CATALOG_COLUMNS, oid)
                table["constraints"] = _rows(conn, CATALOG_CONSTRAINTS, oid)
                table["indexes"] = _rows(conn, CATALOG_INDEXES, oid)
                table["triggers"] = _rows(conn, CATALOG_TRIGGERS, oid)
                tables.append(table)
            enums = _rows(conn, CATALOG_ENUMS)
        return {
            "head": ScriptDirectory.from_config(cfg).get_current_head(),
            "tables": tables,
            "enums": enums,
        }
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(ident))


def main() -> int:
    with contextlib.redirect_stdout(sys.stderr):  # alembic and logging must not reach stdout
        from app.api.account import EXTENSION_SCHEMA

        operations = openapi_operations()
        result = {
            "operations": operations,
            "filters": list_filters(operations),
            "specs": [_spec(key, file) for key, file in SPEC_FILES.items()],
            "extension": EXTENSION_SCHEMA,
            "db": database_catalog(),
        }
    json.dump(result, sys.stdout, indent=1, sort_keys=True, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
