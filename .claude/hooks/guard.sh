#!/usr/bin/env bash
# PreToolUse guard for Bash commands: the few slips that cannot be undone. Everything else
# is guidance (docs/worktrees.md), not enforcement. A pattern matcher over the raw command
# text, not a security boundary. Exit 2 blocks the call; stderr goes back to the agent.
set -uo pipefail

payload="$(cat)"
cmd="$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null || true)"
[[ -z "$cmd" ]] && exit 0

block() { printf 'BLOCKED by guard.sh: %s\n' "$1" >&2; exit 2; }
has() { grep -Eq -- "$1" <<<"$cmd"; }

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
