#!/usr/bin/env python3
"""Claim and prepare one Ready issue, including disposable development credentials."""

import argparse
import contextlib
import fcntl
import json
import os
import re
import secrets
import shlex
import signal
import socket
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path

import task_testmon
from task_board import (
    DOING,
    PROJECT,
    READY,
    add_to_board,
    nodes,
    ownership,
    pages,
    query,
    set_status,
    settled_snapshot,
    snapshot,
    status,
    wait_for_board_item,
)
from task_repo import (
    StartupError,
    clean,
    git,
    no_tracked_secrets,
    primary,
    run,
    secret_file,
    worktrees,
)
from task_runtime import (
    RESERVED_PORTS,
    InvalidRuntime,
    Reexec,
    diagnostic,
    reexec_if_scripts_changed,
    rerun,
    unique_fields,
    validate_metadata,
)
from task_telemetry import record
from worker_slots import SlotError, candidates, release, reserve, undo
from worker_slots import claim as claim_slot

REPOSITORY = "altharmz/bss"
PORT_START, PORT_END = 21000, 21998
TASK_BRANCH = re.compile(r"^issue-([1-9][0-9]*)-")

PR_QUERY = """
query($cursor: String) {
  repository(owner: "altharmz", name: "bss") {
    pullRequests(states: OPEN, first: 100, after: $cursor) {
      totalCount pageInfo { hasNextPage endCursor }
      nodes { headRefName }
    }
  }
}
"""


def branch_name(number, title):
    slug = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-") or "task"
    prefix = f"issue-{number}-"
    branch = prefix + slug[: 80 - len(prefix)].rstrip("-")
    validate_metadata(runtime(branch, [21000, 21001, 21002]), branch)
    return branch


def runtime(branch, ports):
    return dict(
        version=1,
        task_id=branch,
        **dict(zip(("app_port", "mcp_port", "web_port"), ports, strict=True)),
    )


def issue_of(branch):
    match = TASK_BRANCH.match(branch)
    return int(match[1]) if match else None


def select_issue(items, represented, requested=None):
    choices, seen = [], set()
    for position, item in enumerate(items):
        if item["id"] in seen:
            raise StartupError("ambiguous board order: duplicate project item")
        seen.add(item["id"])
        issue = item.get("content") or {}
        if requested is not None and issue.get("number") != requested:
            continue
        if (
            (item.get("fieldValueByName") or {}).get("name") != "Ready"
            or issue.get("state") != "OPEN"
            or issue.get("repository", {}).get("nameWithOwner") != REPOSITORY
        ):
            continue
        labels = {label["name"] for label in nodes(issue["labels"])}
        if labels & {"blocked", "needs-human"} or any(s.startswith("agent:") for s in labels):
            continue
        if issue["number"] in represented:
            continue
        if any(n["state"] == "OPEN" for n in nodes(issue["blockedBy"])):
            continue
        if any(n["ref"] is not None for n in nodes(issue["linkedBranches"])):
            continue
        if any(n["state"] == "OPEN" for n in nodes(issue["closedByPullRequestsReferences"])):
            continue
        priority = labels & {"P0", "P1", "P2", "P3"} or {"P3"}
        if len(priority) != 1:
            raise StartupError(
                f"ambiguous priority for Ready issue #{issue['number']}: need one P0–P3"
            )
        choices.append((int(next(iter(priority))[1]), position, dict(issue, item_id=item["id"])))
    if not choices:
        if requested is not None:
            raise StartupError(
                f"requested issue #{requested} is not an eligible Ready issue "
                "(blocked, claimed, branches/worktrees/PRs excluded); no fallback selected"
            )
        raise StartupError(
            "no eligible Ready issue (blocked, claimed, branches/worktrees/PRs excluded)"
        )
    return min(choices, key=lambda choice: choice[:2])[2]


def requested_issue():
    """A human-approved selection overrides order, never eligibility or board priority.

    The matching approval records the operator's assertion, not proof of conversation
    approval. Agents must obtain explicit approval for this issue before passing it.
    """
    number = os.environ.get("BSS_START_ISSUE", "")
    approval = os.environ.get("BSS_START_APPROVAL", "")
    if not number:
        if approval:
            raise StartupError("HUMAN_APPROVAL requires ISSUE=<id> for start-task")
        return None
    if not re.fullmatch(r"[1-9][0-9]*", number):
        raise StartupError("ISSUE must be a positive issue number")
    if approval != number:
        raise StartupError(
            f"ISSUE={number} requires explicit human approval in the conversation; "
            f"after approval pass HUMAN_APPROVAL={number}"
        )
    return int(number)


