#!/usr/bin/env python3
"""PreToolUse guard for Bash commands: the few slips that cannot be undone.

Everything else is guidance (docs/worktrees.md), not enforcement. A pattern matcher over
the executable part of the command, not a security boundary. Exit 2 blocks the call;
stderr goes back to the agent. Claude Code runs it as is; Codex runs it with `--codex`
(.codex/hooks.json), which adds the Codex-only rule and fails closed on a payload it
cannot read. Cases: guard-cases.txt and .codex/guard-cases.txt (scripts/test_guard.py).
"""

import json
import re
import sys

SHELLS = r"(?:sh|bash|zsh|dash)"
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
SHELL_WORD = re.compile(rf"(?:^|[\s;&|(]){SHELLS}\b")
SHELL_SCRIPT = re.compile(rf"{SHELLS}\s+-[a-z]*c\s*$")

SECRETS = "do not read .env files. See .env.example for the variable names."
HOOKS = "the pre-commit and pre-push hooks are never skipped."
# (pattern, unless, reason): a line that matches `pattern` blocks, unless some line matches
# `unless`. Matching is per line, as grep did when this was a shell script.
RULES = [
    # --- secrets stay out of the transcript ---
    (
        r"(cat|less|more|head|tail|grep|strings|bat|xxd|od)\s+[^|;&]*"
        r"\.env(\.prod|\.smoke)?([\s\"']|$)",
        None,
        SECRETS,
    ),
    (
        r"docker\s+compose(\s+-f\s+\S+)*\s+config",
        r"config\s+(-q|--quiet)",
        "docker compose config prints every secret; use docker compose config --quiet.",
    ),
    # --- other projects share this host's Docker ---
    (
        r"docker\s+(system\s+prune|volume\s+(rm|prune)|network\s+prune|builder\s+prune)",
        None,
        "destructive docker cleanup is not allowed - other containers share this host.",
    ),
    # --- history on the remote is never rewritten ---
    (
        r"git(\s+-\S+)*\s+push(\s[^;&|]*)?\s(--force|--force-with-lease|--force-if-includes"
        r"|--mirror|--delete|--prune|-[A-Za-z]*[fd][A-Za-z]*|[+:]\S*)([=\s;&|]|$)",
        None,
        "force, delete, mirror and prune pushes are forbidden.",
    ),
    # --- the commit and push hooks are never skipped ---
    (
        r"git\s.*--no-verify"
        r"|git(\s+-\S+)*\s+commit\s([^;&|]*\s)?-[a-zA-Z]*n[a-zA-Z]*(\s|$)",
        None,
        HOOKS,
    ),
    (
        r"git\s+(-\S+\s+)*-c\s*core\.hooksPath="
        r"|git\s+config\s+(--[a-z]+\s+)?core\.hooksPath\s+[^-\s]",
        None,
        HOOKS,
    ),
]
# Codex only: git add/commit/push run through `make commit` and `make push`, never bare
# (the Make targets validate files and run the hooks).
CODEX_GIT = re.compile(
    rf"(?:^|[;&|(]\s*|\bxargs\s+|\bsudo\s+|\b{SHELLS}\s+-[a-z]*c\s+)"
    r"git(?:\s+-\S+(?:\s+\S+)?)*?\s+(?:add|commit|push)\b"
)
CODEX_REASON = (
    "use make commit MESSAGE=... FILES='[...]' and make push; "
    "bare git add/commit/push skip the task validation."
)


def executable(text):
    """The part of a command that runs.

    A command that only *mentions* a blocked pattern - an issue body, a commit message, a
    python heredoc that writes a test - must not be blocked. Dropped before matching:
      - heredoc bodies, except those fed to a shell (those lines run);
      - the content of quoted strings that contain whitespace (prose), except the script
        given to `sh -c` / `bash -c`. Quoted single words (".env", "--force") are kept.
    An unbalanced quote leaves the rest of the text as it is, which only blocks more.
    """
    out, end, keep = [], None, False
    for line in text.split("\n"):
        if end is not None:
            if line.strip() == end:
                end = None
            elif keep:
                out.append(line)
            continue
        out.append(line)
        match = HEREDOC.search(line)
        if match:
            end = match.group(2)
            keep = SHELL_WORD.search(line[: match.start()]) is not None
    text = "\n".join(out)

    result, i, n = [], 0, len(text)
    while i < n:
        char = text[i]
        if char not in "'\"":
            result.append(char)
            i += 1
            continue
        j = i + 1
        while j < n and text[j] != char:
            j += 2 if char == '"' and text[j] == "\\" else 1
        if j >= n:
            result.append(text[i:])
            break
        body = text[i + 1 : j]
        script = SHELL_SCRIPT.search("".join(result)) is not None
        result.append(body if script or not re.search(r"\s", body) else "''")
        i = j + 1
    return "".join(result)


def decide(command, codex=False):
    """The reason this command is blocked, or None."""
    lines = executable(command).split("\n")

    def has(pattern):
        return any(re.search(pattern, line) for line in lines)

    if codex and any(CODEX_GIT.search(line) for line in lines):
        return CODEX_REASON
    for pattern, unless, reason in RULES:
        if has(pattern) and not (unless and has(unless)):
            return reason
    return None


def main(argv):
    codex = "--codex" in argv
    try:
        command = json.load(sys.stdin).get("tool_input", {}).get("command", "")
        if not isinstance(command, str):
            raise TypeError("command is not text")
    except (ValueError, TypeError, AttributeError):
        if codex:
            print("BLOCKED by guard.py: cannot read the hook payload", file=sys.stderr)
            return 2
        return 0
    reason = decide(command, codex) if command else None
    if reason:
        print(f"BLOCKED by guard.py: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
