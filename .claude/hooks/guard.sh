#!/usr/bin/env bash
# PreToolUse guard for Bash commands: the few slips that cannot be undone. Everything else
# is guidance (docs/worktrees.md), not enforcement. A pattern matcher over the executable
# part of the command (quoted prose and data heredocs are ignored, see strip below), not a
# security boundary. Exit 2 blocks the call; stderr goes back to the agent.
set -uo pipefail

payload="$(cat)"
cmd="$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null || true)"
[[ -z "$cmd" ]] && exit 0

# A command that only *mentions* a blocked pattern - an issue body, a commit message, a
# python heredoc that writes a test - must not be blocked. Before matching, drop:
#   - heredoc bodies, except those fed to a shell (those lines run);
#   - the content of quoted strings that contain whitespace (prose), except the script
#     given to `sh -c` / `bash -c`. Quoted single words (".env", "--force") are kept.
# If the quoting cannot be parsed the raw text is matched, which only blocks more.
scan="$(python3 - "$cmd" <<'PY' 2>/dev/null || printf '%s' "$cmd"
import re, sys

text = sys.argv[1]
SHELLS = r"(?:sh|bash|zsh|dash)"

# heredoc bodies
lines, out, end, keep = text.split("\n"), [], None, False
for line in lines:
    if end is not None:
        if line.strip() == end:
            end = None
        elif keep:
            out.append(line)
        continue
    out.append(line)
    match = re.search(r"<<-?\s*(['\"]?)(\w+)\1", line)
    if match:
        end = match.group(2)
        keep = re.search(rf"(?:^|[\s;&|(]){SHELLS}\b", line[: match.start()]) is not None
text = "\n".join(out)

# quoted prose
result, i, n = [], 0, len(text)
while i < n:
    ch = text[i]
    if ch in "'\"":
        j = i + 1
        while j < n and text[j] != ch:
            j += 2 if ch == '"' and text[j] == "\\" else 1
        if j >= n:  # unbalanced: leave the rest alone
            result.append(text[i:])
            break
        body = text[i + 1 : j]
        script = re.search(rf"{SHELLS}\s+-[a-z]*c\s*$", "".join(result)) is not None
        result.append(body if script or not re.search(r"\s", body) else "''")
        i = j + 1
    else:
        result.append(ch)
        i += 1
print("".join(result))
PY
)"

block() { printf 'BLOCKED by guard.sh: %s\n' "$1" >&2; exit 2; }
has() { grep -Eq -- "$1" <<<"$scan"; }

# --- secrets stay out of the transcript ---
has '(cat|less|more|head|tail|grep|strings|bat|xxd|od)[[:space:]]+[^|;&]*\.env(\.prod|\.smoke)?([[:space:]"'"'"']|$)' &&
  block "do not read .env files. See .env.example for the variable names."
has 'docker[[:space:]]+compose([[:space:]]+-f[[:space:]]+[^[:space:]]+)*[[:space:]]+config' && ! has 'config[[:space:]]+(-q|--quiet)' &&
  block "docker compose config prints every secret; use docker compose config --quiet."

# --- other projects share this host's Docker ---
has 'docker[[:space:]]+(system[[:space:]]+prune|volume[[:space:]]+(rm|prune)|network[[:space:]]+prune|builder[[:space:]]+prune)' &&
  block "destructive docker cleanup is not allowed - other containers share this host."

# --- history on the remote is never rewritten ---
has 'git([[:space:]]+-[^[:space:]]+)*[[:space:]]+push([[:space:]][^;&|]*)?[[:space:]](--force|--force-with-lease|--force-if-includes|--mirror|--delete|--prune|-[[:alpha:]]*[fd][[:alpha:]]*|[+:][^[:space:]]*)([=[:space:];&|]|$)' &&
  block "force, delete, mirror and prune pushes are forbidden."

# --- the commit and push hooks are never skipped ---
has 'git[[:space:]].*--no-verify|git([[:space:]]+-[^[:space:]]+)*[[:space:]]+commit[[:space:]]([^;&|]*[[:space:]])?-[a-zA-Z]*n[a-zA-Z]*([[:space:]]|$)' &&
  block "the pre-commit and pre-push hooks are never skipped."
has 'git[[:space:]]+(-[^[:space:]]+[[:space:]]+)*-c[[:space:]]*core\.hooksPath=|git[[:space:]]+config[[:space:]]+(--[a-z]+[[:space:]]+)?core\.hooksPath[[:space:]]+[^-[:space:]]' &&
  block "the pre-commit and pre-push hooks are never skipped."

exit 0