def task_owner():
    owner = os.environ.get("BSS_TASK_OWNER", os.environ.get("BSS_TASK_AGENT"))
    if owner is None:
        detected = {
            client
            for client, marker in (("codex", "CODEX_THREAD_ID"), ("claude", "CLAUDECODE"))
            if os.environ.get(marker)
        }
        if len(detected) > 1:
            raise StartupError("ambiguous client identity; set BSS_TASK_OWNER explicitly")
        owner = next(iter(detected), None)
    if owner is None or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", owner):
        raise StartupError(
            "BSS_TASK_OWNER must be 1–32 lowercase letters, digits or hyphens, "
            "starting with a letter"
        )
    return owner


def agent_label():
    # Owner/client identity and worker-slot allocation are independent.
    return f"agent:{task_owner()}"


def body_from_file(path):
    file = Path(path)
    if not file.is_absolute() or file.is_symlink() or not file.is_file():
        raise StartupError("BODY_FILE must be an absolute path to a regular file")
    if file.stat().st_size > 65536:
        raise StartupError("BODY_FILE exceeds the 64 KiB limit")
    try:
        return file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise StartupError("BODY_FILE is not readable UTF-8 text") from None


def new_task_request():
    """(title, body, labels) for a new issue, or (None, number, None) to finish an existing one."""
    resume = os.environ.get("BSS_NEW_TASK_ISSUE", "").strip()
    title = os.environ.get("BSS_NEW_TASK_TITLE", "").strip()
    body = os.environ.get("BSS_NEW_TASK_BODY", "")
    body_file = os.environ.get("BSS_NEW_TASK_BODY_FILE", "")
    if resume:
        if title or body or body_file or os.environ.get("BSS_NEW_TASK_LABELS"):
            raise StartupError("ISSUE finishes an existing issue; give no TITLE, BODY or LABELS")
        if not re.fullmatch(r"[1-9][0-9]*", resume):
            raise StartupError("ISSUE must be an issue number")
        return None, int(resume), None
    if body_file:
        if body:
            raise StartupError("give BODY or BODY_FILE, not both")
        body = body_from_file(body_file)
    if not title or not body.strip():
        raise StartupError("new-priority-task requires non-empty TITLE and BODY (or BODY_FILE)")
    labels = list(
        dict.fromkeys(
            label.strip()
            for label in os.environ.get("BSS_NEW_TASK_LABELS", "").split(",")
            if label.strip()
        )
    )
    if any(
        re.fullmatch(r"P[0-3]", label)
        or label.startswith("agent:")
        or label in {"blocked", "needs-human"}
        for label in labels
    ):
        raise StartupError("new-priority-task LABELS cannot set priority, ownership or gates")
    return title, body, labels


def prioritize(number, url, title):
    """Put an existing P0 issue on the board as the first Ready item."""
    try:
        created = wait_for_board_item(number, float(os.environ.get("BSS_BOARD_ADD_WAIT", "20")))
        if created is None:
            print(
                f"start-task: #{number} was not added to the board by its automation; "
                "adding it directly",
                file=sys.stderr,
            )
            add_to_board(number)
            created = wait_for_board_item(number, 5)
        if created is None:
            raise StartupError("new issue did not appear on the BSS board")
        set_status(dict(created, item_id=created["id"]), READY)
        query(
            """mutation($project: ID!, $item: ID!) {
              updateProjectV2ItemPosition(input: {projectId: $project, itemId: $item}) {
                clientMutationId
              }
            }""",
            project=PROJECT,
            item=created["id"],
        )
        items = settled_snapshot()
        if (
            not items
            or items[0]["id"] != created["id"]
            or status(items[0]) != "Ready"
            or items[0]["content"]["number"] != number
        ):
            raise StartupError("new issue is not first in Ready after prioritization")
        return {
            "issue": number,
            "url": url,
            "title": title,
            "status": "Ready",
            "next_command": "make start-task BSS_TASK_OWNER=<client>",
        }
    except (StartupError, OSError, ValueError, KeyError, TypeError) as error:
        raise StartupError(
            f"new issue #{number} is preserved after setup failure: {error}; "
            f"finish it with: make new-priority-task ISSUE={number}"
        ) from None


