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
