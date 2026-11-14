"""Bind suite SQL to the 100k dataset and measure under rollback (#327).

No query is modified to suppress constraints. Failed bindings/plans remain unbound.
Each successful sample includes its full JSON plan and binding provenance, not secrets.
"""

# Query text comes from the local application's recorded SQL; identifiers from pg_catalog.
# ruff: noqa: S608
import argparse
import ast
import json
import re
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from loadtest.plan_sql import bind_literals, tokens

PROFILES = ("typical", "largest", "prepaid", "postpaid")
KEYWORDS = set(
    "where join left right inner outer on set values returning order group limit offset "
    "for union cross full using as select from having window with into update".split()
)
SENSITIVE = re.compile(r"password|token|secret|credential|pin|encrypted", re.I)
UNSAFE = re.compile(
    r"\b(?:pg_(?:try_)?advisory_(?:lock|unlock)(?:_shared|_all)?|pg_terminate_backend|"
    r"pg_cancel_backend|pg_reload_conf|pg_read_file|pg_write_file|"
    r"dblink|lo_export|setval|nextval)\s*\(",
    re.I,
)


class Unbound(Exception):
    pass


def plain(value):
    if isinstance(value, (date, datetime, UUID, Decimal)):
        return str(value)
    raise TypeError(type(value).__name__)


def tables_and_aliases(query: str, catalog: dict) -> tuple[list[str], dict[str, str]]:
    ts = tokens(query)
    tables, aliases = [], {}
    for i, token in enumerate(ts[:-1]):
        if token.lower() not in {"from", "join", "update", "into"}:
            continue
        table = ts[i + 1].strip('"')
        if table not in catalog:
            continue
        if table not in tables:
            tables.append(table)
        aliases[table] = table
        j = i + 2
        if j < len(ts) and ts[j].lower() == "as":
            j += 1
        if j < len(ts) and re.fullmatch(r"[a-zA-Z_]\w*", ts[j]):
            if ts[j].lower() not in KEYWORDS:
                aliases[ts[j]] = table
    return tables, aliases


def parameter_columns(query: str, catalog: dict) -> dict[int, tuple[str, str]]:
    """Conservative syntactic associations; unknown expressions fall back to type/examples."""
    tables, aliases = tables_and_aliases(query, catalog)
    ts = tokens(query)
    out = {}
    levels, depth = [], 0
    for part in ts:
        if part == ")":
            depth -= 1
        levels.append(depth)
        if part == "(":
            depth += 1
    target = None
    if ts and ts[0].upper() == "UPDATE":
        target = ts[1].strip('"')
    elif len(ts) > 2 and ts[0].upper() == "DELETE" and ts[1].upper() == "FROM":
        target = ts[2].strip('"')
    # INSERT ... (columns) VALUES (expressions), including cast/function expressions.
    match = re.search(r"\bINSERT\s+INTO\s+\"?(\w+)\"?\s*\(([^)]+)\)\s*VALUES\s*\(", query, re.I)
    if match and match[1] in catalog:
        columns = [c.strip().strip('"') for c in match[2].split(",")]
        depth, column = 0, 0
        for t in tokens(query[match.end() :]):
            if t == ")" and depth == 0:
                break
            if t == "," and depth == 0:
                column += 1
            elif t == "(":
                depth += 1
            elif t == ")":
                depth -= 1
            elif re.fullmatch(r"\$\d+", t) and column < len(columns):
                out[int(t[1:])] = (match[1], columns[column])
    for i, t in enumerate(ts):
        if not re.fullmatch(r"\$\d+", t) or int(t[1:]) in out:
            continue
        # nearest column before an operator, allowing ::type and ANY(...).
        before = ts[max(0, i - 9) : i]
        for j in range(len(before) - 1, -1, -1):
            c = before[j].strip('"')
            if j and before[j - 1] == "::":
                continue
            if j >= 2 and before[j - 1] == ".":
                table = aliases.get(before[j - 2].strip('"'))
                if table and c in catalog[table]:
                    out[int(t[1:])] = (table, c)
                    break
            candidates = [table for table in tables if c in catalog[table]]
            if len(candidates) > 1 and levels[i] == 0 and target in candidates:
                out[int(t[1:])] = (target, c)
                break
            if len(candidates) == 1:
                out[int(t[1:])] = (candidates[0], c)
                break
    return out


