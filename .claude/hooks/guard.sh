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

# --- quote-aware checks first (guard.py: shlex tokens, so quoted text is never a command) ---
# 0 = allow, 2 = blocked (reason already on stderr), 3 = cannot tokenise (unbalanced
# quotes, a heredoc): fall through to the raw-text checks below, as strict as before.
# Anything else (python missing, a crash) also falls through: fail-closed.
python3 "$(dirname "$0")/guard.py" "$cmd"
rc=$?
(( rc == 0 )) && exit 0
(( rc == 2 )) && exit 2

# --- raw-text fallback (the guard before it was quote-aware) ---

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
grep -Eq '(cat|less|head|tail|grep|strings)[[:space:]]+[^|]*\.env(\.prod|\.smoke)?([[:space:]]|$)' <<<"$cmd" && \
  block "do not read .env files. See .env.example for the variable names."

# --- history rewriting / force pushes ---
grep -Eq 'git[[:space:]]+(push|reset[[:space:]]+--hard|clean[[:space:]]+-[a-z]*f)' <<<"$cmd" && \
  block "pushes and destructive git operations are the human's call."

# --- the harness is changed through the Edit tool, which asks the human first ---
# Mirrors the "ask" rules in settings.json (.claude/, compose files, Makefile, scripts/):
# a shell write would bypass that prompt. Covers >, >>, tee, sed -i, mv, cp (as the
# destination) and rm. Reading, running and copying *from* these paths stays allowed.
proj="$(printf '%s' "${CLAUDE_PROJECT_DIR:-$PWD}" | sed 's/[][\\.*^$+?(){}|]/\\&/g')"
prot="(\./|${proj}/)?((\.claude|scripts)(/[^[:space:]\"'|;&]*)?|compose(\.override|\.prod)?\.yaml|Makefile)"
b="(^|[|;&(\`[:space:]])"       # a command starts here
a="([^|;&]*[[:space:]\"'=])?"    # its earlier arguments
e="([\"'[:space:]|;&)]|\$)"      # the path token ends here
harness_write() {
  grep -Eq ">[[:space:]]*[\"']?${prot}${e}" <<<"$cmd" && return 0
  grep -Eq "${b}(tee|rm|mv)[[:space:]]${a}${prot}${e}" <<<"$cmd" && return 0
  grep -Eq "${b}sed[[:space:]]([^|;&]*[[:space:]])?(--in-place|-[a-zA-Z]*i)[^|;&]*[[:space:]\"']${prot}${e}" <<<"$cmd" && return 0
  grep -Eq "${b}cp[[:space:]]${a}${prot}[\"']?[[:space:]]*([|;&)]|\$)" <<<"$cmd" && return 0
  return 1
}
harness_write && \
  block "shell writes to .claude/, compose*.yaml, Makefile or scripts/ are not allowed. Use the Edit tool - it asks the human first."

# --- the design handoff is a pristine reference: never written ---
prot="(\./|${proj}/)?design-ref(/[^[:space:]\"'|;&]*)?"
harness_write && \
  block "design-ref/ is a read-only reference (the design handoff). Never write to it."

# --- Node runs only in the web containers, never on the host ---
# A command segment (split at ; && || | & and newlines) that starts with a Node tool -
# directly, after VAR=value assignments, or as `sh -c` / `bash -c` - is blocked unless the
# segment itself runs in the `web` or `web-offline` compose service. Use make web-*.
node_on_host() {
  python3 - "$cmd" <<'PY'
import re, sys
cmd = sys.argv[1]
tool = r"(npm|npx|node|pnpm|yarn|corepack)(\s|$)"
starts = re.compile(r"^\s*(\w+=\S*\s+)*" + tool)
shell_c = re.compile(r"\b(sh|bash)\s+-c\s+['\"]?\s*" + tool)
inside = re.compile(r"\bdocker\s+compose\b.*\b(run|exec|up)\b.*\sweb(-offline)?(\s|$)")
for segment in re.split(r"\|\||&&|[;|&\n]", cmd):
    if (starts.search(segment) or shell_c.search(segment)) and not inside.search(segment):
        sys.exit(0)
sys.exit(1)
PY
}
node_on_host && \
  block "npm/npx/node run only inside the web container, never on the host. Use make web-install / web-build / web-lint / web-test / web-dev."

exit 0
