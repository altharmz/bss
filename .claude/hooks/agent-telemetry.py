#!/usr/bin/env python3
"""PreToolUse (Bash): tell `make` which Claude subagent runs it, for usage attribution.

Claude Code puts `agent_id` in hook input only when a subagent calls the tool. The hook
inserts `BSS_TELEMETRY_AGENT=<id>` right after each `make` that starts a command segment
(`make X` -> `make BSS_TELEMETRY_AGENT=<id> X`). Make exports command-line variables to
its recipes, so `make start-task` binds `<session>:agent:<id>` instead of the parent
session. The command still starts with `make`, so permission rules match as before.

Fail open: any problem means no output, and the command runs unchanged. The main thread
(no agent_id) and commands without `make` are never rewritten. No content is stored.
"""

import json
import re
import sys

AGENT = re.compile(r"[a-z0-9]{1,64}")
SEPARATORS = ";&|(\n"


def segment_starts(command):
    """Offsets of `make` words that start a command segment, outside any quotes.

    Returns None when the command has a heredoc: its body is data (a file being
    written), so nothing in such a command is rewritten.
    """
    starts, quote, at_start, i = [], None, True, 0
    while i < len(command):
        char = command[i]
        if quote:
            if char == "\\" and quote == '"':
                i += 2
                continue
            if char == quote:
                quote = None
        elif char == "\\":
            i += 2
            at_start = False
            continue
        elif char in "'\"":
            quote, at_start = char, False
        elif command.startswith("<<", i):
            return None
        elif char in SEPARATORS:
            at_start = True
        elif char.isspace():
            pass
        else:
            if at_start and command.startswith("make", i):
                after = command[i + 4 : i + 5]
                if after == "" or after.isspace():
                    starts.append(i)
            at_start = False
        i += 1
    return starts


def rewrite(command, agent):
    if "BSS_TELEMETRY_AGENT=" in command:
        return None
    starts = segment_starts(command)
    if not starts:
        return None
    for start in reversed(starts):
        end = start + 4
        command = command[:end] + f" BSS_TELEMETRY_AGENT={agent}" + command[end:]
    return command


def main():
    try:
        payload = json.load(sys.stdin)
        agent = payload.get("agent_id")
        tool_input = payload.get("tool_input") or {}
        command = tool_input.get("command")
        if not isinstance(agent, str) or not AGENT.fullmatch(agent):
            return 0
        if payload.get("tool_name") != "Bash" or not isinstance(command, str):
            return 0
        updated = rewrite(command, agent)
        if updated is None:
            return 0
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": {**tool_input, "command": updated},
            }
        }
        print(json.dumps(output))
    except (ValueError, TypeError, AttributeError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
