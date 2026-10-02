#!/usr/bin/env python3
"""Quote-aware command checks for guard.sh.

Usage: guard.py <command>. Exit 0 = allow, 2 = block (the reason on stderr), 3 = the
command cannot be tokenised (unbalanced quotes, a heredoc or here-string): guard.sh then
falls back to its raw-text checks, exactly as strict as before (fail-closed).

The command is tokenised with shlex (POSIX rules, shell punctuation as separate tokens),
so text inside quotes is an argument, never a command: a grep pattern or a commit message
that mentions `npm` or `git push` is not one. What a shell DOES execute is still checked:
`sh -c` / `bash -c` scripts, `eval`, ssh remote commands, a container's command after
`docker compose run <service>`, `xargs`, `find -exec`, and `$(...)` / backtick
substitutions (wherever they appear, quotes included - conservative).

The rules that protect production (the VPN, its ports, mass container stops) still scan
the raw text, quotes included, as before.

This is a pattern matcher protecting against slips, not a security boundary (CLAUDE.md,
"Agent guardrails"). It cannot see through variables, scripts or interpreters.
"""

import os
import posixpath
import re
import shlex
import sys

ALLOW, BLOCK, UNTOKENISABLE = 0, 2, 3
PUNCTUATION = ";&|()<>\n"
REDIRECTS = {">", ">>", ">|", "&>", "&>>"}
NODE_TOOLS = {"npm", "npx", "node", "pnpm", "yarn", "corepack"}
# compose services in which the Node toolchain is allowed (compose.override.yaml)
NODE_SERVICES = {"web", "web-offline", "web-preview", "e2e"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
SECRET_READERS = {"cat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "strings",
                  "bat", "view", "xxd", "od", "nl", "tac"}
PREFIXES = {"sudo", "env", "time", "nohup", "exec", "command", "nice", "builtin"}
MAX_DEPTH = 6

HARNESS_MSG = ("shell writes to .claude/, compose*.yaml, Makefile or scripts/ are not allowed."
               " Use the Edit tool - it asks the human first.")
DESIGN_MSG = "design-ref/ is a read-only reference (the design handoff). Never write to it."


class Blocked(Exception):
    pass


class Untokenisable(Exception):
    pass


def block(reason: str):
    raise Blocked(reason)


# --- raw-text rules: production safety, quotes included (as before) ---

RAW_RULES = [
    (re.compile(r"softether|vpnserver|vpncmd", re.I),
     "the VPS runs a production SoftEther VPN server. Do not interact with it."),
    (re.compile(r"docker\s+(stop|rm|kill)\s+(-\S+\s+)*\$\(docker\s+ps"),
     "mass container stop/remove is not allowed."),
]
PORTS = re.compile(r"(^|[^0-9])(443|992|1194|500|4500|5555)(:|/|[^0-9]|$)")
PORTS_CONTEXT = re.compile(r"ssh|docker|-p ")


def raw_checks(text: str) -> None:
    for pattern, reason in RAW_RULES:
        if pattern.search(text):
            block(reason)
    if PORTS.search(text) and PORTS_CONTEXT.search(text):
        block("ports 443/992/1194/500/4500/5555 belong to the VPN server on the VPS.")


# --- tokens, segments, substitutions ---

def tokenise(text: str) -> list[str]:
    lex = shlex.shlex(text, posix=True, punctuation_chars=PUNCTUATION)
    lex.whitespace = " \t\r"  # a newline separates commands: it is punctuation here
    lex.commenters = ""  # never let '#' hide the rest of a line
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError as exc:  # unbalanced quotes, a trailing escape
        raise Untokenisable(str(exc)) from None
    if any(t.startswith("<<") for t in tokens):  # heredoc / here-string: body is not argv
        raise Untokenisable("heredoc")
    return tokens


def is_operator(token: str) -> bool:
    return token != "" and all(c in ";&|()\n" for c in token)


def segments(tokens: list[str]) -> list[list[str]]:
    out, current = [], []
    for token in tokens:
        if is_operator(token):
            if current:
                out.append(current)
            current = []
        else:
            current.append(token)
    if current:
        out.append(current)
    return out


def substitutions(text: str) -> list[str]:
    """The bodies of $(...) and `...` anywhere in the text - executed by the shell even
    inside double quotes. Single-quoted ones are literal, but are checked too
    (conservative)."""
    found = []
    i = 0
    while True:
        i = text.find("$(", i)
        if i < 0:
            break
        depth, j = 0, i + 1
        while j < len(text):
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            raise Untokenisable("unbalanced $(")
        found.append(text[i + 2 : j])
        i = j
    if text.count("`") % 2:
        raise Untokenisable("unbalanced backtick")
    found += re.findall(r"`([^`]*)`", text)
    return found


# --- paths ---

PROJECT = posixpath.normpath(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
OUTSIDE = object()  # a cwd outside the project


def resolve(cwd, path: str):
    """The path relative to the project root, OUTSIDE if it is not in the project. `cwd`
    is project-relative ('' = root), OUTSIDE, or None (unknown: assume the root, which
    blocks more rather than less)."""
    if path.startswith("/"):
        full = posixpath.normpath(path)
        if full == PROJECT:
            return ""
        if full.startswith(PROJECT + "/"):
            return full[len(PROJECT) + 1 :]
        return OUTSIDE
    if cwd is OUTSIDE:
        return OUTSIDE
    rel = posixpath.normpath(posixpath.join(cwd or "", path))
    if rel == ".":
        return ""
    if rel == ".." or rel.startswith("../"):
        return OUTSIDE
    return rel


def protected(rel) -> str | None:
    if rel is OUTSIDE:
        return None
    if rel == "Makefile" or re.fullmatch(r"compose(\.override|\.prod)?\.yaml", rel):
        return HARNESS_MSG
    for top in (".claude", "scripts"):
        if rel == top or rel.startswith(top + "/"):
            return HARNESS_MSG
    if rel == "design-ref" or rel.startswith("design-ref/"):
        return DESIGN_MSG
    return None


def protected_or_root(cwd, arg: str) -> str | None:
    """For deletions and moves: also the project root itself, and a glob in the root."""
    rel = resolve(cwd, arg)
    if rel == "":
        return HARNESS_MSG
    if rel is not OUTSIDE and any(c in arg for c in "*?[") and "/" not in rel:
        return HARNESS_MSG  # e.g. `rm -rf *` in the project root
    return protected(rel)


def args(words: list[str]) -> list[str]:
    return [w for w in words[1:] if not w.startswith("-")]


# --- the checks ---

def strip_prefixes(words: list[str]) -> list[str]:
    while words:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
            words = words[1:]
        elif words[0] in PREFIXES:
            words = words[1:]
            while words and words[0].startswith("-"):  # sudo -u x, env -i, nice -n 5
                words = words[2:] if words[0] in ("-u", "-g", "-n", "-C") else words[1:]
        else:
            break
    return words


def compose_target(words: list[str]):
    """For `docker compose [global opts] run|exec|up [opts] SERVICE [cmd...]`: (service,
    command words), else None."""
    i = 2
    takes_value = {"-f", "--file", "-p", "--project-name", "--profile", "--env-file",
                   "--project-directory", "--ansi", "--progress", "--parallel"}
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in takes_value else 1
    if i >= len(words) or words[i] not in ("run", "exec", "up", "create", "start"):
        return None
    i += 1
    takes_value = {"-e", "--env", "-v", "--volume", "-w", "--workdir", "-u", "--user",
                   "--name", "--entrypoint", "-p", "--publish", "-l", "--label",
                   "--env-from-file", "--pull", "--cap-add", "--cap-drop", "-i", "--index"}
    while i < len(words) and words[i].startswith("-"):
        i += 1 if ("=" in words[i] or words[i] not in takes_value) else 2
    if i >= len(words):
        return None
    return words[i], words[i + 1 :]


def check(text: str, cwd, inside: bool, depth: int):
    """Check a command line; returns the cwd after it (cd tracking)."""
    if depth > MAX_DEPTH:
        block("command nested too deeply to check")
    raw_checks(text)
    for body in substitutions(text):
        check(body, cwd, False, depth + 1)
    for seg in segments(tokenise(text)):
        cwd = check_segment(seg, cwd, inside, depth)
    return cwd


def check_segment(seg: list[str], cwd, inside: bool, depth: int):
    # redirections anywhere in the segment
    for i, token in enumerate(seg):
        if token in REDIRECTS and i + 1 < len(seg):
            reason = protected(resolve(cwd, seg[i + 1]))
            if reason:
                block(reason)
    words = strip_prefixes([t for t in seg if t not in REDIRECTS and t != "<"])
    if not words:
        return cwd
    cmd = posixpath.basename(words[0])

    if cmd == "cd":
        if len(words) < 2:
            return ""
        target = words[1]
        if any(c in target for c in "$~") or target == "-":
            return None
        return resolve(cwd, target)

    if cmd in NODE_TOOLS and not inside:
        block("npm/npx/node run only inside the web container, never on the host."
              " Use make web-install / web-build / web-lint / web-test / web-dev.")

    if cmd == "crontab":
        block("cron changes are the human's job.")

    if cmd == "git":
        rest = words[1:]
        while rest and rest[0].startswith("-"):
            rest = rest[2:] if rest[0] in ("-C", "-c") else rest[1:]
        sub = rest[0] if rest else ""
        if sub == "push" or (sub == "reset" and "--hard" in rest) or (
            sub == "clean" and any(re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", w) for w in rest)
        ):
            block("pushes and destructive git operations are the human's call.")

    if cmd == "docker":
        rest = [w for w in words[1:] if not w.startswith("-")]
        pair = tuple(rest[:2])
        if pair in {("system", "prune"), ("volume", "rm"), ("volume", "prune"),
                    ("network", "prune"), ("builder", "prune")}:
            block("destructive docker cleanup is not allowed - other containers share this host.")
        if words[1:2] == ["compose"]:
            target = compose_target(words)
            if target and target[1]:
                service, command = target
                check_segment(command, cwd, inside or service in NODE_SERVICES, depth + 1)

    if cmd == "ssh":
        remote = " ".join(words)
        if re.search(r"sudo|apt|systemctl|rm\s+-rf|docker\s+build", remote):
            block("privileged / build commands must not run on the VPS. Use ./scripts/deploy.sh.")
        if re.search(r"authorized_keys|passwd|mkfs|ufw\s|iptables|shutdown|reboot|crontab",
                     remote):
            block("commands that change host access, firewall, cron or power state are the"
                  " human's job.")
        if re.search(r"docker\s+(system\s+prune|volume\s+(rm|prune)|network\s+prune|builder\s+prune)",
                     remote):
            block("destructive docker cleanup is not allowed - other containers share this host.")

    if cmd in SHELLS:
        for i, w in enumerate(words[1:], start=1):
            if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", w) and i + 1 < len(words):
                check(words[i + 1], cwd, inside, depth + 1)
                break

    if cmd == "eval":
        check(" ".join(words[1:]), cwd, inside, depth + 1)

    if cmd == "xargs":
        rest = words[1:]
        while rest and rest[0].startswith("-"):
            rest = rest[2:] if rest[0] in ("-n", "-I", "-P", "-L", "-s", "-d", "-E") else rest[1:]
        if rest:
            check_segment(rest, cwd, inside, depth + 1)

    if cmd == "find":
        for i, w in enumerate(words):
            if w in ("-exec", "-execdir", "-ok") and i + 1 < len(words):
                end = next((j for j in range(i + 1, len(words)) if words[j] in (";", "+")),
                           len(words))
                check_segment(words[i + 1 : end], cwd, inside, depth + 1)

    if cmd in SECRET_READERS:
        if any(re.search(r"(^|/)\.env(\.prod)?$", w) for w in words[1:]):
            block("do not read .env files. See .env.example for the variable names.")

    if cmd in ("rm", "rmdir", "unlink", "mv", "tee", "truncate", "shred"):
        for arg in args(words):
            reason = protected_or_root(cwd, arg) if cmd in ("rm", "rmdir", "mv") else (
                protected(resolve(cwd, arg)))
            if reason:
                block(reason)

    if cmd == "sed" and any(w == "--in-place" or re.fullmatch(r"-[a-zA-Z]*i.*", w)
                            for w in words[1:]):
        for arg in args(words):
            reason = protected(resolve(cwd, arg))
            if reason:
                block(reason)

    if cmd == "cp":
        targets = args(words)
        if targets:
            reason = protected(resolve(cwd, targets[-1]))
            if reason:
                block(reason)

    return cwd


def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        check(text, "", False, 0)
    except Blocked as exc:
        print(f"BLOCKED by guard.sh: {exc}", file=sys.stderr)
        return BLOCK
    except Untokenisable:
        return UNTOKENISABLE
    return ALLOW


if __name__ == "__main__":
    sys.exit(main())
