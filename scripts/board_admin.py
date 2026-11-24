#!/usr/bin/env python3
"""Board administration: show the board, add an issue, move, reprioritize or reorder one.

Doing and Done stay owned by start-task and finish-task; priority and order changes need
HUMAN_APPROVAL=<issue>, which records the agent's assertion that the human approved and
cannot be verified here. No telemetry: these operations belong to no task.
"""

import json
import os
import re
import sys

from task_board import (
    DOING,
    INBOX,
    PRIORITIES,
    PROJECT,
    READY,
    WAITING,
    add_to_board,
    board_item,
    ownership,
    priority,
    query,
    set_status,
    settled_snapshot,
    snapshot,
    status,
    wait_for_board_item,
    write_priority,
)
from task_repo import StartupError, body_from_file, run
from task_runtime import diagnostic

REPOSITORY = "altharmz/bss"
OPERATIONS = ("board", "add-task", "move-task", "set-priority", "reorder-task")
STATUS_ORDER = ("Inbox", "Ready", "Doing", "Waiting")
OPTIONS = {"Inbox": INBOX, "Ready": READY, "Waiting": WAITING, "Doing": DOING}


def env(name):
    return os.environ.get(f"BSS_BOARD_{name}", "")


def issue_number(value, name="ISSUE"):
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise StartupError(f"{name} must be an issue number")
    return int(value)


def approved(operation, number):
    if env("APPROVAL") != str(number):
        raise StartupError(
            f"{operation} needs HUMAN_APPROVAL=<issue number>: "
            "the human must approve this change in the conversation"
        )


def shown(name):
    return name or "no status"


def item_of(number):
    """The issue's board item, added to the board when it is missing."""
    item = board_item(number)
    if item is None:
        add_to_board(number)
        item = wait_for_board_item(number, 5)
    if item is None:
        raise StartupError(f"#{number} is not on the BSS board")
    return item


def confirmed(number, check):
    items = settled_snapshot()
    if not check(items):
        raise StartupError(f"#{number}: the change was not confirmed on the board")


def confirm_status(number, item_id, name):
    confirmed(
        number,
        lambda items: any(item["id"] == item_id and status(item) == name for item in items),
    )


def view(number):
    data = json.loads(
        run(
            "gh",
            "issue",
            "view",
            str(number),
            "--repo",
            REPOSITORY,
            "--json",
            "number,state,labels",
        ).stdout
    )
    if data["number"] != number:
        raise StartupError(f"#{number}: unexpected issue data from GitHub")
    if data["state"] != "OPEN":
        raise StartupError(f"#{number} is closed")
    return {label["name"] for label in data["labels"]}


def board():
    groups = {}
    for position, item in enumerate(snapshot(), start=1):
        content = item.get("content") or {}
        if content.get("number") is None or status(item) == "Done":
            continue
        groups.setdefault(status(item), []).append(
            {
                "issue": content["number"],
                "item_id": item["id"],
                "status": status(item),
                "priority": priority(item),
                "labels": sorted(ownership(item)),
                "title": content["title"],
                "position": position,
            }
        )
    others = sorted(name for name in groups if name is not None and name not in STATUS_ORDER)
    order = [name for name in (*STATUS_ORDER, *others, None) if name in groups]
    rows = [row for name in order for row in groups[name]]
    lines = []
    for name in order:
        lines.append(f"{name or 'No status'}: {len(groups[name])}")
        for row in groups[name]:
            labels = ",".join(row["labels"])
            lines.append(f"  #{row['issue']} {row['priority'] or '--'} [{labels}] {row['title']}")
    return {"items": rows}, "\n".join(lines) or "board: no open items"


def add_task():
    title = env("TITLE").strip()
    body, body_file = env("BODY"), env("BODY_FILE")
    if not title:
        raise StartupError("add-task requires a non-empty TITLE")
    if bool(body) == bool(body_file):
        raise StartupError("add-task requires exactly one of BODY and BODY_FILE")
    if body_file:
        body = body_from_file(body_file)
    if not body.strip():
        raise StartupError("add-task requires a non-empty body")
    labels = list(dict.fromkeys(s.strip() for s in env("LABELS").split(",") if s.strip()))
    if any(label in PRIORITIES or label.startswith("agent:") for label in labels):
        raise StartupError("LABELS must not contain agent labels or P0-P3 (use PRIORITY=)")
    wanted = env("PRIORITY")
    if wanted == "P0":
        raise StartupError("P0 issues are created with make new-priority-task")
    if wanted not in ("", "P1", "P2", "P3"):
        raise StartupError("PRIORITY must be P1, P2 or P3")
    target = env("STATUS") or "Inbox"
    if target not in ("Inbox", "Ready"):
        raise StartupError("STATUS must be Inbox or Ready")
    args = ["gh", "issue", "create", "--repo", REPOSITORY, "--title", title, "--body", body]
    for label in labels:
        args.extend(["--label", label])
    url = run(*args).stdout.strip()
    match = re.fullmatch(r"https://github\.com/altharmz/bss/issues/([1-9][0-9]*)", url)
    if not match:
        raise StartupError(
            "issue creation returned an unexpected URL; review GitHub before retrying"
        )
    number = int(match[1])
    try:
        item = wait_for_board_item(number, float(os.environ.get("BSS_BOARD_ADD_WAIT", "20")))
        if item is None:
            add_to_board(number)
            item = wait_for_board_item(number, 5)
        if item is None:
            raise StartupError(f"#{number} is not on the BSS board")
        if wanted:
            write_priority({"item_id": item["id"]}, wanted)
        set_status({"item_id": item["id"]}, OPTIONS[target])
        confirmed(
            number,
            lambda items: any(
                row["id"] == item["id"]
                and status(row) == target
                and priority(row) == (wanted or None)
                for row in items
            ),
        )
    except (StartupError, OSError, ValueError, KeyError, TypeError) as error:
        cause = error if isinstance(error, StartupError) else diagnostic(error)
        finish = f"make move-task ISSUE={number} STATUS={target}"
        if wanted:
            finish += f" and make set-priority ISSUE={number} PRIORITY={wanted}"
        raise StartupError(
            f"issue #{number} was created but not placed: {cause}; finish with: {finish}"
        ) from None
    result = {"issue": number, "url": url, "title": title, "status": target}
    if wanted:
        result["priority"] = wanted
    return result, f"Created #{number}: {url} ({target})"


