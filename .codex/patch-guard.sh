#!/usr/bin/env bash
# Codex apply_patch hook: the Edit deny rules of .claude/settings.json, which Codex never
# reads (keep the two lists the same). tool_input.command is the patch text; every path in
# it is checked. Exit 2 blocks. Cases: patch-guard-cases.txt, run by scripts/test_guard.py.
set -uo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
# the heredoc below is python's stdin, so the payload travels in the environment
PATCH_PAYLOAD="$(cat)" python3 - "$root" <<'PY'
import fnmatch, json, os, posixpath, re, sys

root = sys.argv[1]
DENY = [".env", ".env.prod", ".env.smoke", "design-ref/*", ".deployed"]

def block(reason):
    print(f"BLOCKED by .codex/patch-guard.sh: {reason}", file=sys.stderr)
    sys.exit(2)

try:
    payload = json.loads(os.environ["PATCH_PAYLOAD"])
    patch = payload["tool_input"]["command"]
    cwd = payload.get("cwd") or root
except Exception as e:
    block(f"cannot read the apply_patch payload ({e})")

# The task secrets (~/.config/bss/tasks/<task id>/.env) and the load-test password.
config = os.environ.get("XDG_CONFIG_HOME") or posixpath.join(posixpath.expanduser("~"), ".config")
secrets = posixpath.join(posixpath.normpath(config), "bss")

for raw in re.findall(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", patch, re.M):
    path = posixpath.normpath(posixpath.join(cwd, posixpath.expanduser(raw)))
    rel = posixpath.relpath(path, root)
    if any(fnmatch.fnmatch(rel, p) for p in DENY):
        block(f"{rel} is never edited by an agent (secrets, design reference, the deploy record).")
    if path == secrets or path.startswith(secrets + "/"):
        block("the task secret directory is never edited by an agent.")
PY
