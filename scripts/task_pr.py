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

import full_suite
from task_git import hooks, push, require, require_no_git_overrides, shared_paths
from task_quality import context, query, review
from task_runtime import InvalidRuntime, diagnostic, master_harness
from task_telemetry import record

REPOSITORY = "altharmz/bss"
BASE = "master"
FIELDS = (
    "number,url,state,body,baseRefName,headRefName,headRefOid,isCrossRepository,"
    "isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,labels,changedFiles"
)
# Files that grant permissions, run code or define policy; everything else merges on green.
HUMAN_PATHS = {
    "CLAUDE.md",
    "AGENTS.md",
    "scripts/pre-commit.sh",
    ".claude/settings.json",
    ".claude/settings.local.json",
}
HUMAN_PREFIXES = (".claude/hooks/", ".codex/", ".github/workflows/")


def gh(root, *args):
    # Read calls only (creation runs separately): one retry rides out a transient GitHub error.
    for attempt in (1, 2):
        try:
            result = subprocess.run(  # noqa: S603, S607 - fixed executable, argv, no shell
                ["gh", *args], cwd=root, capture_output=True, text=True, check=True, timeout=60
            )
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
            time.sleep(float(os.environ.get("BSS_GH_RETRY_PAUSE", "2")))
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


def find_pr(root, task, allow_merged=False):
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
    require(
        data["state"] == "OPEN" or (allow_merged and data["state"] == "MERGED"),
        "task PR is closed or merged",
    )
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
        "next_command": "make check-pr WAIT=600",
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


# The reasons about the human's approval itself; every other reason is fixed first (#395).
APPROVAL_REASONS = {
    "human-approval-required",
    "human-approval-malformed",
    "human-approval-mismatched",
}
# What waiting can resolve: GitHub finishing CI or computing mergeability.
WAITABLE = re.compile(r"^(ci-pending|mergeability-unknown|merge-state-(unknown|unstable))$")


def next_command(status, reasons, number, head, human, approval_state):
    """The one next step. The approval request comes last (#395): only when no other
    reason is left, so the human never approves a head that later has to change."""
    merge = "make merge-task" + (
        f" HUMAN_APPROVAL={number}:{head}" if human and approval_state == "accepted" else ""
    )
    if status == "ready":
        return merge
    if status == "failed":
        return "fix the failure, make commit, make push, then make check-pr WAIT=600"
    fixes = [reason for reason in reasons if reason not in APPROVAL_REASONS]
    if not fixes:
        return (
            f"Human approval needed for PR {number} at {head}; after approval: "
            f"make merge-task HUMAN_APPROVAL={number}:{head}"
        )
    steps = []
    if "base-behind" in fixes:
        steps.append("make sync-task")
    if "uncommitted-changes" in fixes:
        steps.append("make commit")
    if {"base-behind", "uncommitted-changes", "head-mismatch"} & set(fixes):
        steps.append("make push")
    # After the head is final: the record follows the tree it ran on.
    if "full-suite-required" in fixes:
        steps.append("make test")
    handled = {"base-behind", "uncommitted-changes", "head-mismatch", "full-suite-required"}
    rest = [reason for reason in fixes if reason not in handled and not WAITABLE.match(reason)]
    if rest:
        steps.append("resolve " + ", ".join(rest))
    steps.append("make check-pr WAIT=600" if not rest else "make check-pr")
    return ", ".join(steps[:-1]) + (", then " if len(steps) > 1 else "") + steps[-1]


def wait_seconds():
    raw = os.environ.get("WAIT", "")
    if not raw:
        return 0
    require(re.fullmatch(r"[0-9]{1,6}", raw), "WAIT must be a number of seconds")
    return min(int(raw), 1800)


def await_readiness(root, task, issue):
    """Poll every 20 s until ready, failed or blocked, or until WAIT seconds have passed."""
    deadline = time.monotonic() + wait_seconds()
    pause = float(os.environ.get("BSS_CHECK_PR_POLL", "20"))
    while True:
        outcome, result = readiness(root, task, issue)
        remaining = deadline - time.monotonic()
        # A local step (sync, commit, make test) is not waited for: it never comes by itself.
        waiting = any(WAITABLE.match(reason) for reason in outcome.get("reasons", []))
        if outcome["status"] != "pending" or not waiting or remaining <= 0:
            return outcome, result
        time.sleep(min(pause, remaining))


COMPARE_FILE_LIMIT = 300  # GitHub's compare lists at most this many files