def walk(plan: dict, partial=False):
    """Track consumers that can stop a child before its estimated cardinality is read."""
    yield plan, partial
    child_partial = partial
    if plan["Node Type"] == "Limit" and (
        not plan.get("Plan Rows") or plan.get("Actual Rows", 0) >= plan["Plan Rows"]
    ):
        child_partial = True
    # These nodes consume their whole input even if their own output is limited.
    if plan["Node Type"] in {"Sort", "Hash"} or (
        plan["Node Type"] == "Aggregate" and plan.get("Strategy") in {"Plain", "Hashed"}
    ):
        child_partial = False
    for child in plan.get("Plans", []):
        early = child_partial
        if (
            plan.get("Join Type") in {"Semi", "Anti"}
            and child.get("Parent Relationship") == "Inner"
            and child.get("Actual Rows", 0)
        ):
            early = True
        if (
            child.get("Parent Relationship") in {"InitPlan", "SubPlan"}
            and child.get("Actual Rows") == 1
        ):
            early = True  # scalar/EXISTS consumers; blocking child nodes reset their inputs
        yield from walk(child, early)


def inspect_plan(document: dict, table_rows: dict, limit: float) -> dict:
    root = document["Plan"]
    flags, nodes = [], []
    for node, partial in walk(root):
        kind = node["Node Type"]
        relation = node.get("Relation Name")
        estimated = node.get("Plan Rows", 0)
        actual = node.get("Actual Rows", 0)
        ratio = max(estimated, actual) / max(min(estimated, actual), 1)
        excerpt = {
            key: node[key]
            for key in (
                "Node Type",
                "Relation Name",
                "Index Name",
                "Plan Rows",
                "Actual Rows",
                "Actual Loops",
                "Actual Total Time",
                "Rows Removed by Filter",
                "Shared Read Blocks",
                "Shared Hit Blocks",
                "Filter",
                "Index Cond",
                "Join Filter",
                "Hash Cond",
                "Sort Key",
                "Operation",
                "Conflict Resolution",
                "Tuples Inserted",
                "Conflicting Tuples",
            )
            if key in node
        }
        nodes.append(excerpt)
        excerpt["Estimate Comparable"] = not partial and node.get("Actual Loops", 1) > 0
        if kind in {"Seq Scan", "Parallel Seq Scan"} and table_rows.get(relation, 0) > 10000:
            flags.append(f"sequential scan: {relation} ({table_rows[relation]} rows)")
        if ratio > 100 and excerpt["Estimate Comparable"]:
            flags.append(f"estimate error {ratio:.1f}x: {kind} {relation or ''}")
    elapsed = document["Execution Time"]
    if elapsed > limit:
        flags.append(f"execution {elapsed:.3f} ms > {limit:g} ms")
    return {
        "execution_ms": elapsed,
        "planning_ms": document.get("Planning Time"),
        "shared_read_blocks": root.get("Shared Read Blocks", 0),
        "shared_hit_blocks": root.get("Shared Hit Blocks", 0),
        "nodes": nodes,
        "flags": flags,
    }


