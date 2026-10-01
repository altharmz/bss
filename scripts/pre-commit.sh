#!/usr/bin/env bash
# Installed as .git/hooks/pre-commit by `make hooks` (symlink, so edits here take effect
# immediately). Runs the same gate as `make verify`'s first two steps before every commit.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

fail() {
  printf '\033[1;31mpre-commit hook failed:\033[0m %s\n' "$*" >&2
  printf 'Fix the issue above, or skip this hook with: git commit --no-verify\n' >&2
  exit 1
}

# </dev/null: docker compose run only allocates a pty when stdin is actually a tty, so
# this keeps the hook from ever blocking on stdin in a non-interactive context (GUI
# clients, editors) as well as a plain terminal commit.
make lint </dev/null || fail "make lint"
make test </dev/null || fail "make test"

# The admin UI, only when the commit touches it or the specs its types come from. These
# targets run offline and never install: if node_modules is out of date they fail with
# "run make web-install". A change in app/api or app/internal alone can still break the
# UI; make verify (web-e2e) is the gate for that.
if git diff --cached --name-only | grep -qE '^(frontend|spec)/'; then
  make web-lint </dev/null || fail "make web-lint"
  make web-test </dev/null || fail "make web-test"
fi