def existing_priority_issue(number):
    """A preserved issue to finish: open, P0, unclaimed (never someone's active work)."""
    data = json.loads(
        run(
            "gh",
            "issue",
            "view",
            str(number),
            "--repo",
            REPOSITORY,
            "--json",
            "number,state,title,url,labels",
        ).stdout
    )
    labels = {label["name"] for label in data["labels"]}
    if data["number"] != number or data["state"] != "OPEN" or "P0" not in labels:
        raise StartupError(f"#{number} is not an open P0 issue")
    if any(label.startswith("agent:") for label in labels):
        raise StartupError(f"#{number} is already claimed by an agent")
    return data["url"], data["title"]


def create_priority_task(request):
    title, body, labels = request
    if title is None:
        url, existing = existing_priority_issue(body)
        return prioritize(body, url, existing)
    args = [
        "gh",
        "issue",
        "create",
        "--repo",
        REPOSITORY,
        "--title",
        title,
        "--body",
        body,
        "--label",
        "P0",
    ]
    for label in labels:
        args.extend(["--label", label])
    result = run(*args)
    match = re.fullmatch(
        r"https://github\.com/altharmz/bss/issues/([1-9][0-9]*)", result.stdout.strip()
    )
    if not match:
        raise StartupError(
            "issue creation returned an unexpected URL; review GitHub before retrying"
        )
    return prioritize(int(match[1]), result.stdout.strip(), title)


class Claim:
    """Compensate only an acknowledged, still-recognizable remote mutation."""

    def __init__(self, issue, agent):
        self.issue, self.agent = issue, agent
        self.claimed = self.active = False

    def acquire(self):
        items = snapshot()
        candidate = select_issue([i for i in items if i["id"] == self.issue["item_id"]], set())
        if candidate != self.issue:
            raise StartupError("Ready selection changed before claim; retry")
        self.before = ownership(next(i for i in items if i["id"] == self.issue["item_id"]))
        run(
            "gh",
            "issue",
            "edit",
            str(self.issue["number"]),
            "--repo",
            REPOSITORY,
            "--add-label",
            self.agent,
        )
        self.claimed = True
        self.verify("Ready")
        set_status(self.issue, DOING)
        self.active = True
        self.verify("Doing")

    def verify(self, expected="Doing"):
        items = snapshot()
        matches = [i for i in items if i["id"] == self.issue["item_id"]]
        if (
            len(matches) != 1
            or status(matches[0]) != expected
            or (ownership(matches[0]) != self.before | {self.agent})
            or matches[0]["content"]["state"] != "OPEN"
        ):
            raise StartupError("remote claim changed concurrently; review issue ownership/status")

    def rollback(self):
        if not self.claimed:
            print(
                "start-task: claim not acknowledged; review remote state before retrying",
                file=sys.stderr,
            )
            return
        try:
            self.verify("Doing" if self.active else "Ready")
            if self.active:
                set_status(self.issue, READY)
                self.verify("Ready")
            run(
                "gh",
                "issue",
                "edit",
                str(self.issue["number"]),
                "--repo",
                REPOSITORY,
                "--remove-label",
                self.agent,
            )
            print("start-task: rolled back claim and status", file=sys.stderr)
        except (StartupError, OSError, ValueError, KeyError, TypeError):
            print(
                "start-task: preserved uncertain remote state; review issue "
                f"#{self.issue['number']} ownership/status",
                file=sys.stderr,
            )


def provision_secret(path):
    # Reject every symlink component before any creation; never resolve an alias
    # into protected primary/master secrets, even for an existing task credential.
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise StartupError("refusing symlinked development secret path")
    if path.exists():
        if not path.is_file() or path.stat().st_nlink != 1:
            raise StartupError("development secret is not a regular file")
        return False
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stat.S_IMODE(path.parent.stat().st_mode) != 0o700:
        raise StartupError("development secret directory must have mode 0700")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise StartupError("development secret appeared concurrently; retry") from None
    # Keep newly generated credentials on failure: never delete a credential that
    # a concurrent process may already have adopted. Report path, never its value.
    with os.fdopen(descriptor, "w") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(f"POSTGRES_PASSWORD={secrets.token_hex(32)}\n")
        # the app and mcp share it, so the local connector authenticates (make mcp-smoke)
        output.write(f"BSS_SERVICE_SECRET={secrets.token_hex(32)}\n")
        output.flush()
        os.fsync(output.fileno())
    return True