def known_issue(query: str, locations: list[str]) -> str:
    q = query.lower()
    loc = " ".join(locations)
    if (
        "funded_interval" in q
        and ("::text" in q or "distinct on" in q)
        or "::text" in q
        and any(s in loc for s in ("balance_actions", "numbers"))
        or "filter" in q
        and any(s in q for s in ("prepaid_command", "notification_message"))
        or "group by status" in q
        and "product" in q
        or "metrics.py" in loc
        and "from product" in q
        and "group by" in q
    ):
        return "known (#311)"
    if (
        "count_references" in loc
        or "_sql.py" in loc
        and "count(*)" in q
        or "applied_customer_billing_rate" in q
        and "product_id" in q
        or "auto_topup_enabled" in q
        or "grantless" in q
        or "previous_interval_id" in q
        and "not exists" in q
        or "order by" in q
        and any(
            s in q
            for s in (
                "prepaid_bucket",
                "prepaid_command",
                "balance_action",
                "subscriber_number",
                "network_command",
                "payment_command",
                "notification_message",
                "event_delivery",
            )
        )
        or "notify/events.py" in loc
        or "product_order_item" in q
        and "product_offering_id" in q
    ):
        return "known (#310)"
    return "new"


def cadence(locations: list[str]) -> tuple[str, float]:
    # Conservative default: a helper callable per account/request has the 5 ms budget.
    once = {
        "count_members",
        "earliest_failures_before",
        "latest_run_date",
        "unfinished_run",
        "failed_account_runs",
        "held_back_by",
        "scheduled",
        "housekeep",
        "run_totals",
        "_column_types",
        "_enum_labels",
        "run_members",
        "outcome_gaps",
        "outcomes",
        "announce_changes",
        "flip_statuses",
        "apply_status_changes",
        "process_lifecycle",
        "_entitlement_accounts",
    }
    for loc in locations:
        path, line = loc.rsplit(":", 1)
        try:
            tree = ast.parse(Path(path).read_text())
        except (OSError, SyntaxError):
            return "per request/account (conservative default)", 5.0
        enclosing = [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.lineno <= int(line) <= n.end_lineno
        ]
        name = min(enclosing, key=lambda n: n.end_lineno - n.lineno).name if enclosing else ""
        is_once = (
            name in once
            or "housekeeping.py" in path
            or "repository/account_review.py" in path
            or "notify/events.py" in path
            and name
            in {
                "_account_ids",
                "_plan_expiring",
                "_renewals",
                "_lifecycle",
                "_usage",
                "_card_declines",
                "_ports",
                "_sim_swaps",
                "_security",
            }
        )
        if not is_once:
            return "per request/account (conservative default)", 5.0
    return (
        ("once per tick/run", 100.0)
        if locations
        else ("per request/account (conservative default)", 5.0)
    )


