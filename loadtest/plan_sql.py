"""SQL text utilities for the plan sweep; no database or application imports."""

import re

# Preserve quoted identifiers, strings, comments and bind markers as whole tokens.
TOKEN = re.compile(
    r"--[^\n]*|/\*[\s\S]*?\*/|(?:E)?'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\""
    r"|\$[a-zA-Z_]*\$[\s\S]*?\$[a-zA-Z_]*\$|\$\d+|%\([^)]+\)[sbt]|%[sbt]"
    r"|\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b|[a-zA-Z_][\w$]*|::|<=|>=|<>|!=|[^\s]"
)


def tokens(query: str) -> list[str]:
    return [t for t in TOKEN.findall(query) if not t.startswith(("--", "/*"))]


def fingerprint(query: str) -> str:
    # PostgreSQL can retain one representative of these equivalent surface forms.
    query = re.sub(
        r'\(\s*((?:"?\w+"?\.)?"?\w+"?\s+IS\s+(?:NOT\s+)?NULL)\s*\)',
        r"\1",
        query,
        flags=re.I,
    )
    out = []
    parameters = {}
    number = 0
    for t in tokens(query):
        if re.fullmatch(r"\$\d+", t):
            if t not in parameters:
                number += 1
                parameters[t] = number
            out.append(f"?{parameters[t]}")
        elif t.startswith(("'", "E'", "$", "%")) or t[0].isdigit():
            number += 1
            out.append(f"?{number}")
        elif t.lower() in {"true", "false", "null"}:
            number += 1
            out.append(f"?{number}")
        else:
            out.append(t.strip('"').lower())
    return " ".join(out).rstrip(" ;")


def server_query(query: str, params) -> tuple[str, list]:
    """Convert psycopg placeholders without interpreting markers inside SQL strings."""
    values, names, at, parts = [], {}, 0, []
    for match in TOKEN.finditer(query):
        t = match.group()
        if not t.startswith("%") or t == "%":
            continue
        parts.append(query[at : match.start()])
        if t.startswith("%("):
            name = t[2:-2]
            if name not in names:
                names[name] = len(values) + 1
                values.append(params[name])
            index = names[name]
        else:
            values.append(params[len(values)])
            index = len(values)
        parts.append(f"${index}")
        at = match.end()
    parts.append(query[at:])
    return "".join(parts), values


def explainable(query: str) -> bool:
    first = tokens(query)
    return bool(first) and first[0].upper() in {"SELECT", "WITH", "INSERT", "UPDATE", "DELETE"}


def bind_literals(query: str, values: list[str]) -> str:
    """Substitute already adapted SQL literals, respecting quoted strings/comments."""
    return TOKEN.sub(
        lambda m: (
            values[int(m.group()[1:]) - 1] if re.fullmatch(r"\$\d+", m.group()) else m.group()
        ),
        query,
    )