def reservations(trees):
    ports, issues = set(), set()
    for path, branch in trees:
        issues.add(issue_of(branch))
        metadata = path / ".task/runtime.json"
        if metadata.is_symlink() or metadata.parent.is_symlink():
            raise StartupError(f"refusing symlinked runtime metadata: {metadata}")
        try:
            data = json.loads(metadata.read_text(), object_pairs_hook=unique_fields)
        except FileNotFoundError:
            continue
        except (OSError, ValueError, InvalidRuntime):
            raise StartupError(f"cannot safely read runtime reservations: {metadata}") from None
        task, reserved = validate_metadata(data)
        ports.update(reserved)
        issues.add(issue_of(task))
    return ports, issues


@contextlib.contextmanager
def allocate(used):
    for first in range(PORT_START, PORT_END - 1, 3):
        ports = [first, first + 1, first + 2]
        if set(ports) & (used | RESERVED_PORTS):
            continue
        listeners = []
        try:
            for port in ports:
                listener = socket.socket()
                listeners.append(listener)
                listener.bind(("127.0.0.1", port))
        except OSError:
            for listener in listeners:
                listener.close()
            continue
        try:
            yield ports
        finally:
            for listener in listeners:
                listener.close()
        return
    raise StartupError(f"no free three-port block in {PORT_START}–{PORT_END}")


def write_runtime(output, data):
    json.dump(data, output, indent=2)
    output.write("\n")


def rollback(root, branch, destination, base, metadata_created, expected_metadata):
    # Never force removal, and never discard a commit created by a hook/user.
    unchanged = git("rev-parse", f"refs/heads/{branch}", cwd=root) == base
    attached = dict(worktrees(root)).get(destination) == branch
    if unchanged and attached:
        ignored = git(
            "ls-files", "--others", "--ignored", "--exclude-standard", "-z", cwd=destination
        ).split("\0")
        allowed = {"", ".task/runtime.json"} if metadata_created else {""}
        if any(name not in allowed for name in ignored):
            print(
                f"start-task: preserved changed task (ignored files): {destination}",
                file=sys.stderr,
            )
            return
        if metadata_created:
            metadata = destination / ".task/runtime.json"
            try:
                current = metadata.lstat()
                if metadata.is_symlink() or (current.st_dev, current.st_ino) != metadata_created:
                    raise ValueError
                if json.loads(metadata.read_text()) != expected_metadata:
                    raise ValueError
            except (OSError, ValueError):
                print(
                    f"start-task: preserved changed runtime metadata: {metadata}", file=sys.stderr
                )
                return
            metadata.unlink()
        removed = run("git", "worktree", "remove", "--", str(destination), cwd=root, check=False)
        attached = removed.returncode != 0
    if unchanged and not attached:
        removed = run("git", "branch", "-d", "--", branch, cwd=root, check=False)
        if removed.returncode == 0 and not destination.exists():
            print("start-task: rolled back the new worktree and branch", file=sys.stderr)
        else:
            print(
                f"start-task: manual cleanup may be needed: {destination} / {branch}",
                file=sys.stderr,
            )
    else:
        print(
            f"start-task: preserved changed task; review {destination} / {branch}", file=sys.stderr
        )


