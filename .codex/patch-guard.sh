#!/usr/bin/env bash
# Codex apply_patch hook: the Edit/Write rules of .claude/settings.json, which Codex never
# reads. tool_input.command is the patch text; every path in it is checked. Codex has no
# "ask" for hooks, so the "ask" paths are blocked too: the human edits or approves them.
# Exit 2 blocks the patch; anything this script cannot parse is blocked as well.
set -uo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
# the heredoc below is python's stdin, so the payload travels in the environment
PATCH_PAYLOAD="$(cat)" python3 - "$root" <<'PY'
import fnmatch, json, os, posixpath, re, sys

root = sys.argv[1]
DENY = [".env", ".env.prod", ".env.smoke", "design-ref/*"]
ASK = [".claude/*", ".codex/*", "compose*.yaml", "Makefile", "scripts/*"]

def block(reason):
    print(f"BLOCKED by .codex/patch-guard.sh: {reason}", file=sys.stderr)
    sys.exit(2)

try:
    payload = json.loads(os.environ["PATCH_PAYLOAD"])
    patch = payload["tool_input"]["command"]
    cwd = payload.get("cwd") or root
    if not isinstance(patch, str):
        raise TypeError("tool_input.command is not a string")
except Exception as e:
    block(f"cannot read the apply_patch payload ({e}); no edit without the guard")

paths = re.findall(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$",
                   patch, re.M)
if not paths and "*** Begin Patch" in patch:
    block("a patch without any file header the guard can read")
for raw in paths:
    full = posixpath.normpath(posixpath.join(cwd, raw))
    rel = posixpath.relpath(full, root)
    if rel.startswith("../"):
        continue  # outside the project: not ours to police
    if any(fnmatch.fnmatch(rel, p) for p in DENY):
        block(f"{rel} is never edited by an agent (CLAUDE.md: secrets, design reference).")
    if any(fnmatch.fnmatch(rel, p) for p in ASK):
        block(f"{rel} is a harness file and needs the human's OK (CLAUDE.md, 'Agent "
              "guardrails'): show the diff and ask the human to apply or approve it.")
PY
