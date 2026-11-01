"""Recognize direct lifecycle mutations in shell tool calls, without executing them.

This is a guard against ordinary workflow slips, not an interpreter/security boundary.
Repository Make recipes and their children do not pass through shell tool hooks.
"""

import re
import shlex
import sys
from pathlib import PurePath


def violation(command):
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return "cannot inspect shell syntax; use a plain documented make target"
    # Inspect executable positions, including command substitutions and shell wrappers.
    segments = []
    current = []
    for token in tokens + [";"]:
        if token and all(c in ";&|()<>\n" for c in token):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    for words in segments:
        while words:
            if re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", words[0]):
                words = words[1:]
            elif PurePath(words[0]).name in {"env", "command", "exec", "sudo"}:
                wrapper = PurePath(words.pop(0)).name
                while words and words[0].startswith("-"):
                    option = words.pop(0)
                    if words and (
                        (wrapper == "env" and option in {"-u", "--unset", "-C", "--chdir"})
                        or (wrapper == "sudo" and option in {"-u", "-g"})
                    ):
                        words.pop(0)
            else:
                break
        if not words:
            continue
        tool = PurePath(words[0]).name
        args = words[1:]
        shell_options = {"-c", "-lc", "-ic", "-lic"}
        if tool in {"sh", "bash", "dash"} and shell_options.intersection(args):
            index = next(i for i, arg in enumerate(args) if arg in shell_options) + 1
            if index < len(args) and (reason := violation(args[index])):
                return reason
        if tool == "git":
            # Global option values are skipped before locating the subcommand.
            while args and args[0].startswith("-"):
                option = args.pop(0)
                if option in {"-C", "-c", "--git-dir", "--work-tree", "--namespace"} and args:
                    args.pop(0)
            if not args:
                continue
            sub, *rest = args
            targets = {
                "add": "commit", "commit": "commit", "push": "push",
                "merge": "sync-task / merge-task",
                "fetch": "sync-task / start-task / push / finish-task",
                "pull": "sync-task / start-task / finish-task",
                "switch": "start-task", "checkout": "start-task",
                "reset": "review-task (inspect preserved state)",
                "restore": "review-task (inspect preserved state)",
                "clean": "finish-task", "update-ref": "start-task / finish-task",
                "cherry-pick": "review-task; follow documented recovery policy",
                "rebase": "sync-task (published history is preserved)",
                "stash": "review-task (inspect preserved state)",
            }
            if sub == "stash" and rest and rest[0] in {"list", "show"}:
                continue
            if sub == "branch":
                listing = any(a in {
                    "--list", "--show-current", "--contains", "--no-contains", "--merged",
                    "--no-merged", "-a", "--all", "-r", "--remotes",
                } for a in rest)
                mutating = any(a in {
                    "-d", "-D", "-m", "-M", "-c", "-C", "--delete", "--move", "--copy",
                } for a in rest)
                if mutating or (not listing and any(not a.startswith("-") for a in rest)):
                    return "use make finish-task or make start-task for task branches"
            if sub == "worktree" and rest and rest[0] not in {"list"}:
                return "use make start-task / finish-task / release-slot for worktrees"
            if sub in targets:
                return "use make " + targets[sub]
        if tool == "gh":
            # Read-only gh commands remain available, including API GET/GraphQL queries.
            for noun, verb, target in (("pr", "create", "create-pr"), ("pr", "merge", "merge-task")):
                if len(args) >= 2 and args[:2] == [noun, verb]:
                    return "use make " + target
            if args[:2] == ["issue", "edit"] and any("agent:" in a for a in args):
                return "use make start-task for task ownership"
            if args[:2] == ["project", "item-edit"] and any(
                a == "47fc9ee4" or a.endswith("=47fc9ee4") for a in args
            ):
                return "use make start-task for the Doing transition"
            if args and args[0] == "api":
                method = None
                for index, arg in enumerate(args):
                    if arg in {"-X", "--method"} and index + 1 < len(args):
                        method = args[index + 1].upper()
                    elif arg.startswith("--method="):
                        method = arg.split("=", 1)[1].upper()
                    elif arg.startswith("-X") and len(arg) > 2:
                        method = arg[2:].upper()
                fields = any(
                    a in {"-f", "-F", "--field", "--raw-field", "--input"}
                    or a.startswith(("--field=", "--raw-field=", "--input="))
                    or (a.startswith(("-f", "-F")) and len(a) > 2)
                    for a in args
                )
                graphql = "graphql" in args
                mutation = any(re.search(r"\bmutation\b", a) for a in args)
                # gh switches REST calls to POST when request fields are supplied.
                # Inline GraphQL queries remain read-only despite transport POST;
                # file-backed GraphQL bodies cannot be inspected without reading files.
                opaque = graphql and any(
                    a.startswith(("query=@", "--input")) or a == "--input" for a in args
                )
                writing = method not in {None, "GET", "HEAD", "OPTIONS"}
                if graphql:
                    writing = mutation or opaque
                elif method is None:
                    writing = fields
                if writing:
                    return "use the documented make lifecycle target for GitHub mutations"
        if tool == "docker" and any(a in {"compose", "run", "start", "stop", "restart", "rm", "kill", "build", "exec"} for a in args):
            if "compose" in args and any(a in {"ps", "logs", "images", "version"} for a in args):
                continue
            if "compose" in args and "config" in args and "--quiet" in args:
                continue
            return "use make up / down / logs / ps or another documented runtime target"
        if tool == "task-compose" and args and args[0] in {"--check", "--url", "ps", "logs", "port"}:
            continue
        helpers = {"start_task.py", "task_git.py", "task_pr.py", "task_finish.py", "task_quality.py"}
        if tool == "task-compose" or (
            tool.startswith("python") and any(PurePath(a).name in helpers for a in args)
        ):
            return "use the documented make target instead of invoking lifecycle helpers directly"
    return None


if __name__ == "__main__":
    reason = violation(sys.argv[1])
    if reason:
        print("Normal workflow mutation is Make-owned: " + reason, file=sys.stderr)
        sys.exit(2)
