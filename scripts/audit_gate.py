#!/usr/bin/env python3
"""Decide whether a dependency audit blocks: the gate of the security workflow (#241).

Reads the JSON of `pip-audit --format json` or `npm audit --json` (report version 2) on
stdin. Every pip finding blocks (pip-audit reports no severity). An npm finding blocks at
`high` and above for production packages and at `critical` for dev-only packages; npm audit
does not say which packages are dev-only, so the gate reads the `dev` flag from the
lockfile. A finding is accepted by an entry in the ignore file:

    <advisory id> <review-by YYYY-MM-DD> <reason>

An entry blocks itself once its review-by date has passed, when the date is more than 60
days ahead, or when the line is malformed. Exit 0: nothing blocks; 1: something blocks;
2: the input is not what the gate understands (never pass silently).
Decision: docs/decisions/harness.md, "Supply-chain scanning". Stdlib only.
"""

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IGNORE = ROOT / "ops/security/audit-ignore.txt"
DEFAULT_LOCKFILE = ROOT / "frontend/package-lock.json"
MAX_REVIEW_DAYS = 60
SEVERITY = {"info": 0, "low": 1, "moderate": 2, "high": 3, "critical": 4}
PROD_THRESHOLD = SEVERITY["high"]
DEV_THRESHOLD = SEVERITY["critical"]


class ShapeError(Exception):
    """The input is not the JSON the gate understands."""


@dataclass
class Finding:
    package: str
    versions: list[str]
    advisory: str
    severity: str
    fix: str
    aliases: set[str] = field(default_factory=set)
    blocking: bool = True

    def ids(self) -> set[str]:
        return {self.advisory} | self.aliases

    def line(self) -> str:
        version = ",".join(self.versions) or "?"
        return f"{self.package} {version} {self.advisory} {self.severity} {self.fix}"


