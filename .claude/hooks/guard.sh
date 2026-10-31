#!/usr/bin/env bash
# PreToolUse guard for Bash commands: the few rules that protect production, other
# people's containers, secrets and the commit gate. A pattern matcher against slips, not
# a security boundary. Exit 2 blocks the call; the message on stderr goes back to Claude.
set -uo pipefail

payload="$(cat)"
cmd="$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null || true)"
[[ -z "$cmd" ]] && exit 0

block() { printf 'BLOCKED by guard.sh: %s\n' "$1" >&2; exit 2; }
has() { grep -Eq -- "$1" <<<"$cmd"; }
cloud=false
[[ "${CLAUDE_CODE_REMOTE:-}" == true ]] && cloud=true

# --- cloud sessions never reach the VPS ---
$cloud && has '(vpn2811\.duckdns\.org|vps-test)' &&
  block "cloud sessions never reach the VPS (docs/cloud.md)."

# --- the VPN on the VPS is production; it is not ours to touch ---
has '(softether|vpnserver|vpncmd)' &&
  block "the VPS runs a production SoftEther VPN server. Do not interact with it."
# A VPN port in a network context (ssh/VPS, docker publish, firewall or socket tool,
# host:port). Filesystem paths are masked first: a number inside a path is not a port.
vpn_port='(^|[^0-9])(443|992|1194|500|4500|5555|8444)(:|/|[^0-9]|$)'
unpathed="$(sed -E "s#(^|[[:space:]'\"=(,{])(~|\.{1,2})?/[^[:space:]'\"\`;|&:,)]*#\1_path_#g" <<<"$cmd")"
if grep -Eq "$vpn_port" <<<"$unpathed"; then
  grep -Eq '(^|[^[:alnum:]_.-])(ssh|vps-test|docker|-p|ufw|iptables|ip6tables|nft|firewall-cmd|ss|netstat|lsof|fuser|nc|ncat|netcat|socat|nmap|telnet|tcpdump|port|ports|dport|sport|listen)([^[:alnum:]_.-]|$)|[[:alnum:]_.-]:(443|992|1194|500|4500|5555|8444)([^0-9]|$)' <<<"$unpathed" &&
    block "ports 443/992/1194/500/4500/5555/8444 belong to the VPN server on the VPS."
fi

# --- other people's containers share these hosts ---
has 'docker[[:space:]]+(system[[:space:]]+prune|volume[[:space:]]+(rm|prune)|network[[:space:]]+prune|builder[[:space:]]+prune)' &&
  block "destructive docker cleanup is not allowed - other containers share this host."
has 'docker[[:space:]]+(stop|rm|kill)[[:space:]]+.*\$\((docker[[:space:]]+ps)' &&
  block "mass container stop/remove is not allowed."

# --- the VPS is changed only through scripts/deploy.sh; nothing is built there ---
if has '(^|[|;&[:space:]])ssh[[:space:]]'; then
  has '(sudo|apt|systemctl|rm[[:space:]]+-rf|docker[[:space:]]+(build|compose[[:space:]]+build)|authorized_keys|passwd|mkfs|shutdown|reboot)' &&
    block "privileged, build or host-level commands must not run on the VPS. Use make deploy / make remote."
fi

# --- secrets stay out of the transcript ---
has '(cat|less|more|head|tail|grep|strings|bat|xxd|od)[[:space:]]+[^|;&]*\.env(\.prod|\.smoke)?([[:space:]"'"'"']|$)' &&
  block "do not read .env files. See .env.example for the variable names."
has 'docker[[:space:]]+compose([[:space:]]+-f[[:space:]]+[^[:space:]]+)*[[:space:]]+config' && ! has 'config[[:space:]]+(-q|--quiet)' &&
  block "docker compose config prints every secret; use docker compose config --quiet."
has 'docker[[:space:]]+compose[[:space:]].*(run|exec)[[:space:]].*[[:space:]](env|printenv)([[:space:]]|$)' &&
  block "printing a container's environment prints its secrets."

# --- the deploy record is written only by scripts/deploy.sh (make deploy) ---
has '(>|(^|[[:space:];&|])(tee|rm|mv|cp|ln|touch|truncate|sed[[:space:]]+-i[^[:space:]]*)[[:space:]])[^;&|]*\.deployed([^[:alnum:]_-]|$)' &&
  block ".deployed is written only by make deploy; read it with make deployed."

