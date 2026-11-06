"""One row per issue and rollups: what each issue cost and how the agent got there.

Built on `task_usage.report`. Incomplete coverage is flagged on the row ('~' before a
value, and the coverage column), never silently shown as a smaller total. Rollups add
only complete rows, and say how many rows they left out.
"""

import argparse
import json
import re
import subprocess
import sys
from decimal import Decimal, InvalidOperation

from task_telemetry import sql
from task_usage import REPORT_SQL, report

FRICTION_COLUMNS = ("format", "commit", "create-pr", "sync-task", "merge-task")


def area(labels):
    """Labels that describe the kind of work, not priority or ownership."""
    return sorted(
        name
        for name in labels or []
        if not re.fullmatch(r"P[0-9]", name) and not name.startswith("agent:")
    )


def row(task):
    usd = task["known_cost_by_currency"].get("USD")
    facts = task.get("facts") or {}
    friction = task.get("friction") or {}
    return {
        "issue": int(task["task_id"].split("-")[1]),
        "task_id": task["task_id"],
        "models": sorted({u["model"] or "?" for u in task["usage_identities"]}),
        "clients": sorted({u["client"] or "?" for u in task["usage_identities"]}),
        "labels": area(facts.get("labels")),
        "epic": facts.get("epic"),
        "tokens": task["tokens"]["total_tokens"],
        "cost_usd": usd,
        "cost_complete": task["cost_complete"],
        "coverage_complete": task["collection_complete"],
        "active_seconds": task.get("agent_active_seconds"),
        "wall_seconds": task["time_to_merge_seconds"],
        "human_prompts": task.get("human_prompts"),
        "failed_attempts": sum(friction.get(k, 0) for k in friction),
        "friction": friction,
        "additions": facts.get("additions"),
        "deletions": facts.get("deletions"),
        "changed_files": facts.get("changed_files"),
        "followup_bugs": sum("bug" in f.get("labels", []) for f in facts.get("followups") or []),
        "reverted": facts.get("reverted"),
    }


def rollup(rows, key):
    """Sum only what is complete: tokens need full coverage, cost needs full pricing too."""
    groups = {}
    for r in rows:
        for name in r[key] if isinstance(r[key], list) else [r[key]]:
            g = groups.setdefault(
                str(name),
                dict(issues=0, covered=0, priced=0, tokens=0, cost=Decimal(0), lines=0, cl=0),
            )
            g["issues"] += 1
            if not r["coverage_complete"] or r["tokens"] is None:
                continue
            g["covered"] += 1
            g["tokens"] += r["tokens"]
            if r["additions"] is not None:
                g["lines"] += r["additions"] + r["deletions"]
                g["cl"] += r["tokens"]
            if r["cost_complete"] and r["cost_usd"] is not None:
                g["priced"] += 1
                g["cost"] += Decimal(r["cost_usd"])
    result = []
    for name, g in sorted(groups.items()):
        result.append(
            {
                key: name,
                "issues": g["issues"],
                "covered_issues": g["covered"],
                "priced_issues": g["priced"],
                "tokens": g["tokens"],
                "tokens_per_issue": round(g["tokens"] / g["covered"]) if g["covered"] else None,
                "cost_usd": str(g["cost"].quantize(Decimal("0.01"))) if g["priced"] else None,
                "cost_per_issue_usd": str((g["cost"] / g["priced"]).quantize(Decimal("0.01")))
                if g["priced"]
                else None,
                "tokens_per_changed_line": round(g["cl"] / g["lines"]) if g["lines"] else None,
            }
        )
    return result


def summarize(data):
    tasks = [t for t in report(data)["tasks"] if t["sample_count"] or t.get("facts")]
    rows = sorted((row(t) for t in tasks), key=lambda r: r["issue"])
    return {
        "issues": rows,
        "by_model": rollup(rows, "models"),
        "by_client": rollup(rows, "clients"),
        "by_label": rollup(rows, "labels"),
        "by_epic": rollup([r for r in rows if r["epic"]], "epic"),
    }


def human(value, kind):
    if value is None:
        return "-"
    if kind == "tokens":
        return f"{value / 1e6:.1f}M"
    if kind == "hours":
        return f"{value / 3600:.1f}h"
    if kind == "usd":
        return f"${Decimal(value):.2f}"
    return str(value)


def table(summary):
    header = (
        "issue",
        "model",
        "tokens",
        "cost",
        "active",
        "wall",
        "prompts",
        "fails",
        "+/-",
        "files",
        "cov",
    )
    lines = [header]
    for r in summary["issues"]:
        partial = "" if r["coverage_complete"] else "~"
        lines.append(
            (
                f"#{r['issue']}",
                ",".join(m.removeprefix("claude-") for m in r["models"]) or "-",
                partial + human(r["tokens"], "tokens"),
                ("" if r["cost_complete"] else "~") + human(r["cost_usd"], "usd"),
                human(r["active_seconds"], "hours"),
                human(r["wall_seconds"], "hours"),
                human(r["human_prompts"], "n"),
                str(r["failed_attempts"]),
                f"+{r['additions']}/-{r['deletions']}" if r["additions"] is not None else "-",
                human(r["changed_files"], "n"),
                "ok" if r["coverage_complete"] else "partial",
            )
        )
    widths = [max(len(str(line[i])) for line in lines) for i in range(len(header))]
    out = ["  ".join(str(c).ljust(w) for c, w in zip(line, widths, strict=True)) for line in lines]
    for group, key in (("by_model", "models"), ("by_label", "labels"), ("by_epic", "epic")):
        out.append("")
        out.append(f"by {key} (sums over complete rows only):")
        for g in summary[group]:
            per_issue = human(g["tokens_per_issue"], "tokens")
            per_cost = human(g["cost_per_issue_usd"], "usd")
            out.append(
                f"  {g[key]:<22} issues={g['issues']:<3} covered={g['covered_issues']:<3} "
                f"tokens/issue={per_issue:<7} priced={g['priced_issues']:<3} "
                f"cost/issue={per_cost:<8} tokens/line={human(g['tokens_per_changed_line'], 'n')}"
            )
    out.append("")
    out.append("~ = incomplete coverage or pricing (value is a lower bound). Cost is the")
    out.append("API-equivalent list price; active = summed agent turn time (parallel agents add).")
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        summary = summarize(json.loads(sql(REPORT_SQL)))
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        InvalidOperation,
        RuntimeError,
        subprocess.SubprocessError,
    ):
        print("telemetry: summary failed; check storage", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True) if args.json else table(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
