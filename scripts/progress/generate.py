#!/usr/bin/env python3
"""Generate .progress/PROGRESS.md and .progress/status.json (make progress; not committed).

Inputs (the collectors' JSON, written by `make progress` into .progress/):
  backend.json  tests/progress_collect.py: scope.yaml parsed, app.openapi() operations,
                tables at migration head, and from one run of the test suite: the outcome
                of the tests marked @pytest.mark.capability, and what the passing tests
                exercised (operations requested, tables written)
  ui.json       frontend/scripts/ui-markers.mjs: data-ui markers in frontend/src
  ui-tested.json  frontend/scripts/ui-tested-reporter.mjs: the data-ui ids that passing
                vitest tests saw on screen through expectUi (frontend/src/test/expectUi.ts)

Status is derived, never written by hand:
  implemented   db: the table exists at head; api: app.openapi() has the operation;
                ui: a literal data-ui marker with the item's id exists in the source, or a
                passing test saw it on screen (markers built at run time are invisible to
                the static collector); worker: see tested
  tested        worker: at least one test marked with the capability passed and none
                failed (implemented and tested together - a capability is proven, not
                detected); ui: a passing component test saw the marker on screen
                (expectUi); db: a passing test's database wrote rows (inserted or updated)
                (pg_stat_user_tables); api: a passing test sent a request the app routes
                to the operation

Exits 1 (after writing the outputs) when the scope is invalid, a test is marked with a
capability that scope.yaml does not list, a test calls expectUi with an id that is not
a ui item (nor excluded), or the code has unscoped items: tables,
operations or markers that are neither planned nor excluded. Standard
library only: it runs on the host, like scripts/spec-verify-upstream.py. The output is
deterministic (no timestamps, no line numbers) for repeatable local reports.
"""

import argparse
import json
import sys
from pathlib import Path

AREAS = ("db", "api", "ui", "worker")
STAGES = {"template", 1, 2, 3, 4, 5}
AREA_TITLES = {"db": "Database", "api": "API", "ui": "UI", "worker": "Worker"}


def key_of(item: dict) -> tuple:
    """What the code is matched on: the table, the operation, or the id itself (the ui
    marker, the worker capability)."""
    area = item["area"]
    if area == "db":
        return ("db", item["table"])
    if area == "api":
        return ("api", item["method"].upper(), item["path"])
    return (area, item["id"])


def validate(scope: dict) -> list[str]:
    errors = []
    seen: dict[str, str] = {}
    keys: dict[tuple, str] = {}
    for section, required in (
        ("items", {"id", "area", "stage", "title"}),
        ("excluded", {"id", "area", "reason"}),
    ):
        for entry in scope.get(section) or []:
            ident = entry.get("id", "?")
            missing = required - entry.keys()
            if missing:
                errors.append(f"{section} {ident}: missing {', '.join(sorted(missing))}")
                continue
            area = entry["area"]
            if area not in AREAS:
                errors.append(f"{ident}: unknown area {area!r} (one of {', '.join(AREAS)})")
                continue
            if not ident.startswith(f"{area}."):
                errors.append(f"{ident}: the id must start with '{area}.'")
            if section == "items" and entry["stage"] not in STAGES:
                errors.append(f"{ident}: unknown stage {entry['stage']!r}")
            if area == "db" and "table" not in entry:
                errors.append(f"{ident}: a db entry needs 'table'")
                continue
            if area == "api" and not {"method", "path"} <= entry.keys():
                errors.append(f"{ident}: an api entry needs 'method' and 'path'")
                continue
            if ident in seen:
                errors.append(f"{ident}: duplicate id (also in {seen[ident]})")
            seen[ident] = section
            key = key_of(entry)
            if key in keys:
                errors.append(f"{ident}: same table/operation/marker as {keys[key]}")
            keys[key] = ident
    return errors


