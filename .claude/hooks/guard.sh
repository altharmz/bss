#!/usr/bin/env bash
# PreToolUse guard for Bash commands.
#
# Reads the tool call as JSON on stdin. Exit code 2 blocks the call and feeds the message
# on stderr back to Claude, so the model learns why instead of retrying blindly.
#
# This is defence in depth on top of .claude/settings.json deny rules: it catches the
# shapes a static matcher misses (pipelines, `sh -c`, remote commands over ssh).
set -uo pipefail

payload="$(cat)"
cmd="$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null || true)"
[[ -z "$cmd" ]] && exit 0

block() { printf 'BLOCKED by guard.sh: %s\n' "$1" >&2; exit 2; }

# --- the VPN on the VPS is production; it is not ours to touch ---
grep -Eqi 'softether|vpnserver|vpncmd' <<<"$cmd" && \
  block "the VPS runs a production SoftEther VPN server. Do not interact with it."
grep -Eq '(^|[^0-9])(443|992|1194|500|4500|5555)(:|/|[^0-9]|$)' <<<"$cmd" && \
  grep -Eq 'ssh|docker|-p ' <<<"$cmd" && \
  block "ports 443/992/1194/500/4500/5555 belong to the VPN server on the VPS."

# --- destructive docker operations affect containers we do not own ---
grep -Eq 'docker[[:space:]]+(system[[:space:]]+prune|volume[[:space:]]+rm|volume[[:space:]]+prune|network[[:space:]]+prune|builder[[:space:]]+prune)' <<<"$cmd" && \
  block "destructive docker cleanup is not allowed - other containers share this host."
grep -Eq 'docker[[:space:]]+(stop|rm|kill)[[:space:]]+\$\(docker[[:space:]]+ps' <<<"$cmd" && \
  block "mass container stop/remove is not allowed."

# --- remote access goes through the deploy script only ---
if grep -Eq '(^|[|;&[:space:]])ssh[[:space:]]' <<<"$cmd"; then
  grep -Eq 'sudo|apt|systemctl|rm[[:space:]]+-rf|docker[[:space:]]+build' <<<"$cmd" && \
    block "privileged / build commands must not run on the VPS. Use ./scripts/deploy.sh."
  grep -Eq 'authorized_keys|passwd|mkfs|ufw[[:space:]]|iptables|shutdown|reboot' <<<"$cmd" && \
    block "commands that change host access, firewall or power state are the human's job."
fi

# --- secrets stay out of the transcript ---
grep -Eq '(cat|less|head|tail|grep|strings)[[:space:]]+[^|]*\.env(\.prod)?([[:space:]]|$)' <<<"$cmd" && \
  block "do not read .env files. See .env.example for the variable names."

# --- history rewriting / force pushes ---
grep -Eq 'git[[:space:]]+(push|reset[[:space:]]+--hard|clean[[:space:]]+-[a-z]*f)' <<<"$cmd" && \
  block "pushes and destructive git operations are the human's call."

exit 0
