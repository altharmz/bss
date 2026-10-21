#!/usr/bin/env bash
# Codex apply_patch hook: the Edit deny rules of .claude/settings.json, which Codex never
# reads. tool_input.command is the patch text; every path in it is checked. Exit 2 blocks.
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

for raw in re.findall(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", patch, re.M):
    rel = posixpath.relpath(posixpath.normpath(posixpath.join(cwd, raw)), root)
    if any(fnmatch.fnmatch(rel, p) for p in DENY):
        block(f"{rel} is never edited by an agent (secrets, design reference, the deploy record).")
PY
