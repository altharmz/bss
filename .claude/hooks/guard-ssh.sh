#!/bin/bash
# Second layer under settings.json deny rules: inspects every Bash command
# before execution and blocks dangerous ones aimed at the VPS.
set -euo pipefail

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

case "$COMMAND" in
  *vps-test*) ;;
  *) exit 0 ;;
esac

if echo "$COMMAND" | grep -Eiq 'sudo|rm -rf|mkfs|ufw |iptables|authorized_keys|passwd|shutdown|reboot|>[[:space:]]*/dev/'; then
  jq -n --arg r "Blocked by guard-ssh.sh: command against vps-test matched a dangerous pattern. Run it manually if intentional." \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
fi
exit 0