# --- the commit gate: tests always run before a commit ---
# (cloud sessions have no Docker, so the hook cannot run there: docs/cloud.md)
if ! $cloud; then
  has 'git[[:space:]].*--no-verify|git([[:space:]]+-[^[:space:]]+)*[[:space:]]+commit[[:space:]]([^;&|]*[[:space:]])?-[a-zA-Z]*n[a-zA-Z]*([[:space:]]|$)' &&
    block "the pre-commit (lint + test) and pre-push hooks are never skipped."
  # Only exempt the exact read-only query, including its literal Python argv form
  # when writing lifecycle source. Other occurrences remain fail-closed. Do not
  # exempt whole heredocs: interpreters can execute commands inside them.
  hook_queries="$(python3 - "$cmd" <<'PY'
import re, sys
text = sys.argv[1]
text = re.sub(r'\bgit\s+config\s+--get(?:-all)?\s+core\.hooksPath(?=\s*(?:$|[;&|]))', 'readonly-hook-query', text)
text = re.sub(r'''(["'])config\1,\s*(["'])--get(?:-all)?\2,\s*(["'])core\.hooksPath\3(?=\s*\])''', 'readonly-hook-query', text)
# The rejection diagnostic in the original blocked source is inert as well.
text = re.sub(r'''(["'])custom core\.hooksPath is unsupported; run make hooks\1''', 'hook-rejection-diagnostic', text)
print(text)
PY
)" || block "cannot safely inspect hook-path configuration."
  grep -Eq 'core\.hooksPath' <<<"$hook_queries" &&
    block "the pre-commit (lint + test) and pre-push hooks are never skipped."
fi

# --- history on the remote is never rewritten; local work is never discarded ---
has 'git[[:space:]]+(reset[[:space:]]+--hard|clean[[:space:]]+-[a-z]*f)' &&
  block "destructive git operations are the human's call."
has 'git([[:space:]]+-[^[:space:]]+)*[[:space:]]+push([[:space:]][^;&|]*)?[[:space:]](--force|--force-with-lease|--force-if-includes|--mirror|--delete|--prune|-[[:alpha:]]*[fd][[:alpha:]]*|[+:][^[:space:]]*)([=[:space:];&|]|$)' &&
  block "force, delete, mirror, prune and +/: refspec pushes are forbidden."
# --- merges go through CI: no --admin override, no merging through the raw API ---
has 'gh[[:space:]]+pr[[:space:]]+merge[[:space:]].*--admin|gh[[:space:]]+api[[:space:]].*pulls/[^[:space:]]*/merge' &&
  block "gh pr merge --admin and API merges bypass the checks; merge only with green CI (docs/board.md)."
if $cloud && has 'git([[:space:]]+-[^[:space:]]+)*[[:space:]]+push([^[:alnum:]_-]|$)'; then
  has 'push[[:space:]]+-u[[:space:]]+origin[[:space:]]+cloud/[^[:space:];&|]+([[:space:]]+[0-9]*>&?[[:space:]]*[^[:space:];&|]+)*[[:space:]]*$' ||
    block "cloud sessions push only as: git push -u origin cloud/<topic>."
fi

# --- Node runs only in the web containers, never on the host (cloud: npm/npx/node ok) ---
node_on_host() {
  python3 - "$cmd" <<'PY'
import os, re, sys
cmd = sys.argv[1]
tools = "pnpm|yarn|corepack" if os.environ.get("CLAUDE_CODE_REMOTE") == "true" else "npm|npx|node|pnpm|yarn|corepack"
tool = rf"({tools})(\s|$)"
starts = re.compile(r"^\s*(\w+=\S*\s+)*" + tool)
shell_c = re.compile(r"\b(sh|bash)\s+-c\s+['\"]?\s*" + tool)
inside = re.compile(r"\bdocker\s+compose\b.*\b(run|exec|up)\b.*\s(web(-offline|-preview)?|e2e)(\s|$)")
for segment in re.split(r"\|\||&&|[;|&\n]", cmd):
    if (starts.search(segment) or shell_c.search(segment)) and not inside.search(segment):
        sys.exit(0)
sys.exit(1)
PY
}
node_on_host &&
  block "npm/npx/node run only inside the web container. Use the make web-* targets."

exit 0
