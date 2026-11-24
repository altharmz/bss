"""UI vs MCP parity: which (method, path template) pairs the admin UI calls.

A text scan of frontend/src (no tests, no generated code), not a JS parser. For each
call of getJson / getAll / getPage / postJson / patchJson / request('METHOD', ...) it
resolves the path argument through the file's string constants (and exported ones from
the other files), expands `X[kind]` over every value of the map X, and turns each
remaining `${...}` into `{}`. A call whose path cannot be resolved is returned in
`unresolved` and the test fails on it, so a new indirection cannot hide a call.
Used by tests/internal/test_ui_mcp_parity.py (docs/decisions/tracking.md).
"""

import itertools
import re
from pathlib import Path

CALLS = {
    "getJson": "GET",
    "getAll": "GET",
    "getPage": "GET",
    "postJson": "POST",
    "patchJson": "PATCH",
}
CALL = re.compile(r"\b(getJson|getAll|getPage|postJson|patchJson|request)\s*(?:<[^()]*?>)?\(")
CONST = re.compile(r"^(?:export\s+)?const\s+(\w+)(?:\s*:[^=]+)?\s*=\s*", re.M)
FUNCTION = re.compile(r"function\s+(\w+)\([^)]*\)[^{;]*\{\s*return\s+([^;]+);\s*\}")
LOCAL = r"^[ \t]*const\s+{}\s*=\s*([^;]+);"
MARKER = re.compile(r"//\s*parity:\s*(GET|POST|PATCH|PUT|DELETE)\s+(\S+)")
# the generic helpers every call goes through: their `request(path)` is not a call site
HELPERS = {"client.ts"}
STRING = re.compile(r"'([^'\\\n]*)'|\"([^\"\\\n]*)\"|`([^`\\]*)`")
INTERP = re.compile(r"\$\{([^{}]*)\}")
PATHISH = re.compile(r"^/(tmf-api|internal)/")


def source_files(root: Path) -> list[Path]:
    return sorted(
        p
        for p in [*root.rglob("*.ts"), *root.rglob("*.tsx")]
        if "generated" not in p.parts and "test" not in p.parts and ".test." not in p.name
    )


def _balanced(text: str, start: int, opener: str, closer: str) -> int:
    """Index just past the closer matching text[start] == opener (strings skipped)."""
    depth, i = 0, start
    while i < len(text):
        c = text[i]
        if c in "'\"`":
            end = i + 1
            while end < len(text) and text[end] != c:
                end += 2 if text[end] == "\\" else 1
            i = end
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _split_top(text: str) -> list[str]:
    parts, depth, last, i = [], 0, 0, 0
    while i < len(text):
        c = text[i]
        if c in "'\"`":
            end = i + 1
            while end < len(text) and text[end] != c:
                end += 2 if text[end] == "\\" else 1
            i = end
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(text[last:i])
            last = i + 1
        i += 1
    parts.append(text[last:])
    return [p.strip() for p in parts if p.strip()]


