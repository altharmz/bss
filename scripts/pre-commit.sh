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

# The checks below run on the working tree, so the working tree must be exactly what is
# being committed: no unstaged change to a tracked file, and no untracked file where code
# lives. Otherwise a commit could pass on content it does not contain (a file left out of
# `git add` but present on disk) - or fail on content it does not contain.
if ! git diff --quiet; then
  printf 'Unstaged changes to tracked files:\n' >&2
  git diff --name-only | sed 's/^/  /' >&2
  fail "the commit would not contain what the checks test - stage them (git add) or set them aside (git stash push --keep-index)"
fi
untracked="$(git ls-files --others --exclude-standard -- app tests frontend spec)"
if [[ -n "$untracked" ]]; then
  printf 'Untracked files under app/, tests/, frontend/ or spec/:\n' >&2
  printf '%s\n' "$untracked" | sed 's/^/  /' >&2
  fail "the checks would see files the commit does not contain - add them, delete them, or ignore them"
fi

# </dev/null: docker compose run only allocates a pty when stdin is actually a tty, so
# this keeps the hook from ever blocking on stdin in a non-interactive context (GUI
# clients, editors) as well as a plain terminal commit.
make lint </dev/null || fail "make lint"
make test </dev/null || fail "make test"

# The agent guard, only when the commit touches .claude/: every regression case in
# .claude/hooks/guard-cases.txt must still be allowed or blocked as recorded.
if git diff --cached --name-only | grep -q '^\.claude/'; then
  .claude/hooks/test-guard.sh </dev/null || fail "guard regression cases (.claude/hooks/test-guard.sh)"
fi

# The admin UI, only when the commit touches it or the specs its types come from. These
# targets run offline and never install: if node_modules is out of date they fail with
# "run make web-install". A change in app/api or app/internal alone can still break the
# UI; make verify (web-e2e) is the gate for that.
if git diff --cached --name-only | grep -qE '^(frontend|spec)/'; then
  make web-lint </dev/null || fail "make web-lint"
  make web-test </dev/null || fail "make web-test"
fi