def create(root, branch, destination, base, ports, report_ready=None):
    """Own the branch before attaching a worktree, so partial Git failures can be recovered."""
    created = False
    metadata = destination / ".task/runtime.json"
    metadata_created = False
    try:
        git("branch", "--no-track", branch, base, cwd=root)
        created = True
        git("worktree", "add", "--", str(destination), branch, cwd=root)
        if git("rev-parse", "HEAD", cwd=destination) != base or git(
            "status", "--porcelain=v1", "--untracked-files=all", cwd=destination
        ):
            raise StartupError("new worktree changed during checkout; preserved for review")
        if metadata.parent.is_symlink():
            raise StartupError("refusing symlinked .task directory")
        metadata.parent.mkdir(exist_ok=True)
        if metadata.exists() or metadata.is_symlink():
            raise StartupError("runtime metadata unexpectedly already exists")
        with metadata.open("x+") as output:
            created_stat = os.fstat(output.fileno())
            metadata_created = (created_stat.st_dev, created_stat.st_ino)
            write_runtime(output, runtime(branch, ports))
            output.flush()
            output.seek(0)
            validate_metadata(
                json.load(output, object_pairs_hook=unique_fields),
                git("symbolic-ref", "--quiet", "--short", "HEAD", cwd=destination),
            )
        if report_ready is not None:
            report_ready()
    except BaseException:
        if created:
            try:
                rollback(root, branch, destination, base, metadata_created, runtime(branch, ports))
            except (StartupError, OSError):
                print(
                    f"start-task: cleanup failed; review {destination} / {branch}", file=sys.stderr
                )
        else:
            print(
                f"start-task: no worktree created; review branch {branch} before retrying",
                file=sys.stderr,
            )
        raise


def recover_slot(root, common, slot):
    # Import lazily: finish imports startup helpers as well. Reuse all completion,
    # ownership, clean-tree, runtime and checkpoint checks, never infer completion
    # merely from a missing repo or an old timestamp. Both locks are already held.
    import task_finish

    if any(k.startswith("GIT_") and k != "GIT_PAGER" for k in os.environ):
        raise SlotError("occupied; unset inherited GIT_* overrides before completion recovery")
    if os.environ.get("GH_HOST", "github.com") != "github.com":
        raise SlotError("occupied; unexpected GitHub host for completion recovery")
    try:
        with contextlib.redirect_stdout(sys.stderr):
            task_finish.finish(root, common, slot)
    except (
        StartupError,
        SlotError,
        InvalidRuntime,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        subprocess.SubprocessError,
    ) as error:
        raise SlotError(
            "occupied; completion recovery not confirmed; "
            "preserved for inspection (make finish-task SLOT=" + slot.name + ")"
        ) from error


def select_slot(root, common, stack):
    explicit = os.environ.get("BSS_SLOT")
    names = [explicit] if explicit is not None else candidates(root)
    failures = []
    for name in names:
        try:
            location = stack.enter_context(
                reserve(
                    root,
                    name,
                    worktrees(root),
                    recover=(lambda slot: recover_slot(root, common, slot))
                    if explicit is None
                    else None,
                    refresh=lambda: worktrees(root),
                )
            )
            return name, location
        except (SlotError, OSError, ValueError) as error:
            failures.append(f"{name}: {error}")
    raise SlotError("no usable worker slot available:\n  " + "\n  ".join(failures))


