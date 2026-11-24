"""GitHub facts per task for effectiveness analysis: PR size, labels, priority, epic,
follow-ups.

Content-free: numbers, counts, label names and timestamps only. No titles, bodies or
diffs leave GitHub's answer. Facts are GitHub's current view, so a refresh replaces the
row (unlike usage samples, which are immutable measurements).
"""

# SQL values are validated integers, label slugs and ISO timestamps, quoted literally.
# gh is resolved from the local development toolchain's PATH, like task_pr.py.
# ruff: noqa: S607, S608

import argparse
import json
import re
import subprocess
import sys

from task_board import PRIORITIES, PROJECT
from task_telemetry import literal, sql

REPO = "altharmz/bss"
TASK = re.compile(r"issue-([1-9][0-9]*)-[a-z0-9-]+")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.:/+-]{0,49}")
REFERENCES = """query($number: Int!) { repository(owner: "altharmz", name: "bss") {
  issue(number: $number) {
    parent { number }
    projectItems(first: 20) { nodes { project { id }
      fieldValueByName(name: "Priority") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
    } }
    timelineItems(itemTypes: [CROSS_REFERENCED_EVENT], first: 100) { nodes {
      ... on CrossReferencedEvent { source {
        __typename
        ... on Issue { number createdAt labels(first: 20) { nodes { name } } }
        ... on PullRequest { number createdAt title } } } } } } } }"""


def gh(*args):
    result = subprocess.run(  # noqa: S603, S607 - fixed executable and argv, no shell
        ["gh", *args], capture_output=True, text=True, check=True, timeout=60
    )
    return json.loads(result.stdout)


def labels(nodes):
    names = sorted({n["name"] for n in nodes if isinstance(n, dict) and "name" in n})
    return [name for name in names if LABEL.fullmatch(name)]


def board_priority(graph):
    """The issue's Priority field on the BSS board, or None when empty or not on it."""
    for item in (graph.get("projectItems") or {}).get("nodes") or []:
        if ((item or {}).get("project") or {}).get("id") == PROJECT:
            name = (item.get("fieldValueByName") or {}).get("name")
            return name if name in PRIORITIES else None
    return None


def facts(task, fetch=gh):
    """Collect one task's facts; None when it has no merged PR yet."""
    match = TASK.fullmatch(task)
    if not match or len(task) > 80:
        raise ValueError("invalid task identity")
    issue = int(match[1])
    prs = fetch(
        "pr",
        "list",
        "--repo",
        REPO,
        "--head",
        task,
        "--state",
        "merged",
        "--json",
        "number,additions,deletions,changedFiles,commits,mergedAt",
    )
    if not prs:
        return None
    pr = max(prs, key=lambda p: p["mergedAt"])
    issue_data = fetch("issue", "view", str(issue), "--repo", REPO, "--json", "labels")
    graph = fetch("api", "graphql", "-f", f"query={REFERENCES}", "-F", f"number={issue}")["data"][
        "repository"
    ]["issue"]
    followups, reverted = [], False
    for node in graph["timelineItems"]["nodes"]:
        source = (node or {}).get("source") or {}
        if not source.get("number") or source.get("number") == pr["number"]:
            continue
        if source.get("createdAt", "") <= pr["mergedAt"]:
            continue  # References made before the merge are planning, not follow-ups.
        if source["__typename"] == "PullRequest":
            # The title is read only to detect a revert; it is never stored.
            if str(source.get("title", "")).startswith("Revert"):
                reverted = True
            followups.append({"number": source["number"], "kind": "pr"})
        elif source["__typename"] == "Issue":
            followups.append(
                {
                    "number": source["number"],
                    "kind": "issue",
                    "labels": labels(source.get("labels", {}).get("nodes", [])),
                }
            )
    parent = graph.get("parent") or {}
    return {
        "pr_number": int(pr["number"]),
        "additions": int(pr["additions"]),
        "deletions": int(pr["deletions"]),
        "changed_files": int(pr["changedFiles"]),
        "commits": len(pr["commits"]),
        "labels": labels(issue_data.get("labels", [])),
        "priority": board_priority(graph),
        "epic": int(parent["number"]) if parent.get("number") else None,
        "merged_at": pr["mergedAt"],
        "followups": sorted(followups, key=lambda f: f["number"]),
        "reverted": reverted,
    }


def facts_sql(task, data):
    if not TASK.fullmatch(task):
        raise ValueError("invalid task identity")
    for key in ("pr_number", "additions", "deletions", "changed_files", "commits"):
        if type(data[key]) is not int or data[key] < 0:
            raise ValueError("invalid count")
    if data["epic"] is not None and (type(data["epic"]) is not int or data["epic"] <= 0):
        raise ValueError("invalid epic")
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.]+Z", data["merged_at"]):
        raise ValueError("invalid merge time")
    if type(data["reverted"]) is not bool:
        raise ValueError("invalid revert flag")
    if data["priority"] is not None and data["priority"] not in PRIORITIES:
        raise ValueError("invalid priority")
    values = {
        **{k: str(data[k]) for k in ("pr_number", "additions", "deletions", "changed_files")},
        "commits": str(data["commits"]),
        "labels": literal(json.dumps(data["labels"])) + "::jsonb",
        "priority": "NULL" if data["priority"] is None else literal(data["priority"]),
        "epic": "NULL" if data["epic"] is None else str(data["epic"]),
        "merged_at": literal(data["merged_at"]) + "::timestamptz",
        "followups": literal(json.dumps(data["followups"])) + "::jsonb",
        "reverted": "true" if data["reverted"] else "false",
    }
    columns = ", ".join(values)
    updates = ", ".join(f"{k}=EXCLUDED.{k}" for k in values)
    return (
        f"INSERT INTO task_facts(task_id, {columns}) VALUES "
        f"({literal(task)}, {', '.join(values.values())}) "
        f"ON CONFLICT (task_id) DO UPDATE SET {updates}, captured_at=now();"
    )


def capture(task, fetch=gh):
    data = facts(task, fetch)
    if data is None:
        return False
    sql(facts_sql(task, data))
    return True


def merged_tasks():
    return json.loads(
        sql(
            "SELECT COALESCE(json_agg(task_id ORDER BY task_id),'[]') FROM tasks "
            "WHERE outcome='completed' OR task_id IN (SELECT task_id FROM workflow_events "
            "WHERE event_type='merge-task' AND result=0);"
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task")
    args = parser.parse_args()
    captured = missing = failed = 0
    try:
        tasks = [args.task] if args.task else merged_tasks()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        print("telemetry: facts failed; check storage", file=sys.stderr)
        return 1
    for task in tasks:
        try:
            if capture(task):
                captured += 1
            else:
                missing += 1
        except (
            OSError,
            RuntimeError,
            ValueError,
            KeyError,
            TypeError,
            subprocess.SubprocessError,
        ):
            failed += 1
    print(json.dumps({"captured": captured, "no_merged_pr": missing, "failed": failed}))
    return int(failed > 0)


if __name__ == "__main__":
    sys.exit(main())