class Catalog:
    def __init__(self, conn):
        self.conn = conn
        dates = conn.execute(
            "SELECT min(run_date) AS first_day, max(run_date) AS last_day"
            " FROM billing_run WHERE kind='daily'"
        ).fetchone()
        if not dates["last_day"]:
            raise Unbound("dataset has no daily billing runs")
        self.first_day, self.day = dates["first_day"], dates["last_day"]
        self.columns = {}
        for row in conn.execute(
            "SELECT c.table_name, c.column_name, c.udt_name FROM information_schema.columns c"
            " JOIN pg_class p ON p.relname=c.table_name JOIN pg_namespace n ON n.oid=p.relnamespace"
            " WHERE c.table_schema='public' AND n.nspname='public' AND p.relkind IN ('r','p')"
        ):
            self.columns.setdefault(row["table_name"], {})[row["column_name"]] = row["udt_name"]
        self.rows = {
            r["relname"]: max(0, int(r["reltuples"]))
            for r in conn.execute(
                "SELECT relname, reltuples FROM pg_class WHERE relnamespace='public'::regnamespace"
            )
        }
        self.enum = {}
        for row in conn.execute(
            "SELECT t.typname,e.enumlabel FROM pg_enum e"
            " JOIN pg_type t ON t.oid=e.enumtypid ORDER BY e.enumsortorder"
        ):
            self.enum.setdefault(row["typname"], []).append(row["enumlabel"])
        self.accounts = {}
        queries = {
            "typical": "SELECT * FROM billing_account ORDER BY id OFFSET 50000 LIMIT 1",
            "largest": "SELECT a.* FROM billing_account a JOIN (SELECT billing_account_id,"
            " count(*) n FROM product GROUP BY 1 ORDER BY n DESC,1 LIMIT 1) p"
            " ON p.billing_account_id=a.id",
            "prepaid": "SELECT * FROM billing_account WHERE rating_type='prepaid'"
            " ORDER BY id LIMIT 1",
            "postpaid": "SELECT * FROM billing_account WHERE rating_type='postpaid'"
            " ORDER BY id LIMIT 1",
        }
        for name, query in queries.items():
            self.accounts[name] = conn.execute(query).fetchone()
            if not self.accounts[name]:
                raise Unbound(f"dataset missing {name} account; restore seed100k")
        self.cache = {}
        self.foreign = {}
        for row in conn.execute(
            "SELECT child.relname AS child, ca.attname AS column, parent.relname AS parent,"
            " pa.attname AS target FROM pg_constraint k"
            " JOIN pg_class child ON child.oid=k.conrelid"
            " JOIN pg_class parent ON parent.oid=k.confrelid"
            " JOIN pg_attribute ca ON ca.attrelid=child.oid AND ca.attnum=k.conkey[1]"
            " JOIN pg_attribute pa ON pa.attrelid=parent.oid AND pa.attnum=k.confkey[1]"
            " WHERE k.contype='f' AND cardinality(k.conkey)=1"
        ):
            self.foreign[row["child"], row["column"]] = row["parent"], row["target"]
        self.unique = set()
        for row in conn.execute(
            "SELECT c.relname,a.attname FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid"
            " JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=i.indkey[0]"
            " WHERE i.indisunique AND i.indnkeyatts=1 AND i.indexprs IS NULL"
        ):
            self.unique.add((row["relname"], row["attname"]))

    def sample(self, table, profile):
        run_kind = "daily"
        match = re.search(r"\bkind\s*=\s*'(daily|prepaidCommand)'", getattr(self, "query", ""))
        if match:
            run_kind = match[1]
        key = table, profile, run_kind
        if key in self.cache:
            return self.cache[key]
        cols = self.columns[table]
        account = self.accounts[profile]
        if table == "billing_account":
            row = account
        elif table == "billing_run":
            row = self.conn.execute(
                "SELECT * FROM billing_run WHERE kind=%s"
                " ORDER BY run_date DESC NULLS LAST, id LIMIT 1",
                (run_kind,),
            ).fetchone()
        else:
            clause, params = sql.SQL(""), []
            if "billing_account_id" in cols:
                clause, params = sql.SQL(" WHERE billing_account_id=%s"), [account["id"]]
            elif "product_id" in cols and table != "product":
                product = self.sample("product", profile)
                if product:
                    clause, params = sql.SQL(" WHERE product_id=%s"), [product["id"]]
            elif table == "customer" and account.get("customer_id"):
                clause, params = sql.SQL(" WHERE id=%s"), [account["customer_id"]]
            else:
                for (child, column), (parent, target) in self.foreign.items():
                    if child == table and parent != table and parent in self.columns:
                        if "billing_account_id" in self.columns[parent]:
                            parent_row = self.sample(parent, profile)
                            if parent_row:
                                clause = sql.SQL(" WHERE {}=%s").format(sql.Identifier(column))
                                params = [parent_row[target]]
                                break
            order = sql.SQL(" ORDER BY id") if "id" in cols else sql.SQL("")
            query = sql.SQL("SELECT * FROM {}{}").format(sql.Identifier(table), clause) + order
            row = self.conn.execute(query + sql.SQL(" LIMIT 1"), params).fetchone()
            if row is None and clause:
                row = self.conn.execute(
                    sql.SQL("SELECT * FROM {} LIMIT 1").format(sql.Identifier(table))
                ).fetchone()
        self.cache[key] = row
        return row

    def literal(self, value):
        return sql.Literal(value).as_string(self.conn)

    def generic(self, typ, example):
        typ = typ.removesuffix("[]") if typ.endswith("[]") else typ
        if isinstance(example, dict):
            if "decimal" in example:
                return Decimal(example["decimal"])
        if typ in self.enum:
            return example if example in self.enum[typ] else self.enum[typ][0]
        if typ in {"uuid"}:
            return self.accounts["typical"]["id"]
        if typ in {"date"}:
            return self.day
        if typ in {
            "timestamp with time zone",
            "timestamp without time zone",
            "timestamptz",
            "timestamp",
        }:
            return datetime.combine(self.day, datetime.min.time(), tzinfo=UTC)
        if typ in {"integer", "bigint", "smallint", "numeric", "double precision", "real"}:
            return example if isinstance(example, (int, float)) else 1
        if typ in {"boolean", "bool"}:
            return example if isinstance(example, bool) else False
        if typ == "interval":
            seconds = example.get("interval_seconds", 86400) if isinstance(example, dict) else 86400
            return timedelta(seconds=seconds)
        if isinstance(example, (str, int, float, bool)) or example is None:
            return example
        return "sweep"

    def compatible(self, value, typ):
        if value is None:
            return True
        if typ == "date":
            return isinstance(value, date)
        if typ in {"timestamp with time zone", "timestamp without time zone"}:
            return isinstance(value, (date, datetime))
        if typ == "uuid":
            try:
                UUID(str(value))
                return True
            except ValueError:
                return False
        if typ in {"integer", "bigint", "smallint", "numeric", "double precision", "real"}:
            return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
        if typ == "boolean":
            return isinstance(value, bool)
        if typ == "interval":
            return isinstance(value, timedelta)
        return True

    def bindings(self, shape, types, profile):
        query = shape["query"]
        self.query = query
        hints = parameter_columns(query, self.columns)
        values, provenance = [], []
        insert = query.lstrip().upper().startswith("INSERT")
        for i, typ in enumerate(types, 1):
            example = shape["examples"][i - 1] if i <= len(shape["examples"]) else None
            hint = hints.get(i)
            value, origin, found = None, "typed example/default", False
            if hint:
                table, column = hint
                row = self.sample(table, profile)
                if row:
                    value, origin = row.get(column), f"{profile}:{table}.{column}"
                    found = column in row
                if not found and hint in self.foreign:
                    parent, target = self.foreign[hint]
                    parent_row = self.sample(parent, profile)
                    if parent_row:
                        value, origin = parent_row[target], f"{profile}:FK {parent}.{target}"
                        found = True
                if (
                    insert
                    and column == "id"
                    and typ == "uuid"
                    and "ON CONFLICT" not in query.upper()
                ):
                    value, origin = uuid4(), "fresh insert primary key"
                    found = True
                if (
                    insert
                    and hint in self.unique
                    and hint not in self.foreign
                    and typ in {"text", "character varying"}
                    and "ON CONFLICT" not in query.upper()
                ):
                    value, origin = f"sweep-{uuid4().hex[:12]}", "fresh unique insert value"
                    found = True
                if SENSITIVE.search(column) and typ in {"text", "character varying", "bytea"}:
                    value, origin = "sweep", "credential redacted"
                    found = True
            if not found:
                value = self.generic(typ, example)
            if (
                hasattr(value, "lower")
                and not isinstance(value, str)
                and typ
                in {
                    "date",
                    "timestamp with time zone",
                    "timestamp without time zone",
                }
            ):
                upper = any(
                    int(m[2]) == i
                    for m in re.finditer(
                        r"(?:date|tstz|ts)range\s*\(\s*\$(\d+)\s*,\s*\$(\d+)", query, re.I
                    )
                )
                value = value.upper if upper else value.lower
                origin += ":range upper" if upper else ":range lower"
                if value is None:
                    value = self.generic(typ, example)
            if not self.compatible(value, typ):
                value, origin = self.generic(typ, example), "typed expression fallback"
                if typ == "uuid":
                    tables, _ = tables_and_aliases(query, self.columns)
                    for table in tables:
                        row = self.sample(table, profile)
                        if row and isinstance(row.get("id"), UUID):
                            value, origin = row["id"], f"{profile}:{table}.id (typed fallback)"
                            break
            # JSON operators take a key, not the surrounding JSON document.
            if (
                isinstance(value, (dict, list))
                and typ not in {"json", "jsonb"}
                and not typ.endswith("[]")
            ):
                value, origin = self.generic(typ, example), "typed expression example"
            if re.search(rf"\bOFFSET\s+\${i}\b", query, re.I):
                value, origin = (5000 if profile == "largest" else 0), "page offset"
            if re.search(rf"\bLIMIT\s+\${i}\b", query, re.I):
                value, origin = 21, "page size plus lookahead"
            if re.search(rf"\bAT\s+TIME\s+ZONE\s+\${i}\b", query, re.I):
                value, origin = "Europe/Moscow", "dataset billing timezone"
            if typ.endswith("[]"):
                if not isinstance(value, list):
                    value = [value] if value is not None else []
                element = typ[:-2]
                literal = "ARRAY[" + ",".join(self.literal(v) for v in value) + f"]::{element}[]"
            elif typ in {"json", "jsonb"}:
                literal = self.literal(json.dumps(value, default=plain)) + f"::{typ}"
            elif typ in {"daterange", "tstzrange", "tsrange"}:
                if hasattr(value, "lower") and not isinstance(value, str):
                    literal = self.literal(value)
                else:
                    end = self.day + timedelta(days=1)
                    literal = self.literal(f"[{self.first_day},{end})") + f"::{typ}"
            else:
                # ::character would introduce the default char(1) typmod and truncate
                # a currency. Its column context supplies the correct width instead.
                literal = self.literal(value) + (f"::{typ}" if typ != "character" else "")
            values.append(literal)
            proxy = typ.removesuffix("[]") == "uuid" and origin == "typed example/default"
            if proxy:
                origin = "UUID proxy: sampled billing_account.id; target unresolved/empty"
            provenance.append({"parameter": i, "type": typ, "source": origin, "uuid_proxy": proxy})
        return bind_literals(query, values), provenance