def start(on_ready=None):
    requested = requested_issue()
    root, common = primary()
    agent = agent_label()
    descriptor = os.open(common / "start-task.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock, contextlib.ExitStack() as stack:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise StartupError("unexpected startup lock")
        # Serialize selection as well as claiming, so a second caller can inspect
        # the next slot/issue after the first publishes its local reservation.
        fcntl.flock(lock, fcntl.LOCK_EX)
        clean(root)
        git("fetch", "--prune", "origin", cwd=root)
        base = git("rev-parse", "--verify", "origin/master", cwd=root)
        no_tracked_secrets(root, base)
        if run(
            "git", "merge-base", "--is-ancestor", "HEAD", base, cwd=root, check=False
        ).returncode:
            raise StartupError(
                "cannot fast-forward master to origin/master; local commits are preserved"
            )
        before = git("rev-parse", "HEAD", cwd=root)
        git("merge", "--ff-only", "origin/master", cwd=root)
        # Nothing is claimed yet: continue with the updated scripts if they changed.
        reexec_if_scripts_changed(root, before)
        clean(root)
        slot_name, slot = select_slot(root, common, stack)
        # Completion recovery can synchronize primary to a newer master.
        base = git("rev-parse", "HEAD", cwd=root)
        trees = worktrees(root)
        used, represented = reservations(trees)
        refs = git("for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes", cwd=root)
        for ref in refs.splitlines():
            name = re.sub(r"^refs/(?:heads/|remotes/[^/]+/)", "", ref)
            represented.add(issue_of(name))
        prs = pages(PR_QUERY, lambda response: response["repository"]["pullRequests"])
        represented.update(issue_of(pr["headRefName"]) for pr in prs)
        items = snapshot()
        issue = select_issue(items, represented, requested)
        branch = branch_name(issue["number"], issue["title"])
        destination = slot / "repo"
        if destination.exists() or destination.is_symlink() or destination in dict(trees):
            raise StartupError(f"worktree path collision: {destination}")
        if (
            run(
                "git",
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{branch}",
                cwd=root,
                check=False,
            ).returncode
            == 0
        ):
            raise StartupError(f"branch collision: {branch}")
        secret = secret_file(branch, trees, destination)
        with allocate(used) as ports:
            result = dict(
                slot=slot_name,
                issue=issue["number"],
                title=issue["title"],
                branch=branch,
                worktree=str(destination),
                app_port=ports[0],
                mcp_port=ports[1],
                web_port=ports[2],
                secret_file=str(secret),
                secret_exists=True,
                agent=agent,
                status="Doing",
            )
            if requested is not None:
                result["requested_issue"] = requested
                result["human_approval"] = requested
            claim = Claim(issue, agent)
            owned_slot = claim_slot(slot, branch) if slot else None
            try:
                claim.acquire()

                def ready():
                    result["secret_created"] = provision_secret(secret)
                    run(
                        sys.executable,
                        "-B",
                        str(destination / "scripts/task_runtime.py"),
                        "--check",
                        cwd=destination,
                    )
                    claim.verify()
                    # An ignored file, written after every check of the new worktree.
                    result["test_selection"] = task_testmon.seed(destination, common)
                    if on_ready is not None:
                        on_ready(result)

                create(root, branch, destination, base, ports, report_ready=ready)
            except BaseException:
                claim.rollback()
                if slot and not undo(slot, owned_slot):
                    print(f"start-task: preserved slot reservation: {slot}", file=sys.stderr)
                if secret.exists():
                    print(
                        f"start-task: preserved development credential: {secret}", file=sys.stderr
                    )
                raise
        return result


def interrupted(_signum, _frame):
    raise StartupError("interrupted")


def print_result(result, json_output):
    record(
        result["branch"],
        "start-task",
        0,
        None,
        slot=result.get("slot"),
        client=result["agent"].removeprefix("agent:"),
    )
    if json_output:
        print(json.dumps(result), flush=True)
    else:
        print(
            f"Slot: {result.get('slot') or 'legacy task directory'}\n"
            f"Issue: #{result['issue']} — {result['title']}\nBranch: {result['branch']}"
            f"\nWorktree: {result['worktree']}\nApp port: {result['app_port']}"
            f"\nMCP port: {result['mcp_port']}\nWeb port: {result['web_port']}"
            f"\nSecret file: {result['secret_file']}"
            f"\nSecret exists: {'yes' if result['secret_exists'] else 'no'}"
            f"\nClaim: {result['agent']} / {result['status']}"
            f"\nSecret created: {'yes' if result['secret_created'] else 'no'}"
            f"\nNext: cd {shlex.quote(result['worktree'])}",
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--release-slot", action="store_true")
    mode.add_argument("--new-priority-task", action="store_true")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.release_slot:
            root, _common = primary()
            release(root, os.environ.get("BSS_SLOT"), worktrees(root))
            print("worker slot released; persistent state preserved")
        elif args.new_priority_task:
            request = new_task_request()
            primary()  # Repository validation only: no fetch, slots or task setup.
            result = create_priority_task(request)
            if args.json:
                print(json.dumps(result), flush=True)
            else:
                print(
                    f"Created #{result['issue']}: {result['url']} (Ready)\n"
                    f"Next: {result['next_command']}",
                    flush=True,
                )
        else:
            start(lambda result: print_result(result, args.json))
    except Reexec as handover:
        return rerun(handover.root, "start_task.py", *sys.argv[1:])
    except (
        StartupError,
        SlotError,
        InvalidRuntime,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        KeyboardInterrupt,
    ) as error:
        # Our own errors carry a vetted message; anything else gets a bounded cause only.
        known = isinstance(error, StartupError | SlotError | InvalidRuntime)
        print(f"start-task: {error if known else diagnostic(error)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