def read_ignore(path: Path, today: dt.date) -> tuple[set[str], list[str]]:
    """The accepted advisory ids and one problem line per bad entry."""
    accepted: set[str] = set()
    problems: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return accepted, problems
    latest = today + dt.timedelta(days=MAX_REVIEW_DAYS)
    for number, raw in enumerate(lines, 1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        where = f"{path.name}:{number}"
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            problems.append(f"{where}: malformed, expected '<id> <review-by YYYY-MM-DD> <reason>'")
            continue
        advisory, date_text, _reason = parts
        try:
            review_by = dt.date.fromisoformat(date_text)
        except ValueError:
            problems.append(f"{where}: malformed review-by date {date_text!r}")
            continue
        if review_by < today:
            problems.append(f"{where}: {advisory} review-by {review_by} has passed; review it")
        elif review_by > latest:
            problems.append(
                f"{where}: {advisory} review-by {review_by} "
                f"is more than {MAX_REVIEW_DAYS} days ahead"
            )
        else:
            accepted.add(advisory)
    return accepted, problems


def pip_findings(data: object) -> list[Finding]:
    if not isinstance(data, dict) or not isinstance(data.get("dependencies"), list):
        raise ShapeError("pip-audit JSON needs a 'dependencies' list")
    found: dict[tuple[str, str, str], Finding] = {}
    for dep in data["dependencies"]:
        if not isinstance(dep, dict) or not isinstance(dep.get("name"), str):
            raise ShapeError("pip-audit dependency without a name")
        vulns = dep.get("vulns", [])
        if not isinstance(vulns, list):
            raise ShapeError(f"pip-audit 'vulns' of {dep['name']} is not a list")
        version = str(dep.get("version", "?"))
        for vuln in vulns:
            if not isinstance(vuln, dict) or not isinstance(vuln.get("id"), str):
                raise ShapeError(f"pip-audit vulnerability of {dep['name']} without an id")
            key = (dep["name"], version, vuln["id"])
            if key in found:  # the same package listed in two constraints files
                continue
            fixes = ",".join(vuln.get("fix_versions") or []) or "none"
            found[key] = Finding(
                dep["name"], [version], vuln["id"], "unknown", fixes, set(vuln.get("aliases") or [])
            )
    return list(found.values())


def read_lockfile(path: Path) -> dict:
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ShapeError(f"cannot read the lockfile {path}: {exc}") from exc
    packages = lock.get("packages") if isinstance(lock, dict) else None
    if not isinstance(packages, dict):
        raise ShapeError(f"lockfile {path} has no 'packages' map (lockfileVersion 2 or 3 expected)")
    return packages


def npm_fix(entry: dict) -> str:
    fix = entry.get("fixAvailable")
    if isinstance(fix, dict):
        return f"{fix.get('name')}@{fix.get('version')}"
    return "available" if fix else "none"


def npm_findings(data: object, packages: dict) -> list[Finding]:
    if not isinstance(data, dict) or data.get("auditReportVersion") != 2:
        detail = data.get("error") if isinstance(data, dict) else None
        raise ShapeError(
            f"npm audit JSON with auditReportVersion 2 expected{f': {detail}' if detail else ''}"
        )
    vulnerabilities = data.get("vulnerabilities")
    if not isinstance(vulnerabilities, dict):
        raise ShapeError("npm audit JSON needs a 'vulnerabilities' map")
    found: dict[tuple[str, str], Finding] = {}
    for entry in vulnerabilities.values():
        if not isinstance(entry, dict) or not isinstance(entry.get("via"), list):
            raise ShapeError("npm audit vulnerability without a 'via' list")
        for via in entry["via"]:
            if isinstance(via, str):  # an advisory reported under the named package
                continue
            if (
                not isinstance(via, dict)
                or via.get("severity") not in SEVERITY
                or "name" not in via
            ):
                raise ShapeError(f"npm audit advisory of unknown shape: {via!r}")
            url = str(via.get("url", ""))
            advisory = url.rsplit("/", 1)[-1] if "/advisories/" in url else str(via.get("source"))
            owner = vulnerabilities.get(via["name"], entry)
            nodes = owner.get("nodes") or []
            dev_only = bool(nodes) and all(
                packages.get(node, {}).get("dev") is True for node in nodes
            )
            versions = sorted({str(packages.get(node, {}).get("version", "?")) for node in nodes})
            threshold = DEV_THRESHOLD if dev_only else PROD_THRESHOLD
            finding = Finding(
                via["name"],
                versions,
                advisory,
                via["severity"] + (" (dev)" if dev_only else ""),
                npm_fix(owner),
                {str(via.get("source"))},
                SEVERITY[via["severity"]] >= threshold,
            )
            found.setdefault((finding.package, advisory), finding)
    return list(found.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tool", choices=("pip", "npm"), required=True)
    parser.add_argument("--ignore", type=Path, default=DEFAULT_IGNORE)
    parser.add_argument("--lockfile", type=Path, default=DEFAULT_LOCKFILE, help="npm only")
    parser.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    args = parser.parse_args(argv)

    try:
        data = json.loads(sys.stdin.read())
    except ValueError as exc:
        print(f"audit_gate: {args.tool} input is not JSON: {exc}", file=sys.stderr)
        return 2
    try:
        if args.tool == "pip":
            findings = pip_findings(data)
        else:
            findings = npm_findings(data, read_lockfile(args.lockfile))
    except ShapeError as exc:
        print(f"audit_gate: unknown {args.tool} JSON shape: {exc}", file=sys.stderr)
        return 2

    accepted, problems = read_ignore(args.ignore, args.today)
    blocking = [f for f in findings if f.blocking and not (f.ids() & accepted)]
    waived = [f for f in findings if f.blocking and f.ids() & accepted]
    for finding in blocking:
        print(finding.line())
    for problem in problems:
        print(problem)
    print(
        f"audit_gate {args.tool}: {len(findings)} findings, {len(waived)} accepted, "
        f"{len(findings) - len(blocking) - len(waived)} below the threshold, "
        f"{len(blocking)} blocking; {len(problems)} ignore-file problems"
    )
    return 1 if blocking or problems else 0


if __name__ == "__main__":
    sys.exit(main())