def measure(conn, cat, shape):
    result = {**shape, "status": "unbound", "samples": [], "errors": []}
    query = shape["query"].strip().rstrip(";")
    origins = shape.get("origins") or shape["locations"]
    result["cadence"], budget = cadence(origins)
    result["budget_ms"] = budget
    result["classification"] = known_issue(query, origins)
    if UNSAFE.search(query):
        result["errors"].append("session/external side-effect function cannot be rolled back")
        return result
    prepared = False
    try:
        conn.execute("PREPARE plan_sweep_statement AS " + query)
        prepared = True
        types = conn.execute(
            "SELECT parameter_types::text[] AS types FROM pg_prepared_statements"
            " WHERE name='plan_sweep_statement'"
        ).fetchone()["types"]
    except psycopg.Error as error:
        result["errors"].append(f"prepare: {error.sqlstate} {error.diag.message_primary}")
        return result
    finally:
        if prepared:
            conn.execute("DEALLOCATE plan_sweep_statement")
    for profile in PROFILES:
        try:
            bound, sources = cat.bindings(shape, types, profile)
            conn.execute("BEGIN")
            conn.execute("SET LOCAL statement_timeout='15s'")
            conn.execute("SET LOCAL lock_timeout='1s'")
            document = conn.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + bound).fetchone()
            document = next(iter(document.values()))[0]
            summary = inspect_plan(document, cat.rows, budget)
            result["samples"].append(
                {"profile": profile, "bindings": sources, **summary, "plan": document}
            )
        except (psycopg.Error, ValueError, TypeError, IndexError, KeyError, Unbound) as error:
            code = getattr(error, "sqlstate", None)
            # Error detail can contain credentials/row data. Record primary message only.
            message = error.diag.message_primary if isinstance(error, psycopg.Error) else str(error)
            result["errors"].append(f"{profile}: {code or type(error).__name__} {message}")
        finally:
            conn.rollback()
    if result["samples"]:
        result["worst_ms"] = max(s["execution_ms"] for s in result["samples"])
        result["flags"] = sorted({f for s in result["samples"] for f in s["flags"]})
        result["status"] = "flagged" if result["flags"] else "measured"
    return result


