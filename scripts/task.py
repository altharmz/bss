"""One entry point for the lifecycle make targets: python3 -B scripts/task.py <target>.

It knows every lifecycle target, the script that runs it and the make variables it
accepts. Values keep travelling as exported environment variables (pasting make variables
into a recipe breaks on quotes); the Makefile passes only the names of the variables given
on the make command line (BSS_MAKE_VARS), so a misspelled one fails instead of being
ignored. Variables from the process environment are not checked.
"""

import importlib
import importlib.util
import os
import sys


def _json_flag():
    return ["--json"] if os.environ.get("JSON") == "1" else []


# target: (module, arguments, variables allowed on the make command line)
TARGETS = {
    "start-task": (
        "start_task",
        _json_flag,
        {"BSS_TASK_OWNER", "ISSUE", "HUMAN_APPROVAL", "JSON", "SLOT"},
    ),
    "new-priority-task": (
        "start_task",
        lambda: ["--new-priority-task", *_json_flag()],
        {"TITLE", "BODY", "BODY_FILE", "ISSUE", "LABELS", "JSON"},
    ),
    "release-slot": ("start_task", ["--release-slot"], {"SLOT"}),
    "commit": (
        "task_git",
        ["commit"],
        {"MESSAGE", "MESSAGE_FILE", "FILES", "FILES_FILE", "DRY_RUN"},
    ),
    "push": ("task_git", ["push"], set()),
    "sync-task": ("task_git", ["sync-task"], set()),
    "create-pr": ("task_pr", ["create-pr"], {"PR_TITLE", "PR_BODY_FILE"}),
    "check-pr": ("task_pr", ["check-pr"], {"HUMAN_APPROVAL", "WAIT"}),
    "merge-task": ("task_finish", ["merge-task"], {"HUMAN_APPROVAL"}),
    "finish-task": ("task_finish", ["finish-task"], {"SLOT"}),
    "clean-tasks": ("task_cleanup", [], {"CONFIRM"}),
    "format": ("task_quality", ["format"], set()),
    "review-task": ("task_quality", ["review-task"], set()),
    "show-task": ("task_quality", ["show-task"], set()),
    "check": ("task_quality", ["check"], {"HYPOTHESIS_PROFILE"}),
    # The board targets run scripts/board_admin.py (task_board.py is the board library).
    "board": ("board_admin", ["board"], {"JSON"}),
    "add-task": (
        "board_admin",
        ["add-task"],
        {"TITLE", "BODY", "BODY_FILE", "LABELS", "PRIORITY", "STATUS", "JSON"},
    ),
    "move-task": (
        "board_admin",
        ["move-task"],
        {"ISSUE", "STATUS", "WAITING_ON", "UNBLOCKS_WHEN", "JSON"},
    ),
    "set-priority": (
        "board_admin",
        ["set-priority"],
        {"ISSUE", "PRIORITY", "HUMAN_APPROVAL", "JSON"},
    ),
    "reorder-task": (
        "board_admin",
        ["reorder-task"],
        {"ISSUE", "AFTER", "HUMAN_APPROVAL", "JSON"},
    ),
}

# Allowed for every target, besides any BSS_* name (internal knobs, BSS_TELEMETRY_AGENT).
COMMON = {"VERBOSE"}


def arguments(target):
    args = TARGETS[target][1]
    return list(args() if callable(args) else args)


def unknown_variables(target):
    """The command-line variable names that `target` does not accept, sorted."""
    allowed = TARGETS[target][2] | COMMON
    given = os.environ.get("BSS_MAKE_VARS", "").split()
    return sorted({n for n in given if n not in allowed and not n.startswith("BSS_")})


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in TARGETS:
        print(f"task: unknown target '{' '.join(argv)}'", file=sys.stderr)
        return 1
    target = argv[0]
    unknown = unknown_variables(target)
    if unknown:
        allowed = " ".join(sorted(TARGETS[target][2] | COMMON))
        noun = "variable" if len(unknown) == 1 else "variables"
        print(
            f"make {target}: unknown {noun} {', '.join(unknown)}; allowed: {allowed}",
            file=sys.stderr,
        )
        return 1
    name = TARGETS[target][0]
    # Modules read sys.argv as if they were run directly (task_runtime.rerun does that).
    sys.argv = [importlib.util.find_spec(name).origin, *arguments(target)]
    return importlib.import_module(name).main()


if __name__ == "__main__":
    sys.exit(main())
