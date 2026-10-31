"""Create task pull requests and report readiness without merging or bypassing policy."""

# GitHub CLI is resolved from the local development toolchain, as Git is in task_git.
# ruff: noqa: S607

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from task_git import hooks, push, require
from task_quality import context, query, review
from task_runtime import InvalidRuntime
from task_telemetry import record

REPOSITORY = "altharmz/bss"
BASE = "master"
FIELDS = (
    "number,url,state,body,baseRefName,headRefName,headRefOid,isCrossRepository,"
    "isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,labels,changedFiles"
)
HUMAN_PATHS = {
    "CLAUDE.md",
    "AGENTS.md",
    "docs/board.md",
    "scripts/pre-commit.sh",
    ".codex/guard.sh",
    ".codex/patch-guard.sh",
}


def gh(root, *args):
    result = subprocess.run(  # noqa: S603, S607 - fixed executable, argv, no shell
        ["gh", *args], cwd=root, capture_output=True, text=True, check=True, timeout=60
    )
    if "--paginate" not in args:
        return json.loads(result.stdout)
    # Older installed gh versions emit consecutive JSON documents, without --slurp.
    remaining = result.stdout.lstrip()
    pages = []
    decoder = json.JSONDecoder()
    while remaining:
        page, end = decoder.raw_decode(remaining)
        require(isinstance(page, list), "incomplete GitHub file page")
        pages.append(page)
        remaining = remaining[end:].lstrip()
    require(pages, "missing GitHub file pages")
    return pages


def repository(root):
    # Explicit --repo prevents GH_REPO or Git configuration selecting another project.
    expected = {
        "git@github.com:altharmz/bss.git",
        "https://github.com/altharmz/bss.git",
        "https://github.com/altharmz/bss",
        "ssh://git@github.com/altharmz/bss.git",
    }
    for args in (("--all",), ("--push", "--all")):
        urls = query(root, "remote", "get-url", *args, "origin").decode().splitlines()
        require(len(urls) == 1 and urls[0] in expected, "unexpected origin repository")


def issue_state(root, task):
    issue = int(task.split("-")[1])
    data = gh(
        root, "issue", "view", str(issue), "--repo", REPOSITORY, "--json", "number,state,labels"
    )
    require(data["number"] == issue and data["state"] == "OPEN", "task issue is not open")
    return data


def find_pr(root, task):
    found = gh(
        root,
        "pr",
        "list",
        "--repo",
        REPOSITORY,
        "--head",
        task,
        "--state",
        "all",
        "--limit",
        "100",
        "--json",
        "number",
    )
    require(len(found) <= 1, "conflicting PR history for task branch; inspect GitHub")
    if not found:
        return None
    data = gh(root, "pr", "view", str(found[0]["number"]), "--repo", REPOSITORY, "--json", FIELDS)
    number = data["number"]
    require(type(number) is int and number > 0, "invalid PR number")
    require(data["url"] == f"https://github.com/{REPOSITORY}/pull/{number}", "unexpected PR URL")
    require(
        data["headRefName"] == task
        and data["baseRefName"] == BASE
        and data["isCrossRepository"] is False,
        "conflicting PR branch or repository",
    )
    require(data["state"] == "OPEN", "task PR is closed or merged")
    issue = task.split("-")[1]
    require(
        re.search(
            rf"(?im)^Fixes (?:#{issue}|{REPOSITORY}#{issue}|"
            rf"https://github.com/{REPOSITORY}/issues/{issue})\s*$",
            data["body"],
        ),
        "PR must contain its task's Fixes reference on a separate line",
    )
    return data


def create(root, task):
    require(
        not query(root, "status", "--porcelain=v1", "--untracked-files=all"),
        "commit intended changes before creating a PR",
    )
    existing = find_pr(root, task)
    # Publication is the existing safe task push, including normal pre-push hooks.
    hooks(root)
    push(root, task)
    if existing:
        data = find_pr(root, task)
    else:
        title = (
            os.environ.get("PR_TITLE") or query(root, "log", "-1", "--format=%s").decode().strip()
        )
        body_file = os.environ.get("PR_BODY_FILE", "")
        body = (
            Path(body_file).read_text() if body_file else f"Implements task #{task.split('-')[1]}."
        )
        body += f"\n\nFixes #{task.split('-')[1]}\n"
        # A file preserves newlines and keeps arbitrary prose out of shell commands.
        with tempfile.TemporaryDirectory(prefix="bss-pr-") as directory:
            path = Path(directory) / "body.md"
            path.write_text(body)
            subprocess.run(  # noqa: S603, S607
                [
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    REPOSITORY,
                    "--base",
                    BASE,
                    "--head",
                    task,
                    "--title",
                    title,
                    "--body-file",
                    str(path),
                ],
                cwd=root,
                capture_output=True,
                check=True,
                timeout=60,
            )
        data = find_pr(root, task)
    require(data is not None, "PR creation was not confirmed; inspect GitHub before retrying")
    require(
        data["headRefOid"] == query(root, "rev-parse", "HEAD").decode().strip(),
        "PR head changed during creation; inspect GitHub",
    )
    return {
        "status": "created" if existing is None else "existing",
        "pr_number": data["number"],
        "url": data["url"],
    }