def build(backend: dict, ui_markers: list[dict], ui_tested: list[str]) -> tuple[dict, list[str]]:
    scope = backend["scope"]
    errors = validate(scope)
    present = {("db", t) for t in backend["tables"]}
    present |= {("api", op["method"], op["path"]) for op in backend["openapi"]}
    present |= {("ui", m["marker"]) for m in ui_markers}
    capabilities = backend["capabilities"]
    proven = set(capabilities["passed"]) - set(capabilities["failed"])
    exercised = {("db", t) for t in backend["tested"]["tables"]}
    exercised |= {("api", op["method"], op["path"]) for op in backend["tested"]["api"]}

    known = {
        key_of(e)
        for e in (scope.get("items") or []) + (scope.get("excluded") or [])
        if e.get("area") in AREAS
    }
    for capability in sorted(set(capabilities["seen"])):
        if ("worker", capability) not in known:
            errors.append(
                f"tests are marked with capability {capability!r}, which is not a worker item"
            )
    for marker in sorted(set(ui_tested)):
        if ("ui", marker) not in known:
            errors.append(f"tests call expectUi({marker!r}), which is not a ui item")

    items = []
    for item in scope.get("items") or []:
        if item.get("area") not in AREAS:
            continue
        worker = item["area"] == "worker"
        if worker:
            done = item["id"] in proven
        elif item["area"] == "ui":
            # A marker built at run time (template literal, prop, lookup table) is invisible
            # to the static collector; a passing test that saw it on screen proves it exists.
            done = key_of(item) in present or item["id"] in ui_tested
        else:
            done = key_of(item) in present
        if worker:
            tested = done
        elif item["area"] == "ui":
            tested = item["id"] in ui_tested
        else:
            tested = key_of(item) in exercised
        items.append(
            {
                "id": item["id"],
                "area": item["area"],
                "stage": item["stage"],
                "title": item["title"],
                "implemented": done,
                "tested": tested,
            }
        )

    unscoped = []
    for key in sorted(present - known):
        if key[0] == "api":
            unscoped.append({"area": "api", "what": f"{key[1]} {key[2]}"})
        elif key[0] == "db":
            unscoped.append({"area": "db", "what": f"table {key[1]}"})
        else:
            unscoped.append({"area": "ui", "what": f'data-ui="{key[1]}"'})

    areas = {}
    for area in AREAS:
        mine = [i for i in items if i["area"] == area]
        areas[area] = {
            "items": len(mine),
            "implemented": sum(i["implemented"] for i in mine),
            "tested": sum(i["tested"] for i in mine),
        }
    excluded = [
        {"id": e["id"], "area": e["area"], "reason": e["reason"]}
        for e in scope.get("excluded") or []
        if e.get("area") in AREAS
    ]
    status = {"areas": areas, "items": items, "excluded": excluded, "unscoped": unscoped}
    return status, errors


def percent(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part * 100 // whole}%)" if whole else "0/0"


def render(status: dict) -> str:
    out = [
        "# Implementation progress",
        "",
        "Generated by `make progress` from `docs/progress/scope.yaml` and the code - do not",
        "edit by hand. Implemented: the table exists at migration head (db), `app.openapi()`",
        "has the operation (api), a literal `data-ui` marker exists in `frontend/src` or a passing",
        "component test saw it on screen (ui). Worker:",
        "implemented and tested when a test marked with the capability passes and none fails.",
        "Tested (ui): a passing component test saw the marker on screen (`expectUi`). Tested",
        "(db): a passing test's database inserted or updated rows in the table. Tested (api): a",
        "passing test sent a request the app routes to the operation.",
        "",
        "| Area | Items | Implemented | Tested |",
        "|---|---|---|---|",
    ]
    for area in AREAS:
        a = status["areas"][area]
        tested = "n/a" if a["tested"] is None else percent(a["tested"], a["items"])
        out.append(
            f"| {AREA_TITLES[area]} | {a['items']} | "
            f"{percent(a['implemented'], a['items'])} | {tested} |"
        )
    for area in AREAS:
        out += [
            "",
            f"## {AREA_TITLES[area]}",
            "",
            "| Id | Stage | Title | Implemented | Tested |",
            "|---|---|---|---|---|",
        ]
        for i in status["items"]:
            if i["area"] == area:
                done = "yes" if i["implemented"] else "no"
                tested = "n/a" if i["tested"] is None else ("yes" if i["tested"] else "no")
                out.append(f"| `{i['id']}` | {i['stage']} | {i['title']} | {done} | {tested} |")
    out += ["", "## Unscoped", ""]
    if status["unscoped"]:
        out.append(
            "In the code but neither planned nor excluded in `scope.yaml` "
            "- add them to one of the two:"
        )
        out.append("")
        out += [f"- {u['area']}: `{u['what']}`" for u in status["unscoped"]]
    else:
        out.append("None.")
    out += ["", "## Excluded", "", "| Id | Reason |", "|---|---|"]
    out += [f"| `{e['id']}` | {e['reason']} |" for e in status["excluded"]]
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", default=".progress/backend.json")
    parser.add_argument("--ui", default=".progress/ui.json")
    parser.add_argument("--ui-tested", default=".progress/ui-tested.json")
    parser.add_argument("--md", default=".progress/PROGRESS.md")
    parser.add_argument("--json", default=".progress/status.json")
    args = parser.parse_args()

    backend = json.loads(Path(args.backend).read_text())
    ui_markers = json.loads(Path(args.ui).read_text())
    ui_tested = json.loads(Path(args.ui_tested).read_text())
    status, errors = build(backend, ui_markers, ui_tested)
    Path(args.md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.md).write_text(render(status))
    Path(args.json).write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")

    for error in errors:
        print(f"progress: scope.yaml: {error}", file=sys.stderr)
    for u in status["unscoped"]:
        print(
            f"progress: unscoped {u['area']}: {u['what']} - plan or exclude it in scope.yaml",
            file=sys.stderr,
        )
    for area in AREAS:
        a = status["areas"][area]
        print(
            f"progress: {area}: {percent(a['implemented'], a['items'])} implemented",
            file=sys.stderr,
        )
    return 1 if errors or status["unscoped"] else 0


if __name__ == "__main__":
    sys.exit(main())