def constants(text: str, base: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """name -> the strings it can hold. A map object holds all its values under both
    `name` and `name.key`."""
    found: dict[str, list[str]] = dict(base or {})
    for m in CONST.finditer(text):
        name, i = m.group(1), m.end()
        if i >= len(text):
            continue
        if text[i] in "'\"`" and _string_end(text, i) <= len(text):
            found[name] = resolve(text[i : _string_end(text, i)], found)
        elif text[i] == "{":
            body = text[i + 1 : _balanced(text, i, "{", "}") - 1]
            values: list[str] = []
            for entry in _split_top(body):
                key, _, value = entry.partition(":")
                if value.strip()[:1] in ("'", '"', "`") or value.strip() in found:
                    got = resolve(value.strip(), found)
                    found[f"{name}.{key.strip()}"] = got
                    values += got
            found[name] = values
    for m in FUNCTION.finditer(text):  # a helper that only returns a path
        found[m.group(1) + "()"] = resolve(m.group(2), found)
    return found


def _string_end(text: str, i: int) -> int:
    q, end = text[i], i + 1
    while end < len(text) and text[end] != q:
        end += 2 if text[end] == "\\" else 1
    return end + 1


def _top_split(expr: str, sep: str) -> list[str] | None:
    """expr cut at its first top-level `sep` (outside strings and brackets), or None."""
    depth, i = 0, 0
    while i < len(expr):
        c = expr[i]
        if c in "'\"`":
            i = _string_end(expr, i) - 1
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == sep and depth == 0:
            if sep == "?" and expr[i + 1 : i + 2] in (".", "?"):
                i += 1  # optional chaining, not a ternary
            else:
                return [expr[:i], expr[i + 1 :]]
        i += 1
    return None


def _expr(expr: str, known: dict[str, list[str]]) -> list[str]:
    expr = expr.strip()
    if re.fullmatch(r"[\w.]+", expr) and expr in known:
        return known[expr]
    m = re.fullmatch(r"(\w+)\[.*\]", expr, re.S)
    if m and m.group(1) in known:
        return known[m.group(1)]
    m = re.fullmatch(r"(\w+)\(.*\)", expr, re.S)
    if m and m.group(1) + "()" in known:
        return known[m.group(1) + "()"]
    return ["{}"]


def resolve(expr: str, known: dict[str, list[str]]) -> list[str]:
    """Every path an argument expression can denote ([] when it names none)."""
    expr = expr.strip()
    branches = _top_split(expr, "?")
    if branches and ":" in branches[1]:
        yes, _, no = branches[1].partition(":")
        return resolve(yes, known) + resolve(no, known)
    pieces = _top_split(expr, "+")
    if pieces and len(pieces) > 1:
        parts = [resolve(x, known) or ["{}"] for x in pieces]
        return ["".join(c) for c in itertools.product(*parts)]
    if expr[:1] in ("'", '"', "`") and _string_end(expr, 0) == len(expr):
        body = expr[1:-1]
        slots = [_expr(s, known) for s in INTERP.findall(body)]
        pieces = re.split(r"\$\{[^{}]*\}", body)
        out = []
        for combo in itertools.product(*slots):
            tail = [*combo, ""]
            out.append("".join(p + tail[i] for i, p in enumerate(pieces)))
        return out
    if re.fullmatch(r"[\w.]+|\w+\[.*\]", expr):
        got = _expr(expr, known)
        return [] if got == ["{}"] else got
    # a helper call (query(PAYMENTS, filter), partyPath(kind)): the first path-like argument
    m = re.fullmatch(r"(\w+)\((.*)\)", expr, re.S)
    if m:
        for arg in _split_top(m.group(2)):
            got = resolve(arg, known)
            if got:
                return got
    return []


def _clean(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path.split("?")[0]).rstrip("/")


def _local(text: str, before: int, name: str, known: dict[str, list[str]]) -> list[str]:
    """The paths of the nearest `const name = ...;` above the call (a variable local to
    its function), or of the `return` expressions of a function called `name`."""
    found = list(re.finditer(LOCAL.format(re.escape(name)), text[:before], re.M))
    if found:
        return resolve(found[-1].group(1), known)
    m = re.search(rf"function\s+{re.escape(name)}\s*\(", text)
    if m:
        start = text.index("{", _balanced(text, text.index("(", m.start()), "(", ")"))
        body = text[start : _balanced(text, start, "{", "}")]
        return [p for r in re.findall(r"return\s+([^;]+);", body) for p in resolve(r, known)]
    return []


def extract(root: Path) -> tuple[set[tuple[str, str]], list[str]]:
    """({(METHOD, path template)}, [unresolved calls as 'file: snippet']).

    A line `// parity: METHOD <path expression>` adds a call the scan cannot see (a path
    taken from a variable at run time) and acknowledges an unresolvable call within the
    six lines below it."""
    files = {p: p.read_text() for p in source_files(root)}
    shared: dict[str, list[str]] = {}
    for _ in range(2):  # twice: an exported map may name another file's constants
        for text in files.values():
            for name, values in constants(text, shared).items():
                if re.search(rf"export\s+const\s+{re.escape(name.split('.')[0])}\b", text):
                    if len(values) > len(shared.get(name, [])):
                        shared[name] = values
    calls: set[tuple[str, str]] = set()
    unresolved: list[str] = []
    for path, text in files.items():
        known = constants(text, shared)
        known["api.base"] = re.findall(r"\bbase:\s*'([^']+)'", text)
        marked: list[int] = []
        for m in MARKER.finditer(text):
            line = text.count("\n", 0, m.start())
            marked += range(line + 1, line + 7)
            calls |= {(m.group(1), _clean(p)) for p in resolve("`" + m.group(2) + "`", known)}
        if path.name in HELPERS:
            continue
        for m in CALL.finditer(text):
            if text[m.start() - 1 : m.start()] == ".":
                continue  # a method of some other object
            if re.search(r"\b(?:function|async)\s+$", text[max(0, m.start() - 12) : m.start()]):
                continue  # the definition, not a call
            end = _balanced(text, m.end() - 1, "(", ")")
            args = _split_top(text[m.end() : end - 1])
            if not args:
                continue
            method = CALLS.get(m.group(1))
            if m.group(1) == "request":
                lit = re.fullmatch(r"'(GET|POST|PATCH|PUT|DELETE)'", args[0])
                if not lit or len(args) < 2:
                    continue
                method, target = lit.group(1), args[1]
            else:
                target = args[0]
            paths = resolve(target, known)
            if not paths and re.fullmatch(r"\w+", target):
                paths = _local(text, m.start(), target, known)
            if not paths:  # a helper call or template around a local: look inside it
                inner = re.fullmatch(r"(\w+)\(.*\)", target, re.S)
                if inner:
                    paths = _local(text, m.start(), inner.group(1), known)
            found = [_clean(p) for p in paths if PATHISH.match(_clean(p))]
            if not found and text.count("\n", 0, m.start()) not in marked:
                unresolved.append(f"{path.name}: {m.group(1)}({target[:60]})")
            calls |= {(method, p) for p in found if method}
    return calls, unresolved


def matches(op_path: str, ui_path: str) -> bool:
    a, b = _clean(op_path).split("/"), ui_path.split("/")
    return len(a) == len(b) and all(x == y or "{}" in (x, y) for x, y in zip(a, b, strict=True))


def uncalled(operations: dict[str, tuple[str, str]], calls: set[tuple[str, str]]) -> list[str]:
    """Operation names (operationId -> (METHOD, path)) no UI call matches."""
    return sorted(
        name
        for name, (method, path) in operations.items()
        if not any(m == method and matches(path, p) for m, p in calls)
    )