def ci_state(checks):
    require(isinstance(checks, list), "incomplete check response")
    states = []
    for check in checks:
        if check["__typename"] == "CheckRun":
            state = check["conclusion"] if check["status"] == "COMPLETED" else "PENDING"
        elif check["__typename"] == "StatusContext":
            state = check["state"]
        else:
            raise InvalidRuntime("unknown check type")
        states.append(state)
    if any(
        s
        in {
            "FAILURE",
            "ERROR",
            "CANCELLED",
            "TIMED_OUT",
            "ACTION_REQUIRED",
            "STARTUP_FAILURE",
            "STALE",
        }
        for s in states
    ):
        return "failed"
    if not states or any(s not in {"SUCCESS", "NEUTRAL", "SKIPPED"} for s in states):
        return "pending"
    return "passed"


def readiness(root, task, issue):
    data = find_pr(root, task)
    if data is None:
        return {"status": "missing", "ci": "unknown", "reasons": ["missing-pr"]}, 1
    number = data["number"]
    pages = gh(
        root,
        "api",
        "--paginate",
        f"repos/{REPOSITORY}/pulls/{number}/files?per_page=100",
    )
    entries = [entry for page in pages for entry in page]
    names = [entry["filename"] for entry in entries]
    require(
        type(data["changedFiles"]) is int
        and len(names) == data["changedFiles"]
        and len(set(names)) == len(names),
        "incomplete or duplicate PR file list; human policy cannot be established",
    )
    # A rename away from an instruction or guard path still touches that protected path.
    names.extend(entry["previous_filename"] for entry in entries if "previous_filename" in entry)
    human = "needs-human" in {label["name"] for label in issue["labels"] + data["labels"]} or any(
        n in HUMAN_PATHS or n.startswith((".claude/", ".github/workflows/")) for n in names
    )
    head = data["headRefOid"]
    require(isinstance(head, str) and re.fullmatch(r"[a-f0-9]{40}", head), "invalid PR head")
    comparison = gh(root, "api", f"repos/{REPOSITORY}/compare/{BASE}...{head}")
    behind = comparison["behind_by"]
    require(type(behind) is int and behind >= 0, "invalid base comparison")
    ci = ci_state(data["statusCheckRollup"])
    reasons = []
    if human:
        reasons.append("human-approval-required")
    if data["reviewDecision"] in {"REVIEW_REQUIRED", "CHANGES_REQUESTED"}:
        reasons.append("review-" + data["reviewDecision"].lower().replace("_", "-"))
    elif data["reviewDecision"] not in {"", "APPROVED"}:
        reasons.append("review-state-unknown")
    if data["isDraft"]:
        reasons.append("draft-pr")
    if behind:
        reasons.append("base-behind")
    if head != query(root, "rev-parse", "HEAD").decode().strip():
        reasons.append("head-mismatch")
    if query(root, "status", "--porcelain=v1", "--untracked-files=all"):
        reasons.append("uncommitted-changes")
    if ci != "passed":
        reasons.append("ci-" + ci)
    if data["mergeable"] != "MERGEABLE":
        reasons.append(
            "merge-conflict" if data["mergeable"] == "CONFLICTING" else "mergeability-unknown"
        )
    merge_state = data["mergeStateStatus"]
    if merge_state != "CLEAN":
        reasons.append("merge-state-" + merge_state.lower())
    failed = ci == "failed" or "merge-conflict" in reasons
    policy = human or any(r.startswith("review-") for r in reasons) or merge_state == "BLOCKED"
    status = "failed" if failed else "blocked" if policy else "pending" if reasons else "ready"
    return {
        "status": status,
        "pr_number": number,
        "url": data["url"],
        "ci": ci,
        "mergeable": data["mergeable"],
        "merge_state": merge_state,
        "review_decision": data["reviewDecision"],
        "human_approval_required": human,
        "base_behind": behind,
        "reasons": reasons,
    }, {"ready": 0, "failed": 1, "pending": 2, "blocked": 3}[status]


def main():
    operation = sys.argv[1]
    task = slot = None
    result = 1
    outcome = {}
    started = time.monotonic()
    try:
        require(operation in {"create-pr", "check-pr"}, "unknown operation")
        require(
            not any(k.startswith("GIT_") and k != "GIT_PAGER" for k in os.environ),
            "unset inherited GIT_* overrides",
        )
        require(
            os.environ.get("GH_HOST", "github.com") == "github.com",
            "unexpected GitHub host; unset GH_HOST",
        )
        root, primary, task, slot, metadata = context()
        record(task, operation + "-attempt", 0, 0, slot=slot)
        review(root, primary, task, slot, metadata)
        repository(root)
        issue = issue_state(root, task)
        if operation == "create-pr":
            outcome = create(root, task)
            result = 0
        else:
            outcome, result = readiness(root, task, issue)
        print(json.dumps(outcome))
    except InvalidRuntime as error:
        print(f"task PR: {error}", file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        # API errors and argv may contain prose. Never echo arbitrary response payloads.
        print(
            "task PR: Git/GitHub failed or returned incomplete state; inspect before retrying",
            file=sys.stderr,
        )
    finally:
        if task:
            record(
                task,
                operation,
                result,
                int((time.monotonic() - started) * 1000),
                slot=slot,
                pr_number=outcome.get("pr_number"),
                pr_status=outcome.get("status"),
                ci_state=outcome.get("ci"),
            )
    return result


if __name__ == "__main__":
    sys.exit(main())
