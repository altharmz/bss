#!/usr/bin/env python3
"""make coverage (#480): one line-coverage report for the backend and the frontend.

Reads coverage.py's JSON report (app/, bss_mcp/) and Vitest's V8 reports (frontend/src/),
then writes `.coverage-report/report.json` and `report.md`: the totals per source, the
combined percentage weighted by executable lines (raw counts, never the reporters' rounded
percentages), every file with missing lines, the explicit exclusions and the suites' exit
statuses. Exits 1 when a suite failed or a report is missing, after writing what it could.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_EXCLUSION = re.compile(r"(?:v8|istanbul|c8)\s+ignore")


def percent(covered: int, total: int) -> str:
    if total == 0:
        return "n/a"
    value = Decimal(covered * 100) / Decimal(total)
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)}%"


def backend_files(report: dict) -> list[dict]:
    files = []
    for path, data in report["files"].items():
        summary = data["summary"]
        files.append(
            {
                "path": path,
                "covered": summary["covered_lines"],
                "executable": summary["num_statements"],
                "missing_lines": sorted(data["missing_lines"]),
                "excluded_lines": sorted(data.get("excluded_lines", [])),
            }
        )
    return files


def istanbul_lines(entry: dict) -> dict[int, int]:
    """Line hits the way istanbul-lib-coverage derives them: a statement counts on its
    start line, and a line is as covered as its best statement (json-summary's numbers)."""
    lines: dict[int, int] = {}
    for key, where in entry["statementMap"].items():
        line = where["start"]["line"]
        count = entry["s"].get(key, 0)
        if line not in lines or lines[line] < count:
            lines[line] = count
    return lines


def frontend_relative(path: str) -> str:
    for marker in ("/web/", "/frontend/"):
        if marker in path:
            return "frontend/" + path.split(marker, 1)[1]
    return path


def frontend_files(report: dict, root: Path) -> list[dict]:
    files = []
    for path, entry in report.items():
        lines = istanbul_lines(entry)
        relative = frontend_relative(path)
        source = root / relative
        excluded = []
        if source.is_file():
            text = source.read_text(encoding="utf-8").splitlines()
            excluded = [n for n, line in enumerate(text, 1) if FRONTEND_EXCLUSION.search(line)]
        files.append(
            {
                "path": relative,
                "covered": sum(1 for hits in lines.values() if hits > 0),
                "executable": len(lines),
                "missing_lines": sorted(n for n, hits in lines.items() if hits == 0),
                "excluded_lines": excluded,
            }
        )
    return files


def totals(files: list[dict]) -> dict:
    covered = sum(f["covered"] for f in files)
    executable = sum(f["executable"] for f in files)
    return {
        "covered": covered,
        "executable": executable,
        "missing": executable - covered,
        "percent": percent(covered, executable),
    }


def ranges(lines: list[int]) -> str:
    parts, start, prev = [], None, None
    for line in lines:
        if start is None:
            start = prev = line
        elif line == prev + 1:
            prev = line
        else:
            parts.append(f"{start}" if start == prev else f"{start}-{prev}")
            start = prev = line
    if start is not None:
        parts.append(f"{start}" if start == prev else f"{start}-{prev}")
    return ", ".join(parts)


def markdown(result: dict) -> str:
    out = ["# Line coverage", ""]
    out.append(f"Commit: `{result['commit']}`" if result["commit"] else "Commit: unknown")
    out.append("")
    out += ["| Source | Covered | Executable | Missing | Line coverage | Suite exit |"]
    out += ["| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name in ("backend", "frontend", "combined"):
        t = result["totals"].get(name)
        if t is None:
            continue
        status = result["statuses"].get(name, "")
        out.append(
            f"| {name} | {t['covered']} | {t['executable']} | {t['missing']} | "
            f"{t['percent']} | {'' if status is None else status} |"
        )
    for problem in result["problems"]:
        out += ["", f"**Problem:** {problem}"]
    out += ["", "## Files with missing lines", ""]
    out += ["| File | Missing | Lines |", "| --- | ---: | --- |"]
    for f in result["files"]:
        if f["missing_lines"]:
            out.append(
                f"| `{f['path']}` | {len(f['missing_lines'])} | {ranges(f['missing_lines'])} |"
            )
    out += ["", "## Explicit exclusions", ""]
    excluded = [f for f in result["files"] if f["excluded_lines"]]
    if not excluded:
        out.append("None.")
    for f in excluded:
        out.append(f"- `{f['path']}`: {ranges(f['excluded_lines'])}")
    return "\n".join(out) + "\n"


def read_status(path: Path | None) -> int | None:
    if path is None or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return int(text) if text.lstrip("-").isdigit() else None


def build(args: argparse.Namespace) -> dict:
    problems: list[str] = []
    result: dict = {"commit": args.commit, "totals": {}, "statuses": {}, "files": []}
    sources = []
    if args.backend.is_file():
        files = backend_files(json.loads(args.backend.read_text(encoding="utf-8")))
        result["totals"]["backend"] = totals(files)
        sources.append(files)
    else:
        problems.append(f"backend report missing: {args.backend}")
    if args.frontend.is_file():
        files = frontend_files(json.loads(args.frontend.read_text(encoding="utf-8")), args.root)
        result["totals"]["frontend"] = totals(files)
        sources.append(files)
        if args.frontend_summary and args.frontend_summary.is_file():
            summary = json.loads(args.frontend_summary.read_text(encoding="utf-8"))["total"][
                "lines"
            ]
            mine = result["totals"]["frontend"]
            if (summary["covered"], summary["total"]) != (mine["covered"], mine["executable"]):
                problems.append(
                    "frontend lines differ from json-summary: "
                    f"{mine['covered']}/{mine['executable']}"
                    f" vs {summary['covered']}/{summary['total']}"
                )
    else:
        problems.append(f"frontend report missing: {args.frontend}")
    all_files = [f for files in sources for f in files]
    if len(sources) == 2:
        result["totals"]["combined"] = totals(all_files)
    result["files"] = sorted(all_files, key=lambda f: (-len(f["missing_lines"]), f["path"]))
    for name, path in (("backend", args.backend_status), ("frontend", args.frontend_status)):
        status = read_status(path)
        result["statuses"][name] = status
        if status is None:
            problems.append(f"{name} suite exit status unknown")
        elif status != 0:
            problems.append(f"{name} suite exited with {status}")
    result["problems"] = problems
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--backend-status", type=Path)
    parser.add_argument("--frontend", type=Path, required=True)
    parser.add_argument("--frontend-summary", type=Path)
    parser.add_argument("--frontend-status", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--commit", default="")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    result = build(args)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (args.out / "report.md").write_text(markdown(result), encoding="utf-8")
    for name, t in result["totals"].items():
        print(f"{name}: {t['covered']}/{t['executable']} lines, {t['percent']}")
    for problem in result["problems"]:
        print(f"problem: {problem}", file=sys.stderr)
    print(f"report: {args.out / 'report.md'}")
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