def move_allowed(number, current, target, labels):
    if target in ("Inbox", "Ready"):
        return current in ("Inbox", "Ready", "Waiting", None)
    if target == "Waiting":
        return current in ("Inbox", "Ready", "Doing", None)
    if current != "Waiting":
        raise StartupError("Doing is set by make start-task")
    if not any(s.startswith("agent:") for s in labels) or "needs-human" in labels:
        raise StartupError(
            f"#{number}: cannot move from Waiting to Doing "
            "(needs an agent: label and no needs-human label)"
        )
    return True


def move_task():
    number = issue_number(env("ISSUE"))
    target = env("STATUS")
    if target == "Done":
        raise StartupError("Done is set by closing the issue or by make finish-task")
    if target not in OPTIONS:
        raise StartupError("STATUS must be Inbox, Ready, Waiting or Doing")
    waiting_on, unblocks_when = env("WAITING_ON").strip(), env("UNBLOCKS_WHEN").strip()
    if target == "Waiting" and not (waiting_on and unblocks_when):
        raise StartupError("STATUS=Waiting needs WAITING_ON and UNBLOCKS_WHEN")
    labels = view(number)
    item = item_of(number)
    current = status(item)
    result = {"issue": number, "previous": current, "status": target}
    line = f"#{number}: {shown(current)} -> {target}"
    if current == target:
        return result, line
    if not move_allowed(number, current, target, labels):
        raise StartupError(f"#{number}: cannot move from {shown(current)} to {target}")
    if target == "Waiting":
        run(
            "gh",
            "issue",
            "comment",
            str(number),
            "--repo",
            REPOSITORY,
            "--body",
            f"Waiting on: {waiting_on} — unblocks when: {unblocks_when}",
        )
    set_status({"item_id": item["id"]}, OPTIONS[target])
    confirm_status(number, item["id"], target)
    return result, line


def set_priority():
    number = issue_number(env("ISSUE"))
    wanted = env("PRIORITY")
    if wanted not in PRIORITIES:
        raise StartupError("PRIORITY must be P0, P1, P2 or P3")
    approved("set-priority", number)
    view(number)
    item = item_of(number)
    previous = priority(item)
    if previous != wanted:
        write_priority({"item_id": item["id"]}, wanted)
        confirmed(
            number,
            lambda items: any(row["id"] == item["id"] and priority(row) == wanted for row in items),
        )
    result = {"issue": number, "previous": previous, "priority": wanted}
    return result, f"#{number}: priority {previous or 'none'} -> {wanted}"


def reorder_task():
    number = issue_number(env("ISSUE"))
    approved("reorder-task", number)
    after = issue_number(env("AFTER"), "AFTER") if env("AFTER") else None
    if after == number:
        raise StartupError("AFTER must be a different issue than ISSUE")
    item = item_of(number)
    anchor = None
    if after is not None:
        anchor = board_item(after)
        if anchor is None:
            raise StartupError(f"#{after} is not on the BSS board")
    query(
        """mutation($project: ID!, $item: ID!, $after: ID) {
          updateProjectV2ItemPosition(input: {projectId: $project, itemId: $item,
            afterId: $after}) { clientMutationId }
        }""",
        project=PROJECT,
        item=item["id"],
        after=anchor["id"] if anchor else None,
    )

    def placed(items):
        ids = [row["id"] for row in items]
        if item["id"] not in ids:
            return False
        index = ids.index(item["id"])
        return index == 0 if anchor is None else index > 0 and ids[index - 1] == anchor["id"]

    confirmed(number, placed)
    line = (
        f"#{number}: now first on the board" if after is None else f"#{number}: now after #{after}"
    )
    return {"issue": number, "after": after}, line


def main():
    operation = sys.argv[1] if len(sys.argv) == 2 else None
    if operation not in OPERATIONS:
        print(f"usage: board_admin.py {{{','.join(OPERATIONS)}}}", file=sys.stderr)
        return 2
    try:
        if os.environ.get("GH_HOST", "github.com") != "github.com":
            raise StartupError("unexpected GitHub host")
        handler = {
            "board": board,
            "add-task": add_task,
            "move-task": move_task,
            "set-priority": set_priority,
            "reorder-task": reorder_task,
        }[operation]
        result, line = handler()
    except StartupError as error:
        print(f"{operation}: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"{operation}: {diagnostic(error)}", file=sys.stderr)
        return 1
    print(json.dumps(result) if env("JSON") == "1" else line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