def incoming_shared(root, head, own):
    """The paths the PR (`own`: its complete file list) and the master commits it lacks
    both change. A truncated incoming list counts as shared: unknown is never fine."""
    files = gh(root, "api", f"repos/{REPOSITORY}/compare/{head}...{BASE}")["files"]
    require(isinstance(files, list), "invalid base comparison")
    if len(files) >= COMPARE_FILE_LIMIT:
        return ["(more files than GitHub lists)"]
    incoming = [entry["filename"] for entry in files]
    incoming += [entry["previous_filename"] for entry in files if "previous_filename" in entry]
    return shared_paths(own, incoming)


def readiness(root, task, issue, approval=None):
    data = find_pr(root, task)
    if data is None:
        return {
            "status": "missing",
            "ci": "unknown",
            "reasons": ["missing-pr"],
            "next_command": "make create-pr",
        }, 1
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
        n in HUMAN_PATHS or n.startswith(HUMAN_PREFIXES) for n in names
    )
    head = data["headRefOid"]
    require(isinstance(head, str) and re.fullmatch(r"[a-f0-9]{40}", head), "invalid PR head")
    approval = os.environ.get("HUMAN_APPROVAL", "") if approval is None else approval
    approval_state = "missing"
    if approval:
        if not re.fullmatch(r"[1-9][0-9]*:[a-f0-9]{40}", approval):
            approval_state = "malformed"
        elif approval != f"{number}:{head}":
            approval_state = "mismatched"
        else:
            approval_state = "accepted"
    human_blocked = human and approval_state != "accepted"
    comparison = gh(root, "api", f"repos/{REPOSITORY}/compare/{BASE}...{head}")
    behind = comparison["behind_by"]
    require(type(behind) is int and behind >= 0, "invalid base comparison")
    # Behind master is fine when nothing is shared (task_git.shared_paths, #390).
    shared = incoming_shared(root, head, names) if behind else []
    ci = ci_state(data["statusCheckRollup"])
    suite = full_suite.state(root, head, names)
    reasons = []
    if human_blocked:
        reasons.append("human-approval-required")
    if approval_state in {"malformed", "mismatched"}:
        reasons.append("human-approval-" + approval_state)
    if data["reviewDecision"] in {"REVIEW_REQUIRED", "CHANGES_REQUESTED"}:
        reasons.append("review-" + data["reviewDecision"].lower().replace("_", "-"))
    elif data["reviewDecision"] not in {"", "APPROVED"}:
        reasons.append("review-state-unknown")
    if data["isDraft"]:
        reasons.append("draft-pr")
    if shared:
        reasons.append("base-behind")
    if head != query(root, "rev-parse", "HEAD").decode().strip():
        reasons.append("head-mismatch")
    if query(root, "status", "--porcelain=v1", "--untracked-files=all"):
        reasons.append("uncommitted-changes")
    if suite in {"missing", "stale"}:
        reasons.append("full-suite-required")
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
    policy = (
        human_blocked
        or approval_state in {"malformed", "mismatched"}
        or any(r.startswith("review-") for r in reasons)
        or merge_state == "BLOCKED"
    )
    # Waiting for the human only when nothing else is left: a missing approval with a fix
    # still due is pending, and WAIT polls through pending CI (#395).
    others = [reason for reason in reasons if reason not in APPROVAL_REASONS]
    if (
        policy
        and others
        and not (any(r.startswith("review-") for r in reasons) or merge_state == "BLOCKED")
    ):
        policy = False
    status = "failed" if failed else "blocked" if policy else "pending" if reasons else "ready"
    return {
        "next_command": next_command(status, reasons, number, head, human, approval_state),
        "status": status,
        "pr_number": number,
        "url": data["url"],
        "ci": ci,
        "mergeable": data["mergeable"],
        "merge_state": merge_state,
        "review_decision": data["reviewDecision"],
        "human_approval_required": human,
        "human_approval": approval_state,
        "head_sha": head,
        "base_behind": behind,
        "full_suite": suite,
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
        require_no_git_overrides()
        require(
            os.environ.get("GH_HOST", "github.com") == "github.com",
            "unexpected GitHub host; unset GH_HOST",
        )
        if operation == "check-pr":
            # A branch older than a harness fix gets master's gates (#395).
            handed = master_harness(Path.cwd(), operation)
            if handed is not None:
                return handed
        root, primary, task, slot, metadata = context()
        record(task, operation + "-attempt", 0, 0, slot=slot)
        review(root, primary, task, slot, metadata)
        repository(root)
        issue = issue_state(root, task)
        if operation == "create-pr":
            outcome = create(root, task)
            result = 0
        else:
            outcome, result = await_readiness(root, task, issue)
        print(json.dumps(outcome))
    except InvalidRuntime as error:
        print(f"task PR: {error}", file=sys.stderr)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        subprocess.SubprocessError,
    ) as error:
        # API errors and argv may contain prose. Never echo arbitrary response payloads.
        print(
            f"task PR: Git/GitHub failed or returned incomplete state ({diagnostic(error)}); "
            "inspect before retrying",
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