def markdown(report):
    c = report["coverage"]
    lines = [
        "# SQL plan sweep (#327)",
        "",
        f"Dataset: {report['dataset']}",
        f"Collected {c['collected']}; measured {c['measured']}; unbound {c['unbound']}; "
        f"coverage {c['percent']:.2f}%; flagged {c['flagged']}.",
        "",
        "Writes were rolled back; sequence state restored. Four account profiles; "
        "partial profile failures remain visible. "
        "Plans are warm/repeated, not concurrency results.",
        "",
        "| Shape | Worst ms | Status | Mark | Source |",
        "|---|---:|---|---|---|",
    ]
    ordered = sorted(report["shapes"], key=lambda r: r.get("worst_ms", -1), reverse=True)
    for i, r in enumerate(ordered, 1):
        r["number"] = i
        locations = ", ".join(r["locations"][:3])
        lines.append(
            f"| {i} | {r.get('worst_ms', '')} | {r['status']} | "
            f"{r['classification']} | {locations} |"
        )
    for r in ordered:
        lines += [
            "",
            f"## Shape {r['number']}: {r['status']} ({r['classification']})",
            "",
            f"Source: {', '.join(r['locations'])}",
            f"Tests: {', '.join(r['tests'][:8])}",
            f"Cadence: {r['cadence']}; budget {r['budget_ms']} ms.",
            "",
            "```sql",
            r["query"],
            "```",
        ]
        if r.get("flags"):
            lines += ["", *[f"- {f}" for f in r["flags"]]]
        if r["samples"]:
            worst = max(r["samples"], key=lambda s: s["execution_ms"])
            lines += [
                "",
                f"Worst profile: {worst['profile']}",
                "```json",
                json.dumps(worst["nodes"], indent=1, default=plain),
                "```",
            ]
        if r["errors"]:
            lines += ["", "Binding/profile errors:", *[f"- {e}" for e in r["errors"]]]
    return "\n".join(lines) + "\n"


def restore_sequences(conn, sequences):
    """Restore and verify sequence state; a failure leaves the recovery journal intact."""
    for name, value, called in sequences:
        conn.execute("SELECT setval(%s::regclass,%s,%s)", (name, value, called))
        # name is a fully quoted identifier generated from pg_catalog, not SQL input.
        row = conn.execute(
            sql.SQL("SELECT last_value,is_called FROM {}").format(sql.SQL(name))
        ).fetchone()
        state = (row["last_value"], row["is_called"]) if isinstance(row, dict) else row
        if state != (value, called):
            raise RuntimeError(f"sequence restoration failed: {name}")


def main():
    from app.config import settings

    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    if settings.app_env != "test" or "@lt-db:" not in settings.database_url:
        raise SystemExit("plan sweep requires the isolated APP_ENV=test lt-db")
    collection = json.loads((args.run / "shapes.json").read_text())
    results = []
    started_at = datetime.now(UTC).isoformat()
    started = time.monotonic()
    journal = args.run / "sequence-state.json"
    with psycopg.connect(
        settings.database_url, autocommit=True, row_factory=dict_row, prepare_threshold=None
    ) as conn:
        identity = conn.execute(
            "SELECT (SELECT system_identifier::text FROM pg_control_system()) AS cluster,"
            " oid AS database FROM pg_database WHERE datname=current_database()"
        ).fetchone()
        if journal.exists():
            pending = json.loads(journal.read_text())
            if not isinstance(pending, dict) or pending.get("identity") != identity:
                raise SystemExit(
                    "pending sequence journal belongs to a different database; preserved"
                )
            restore_sequences(conn, pending["sequences"])
            journal.unlink()
        cat = Catalog(conn)
        count = conn.execute("SELECT count(*) AS n FROM billing_account").fetchone()["n"]
        if count < 100000:
            raise SystemExit(f"plan sweep needs seed100k, found {count} accounts")
        sequences = []
        for row in conn.execute(
            "SELECT schemaname,sequencename FROM pg_sequences WHERE schemaname='public'"
        ).fetchall():
            seq = sql.Identifier(row["schemaname"], row["sequencename"])
            state = conn.execute(
                sql.SQL("SELECT last_value,is_called FROM {}").format(seq)
            ).fetchone()
            sequences.append((seq.as_string(conn), state["last_value"], state["is_called"]))
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        for name in ("report.json", "report.md", "progress.jsonl"):
            path = args.run / name
            if path.exists():
                path.rename(path.with_name(f"{path.stem}-{stamp}{path.suffix}"))
        journal.write_text(json.dumps({"identity": identity, "sequences": sequences}))
        try:
            for i, shape in enumerate(collection["shapes"], 1):
                r = measure(conn, cat, shape)
                results.append(r)
                with (args.run / "progress.jsonl").open("a") as checkpoint:
                    checkpoint.write(json.dumps(r, default=plain) + "\n")
                if i % 25 == 0:
                    print(
                        f"{i}/{len(collection['shapes'])}: {Counter(r['status'] for r in results)}",
                        flush=True,
                    )
        finally:
            try:
                conn.rollback()
            except psycopg.Error:
                conn.close()
            with psycopg.connect(settings.database_url, autocommit=True) as cleanup:
                restore_sequences(cleanup, sequences)
            journal.unlink()
        states = Counter(r["status"] for r in results)
        measured = states["measured"] + states["flagged"]
        report = {
            "dataset": f"seed100k: {count} accounts; PostgreSQL {conn.info.server_version}",
            "started": started_at,
            "seconds": time.monotonic() - started,
            "coverage": {
                "collected": len(results),
                "measured": measured,
                "unbound": states["unbound"],
                "flagged": states["flagged"],
                "percent": 100 * measured / max(len(results), 1),
                "all_four_profiles": sum(len(r["samples"]) == 4 for r in results),
                "partial_profiles": sum(0 < len(r["samples"]) < 4 for r in results),
                "uuid_proxy_shapes": sum(
                    any(b.get("uuid_proxy") for s in r["samples"] for b in s["bindings"])
                    for r in results
                ),
            },
            "sequence_restoration_verified": True,
            "accounts_before_after": [
                count,
                conn.execute("SELECT count(*) AS n FROM billing_account").fetchone()["n"],
            ],
            "billing_dates": [str(cat.first_day), str(cat.day)],
            "relation_rows_estimated": {t: cat.rows.get(t, 0) for t in cat.columns},
            "server_settings": conn.execute(
                "SELECT current_setting('shared_buffers') AS shared_buffers,"
                " current_setting('work_mem') AS work_mem, current_setting('jit') AS jit"
            ).fetchone(),
            "collection": {k: v for k, v in collection.items() if k != "shapes"},
            "profiles": {p: str(a["id"]) for p, a in cat.accounts.items()},
            "shapes": results,
        }
    text = markdown(report)
    (args.run / "report.json").write_text(json.dumps(report, indent=1, default=plain))
    (args.run / "report.md").write_text(text)
    print(json.dumps(report["coverage"]))
    if report["coverage"]["percent"] < 90 or collection["deallocations"]:
        raise SystemExit("plan sweep incomplete: coverage <90% or collection evicted statements")
    if report["accounts_before_after"][0] != report["accounts_before_after"][1]:
        raise SystemExit("plan sweep changed the account count; inspect the failed report")


if __name__ == "__main__":
    main()
