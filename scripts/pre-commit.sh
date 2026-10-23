#!/usr/bin/env bash
# Installed as .git/hooks/pre-commit by `make hooks` (symlink, so edits here take effect
# immediately). The one local test run of a development cycle: lint + test before every
# commit (the guard blocks --no-verify). Deploying does not test again.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# Every run keeps its full output in the git directory (never tracked, no .gitignore
# needed; in a worktree, that worktree's own git dir), one file per run, the last
# KEEP_LOGS kept. A failure can always be read afterwards, whatever the caller kept.
KEEP_LOGS=10
logdir="$(git rev-parse --absolute-git-dir)/pre-commit-logs"
mkdir -p "$logdir"
log="${logdir}/pre-commit-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
printf 'pre-commit %s  HEAD %s\nstaged:\n%s\n\n' "$(date -u +%FT%TZ)" \
  "$(git rev-parse --short HEAD 2>/dev/null || echo none)" \
  "$(git diff --cached --name-only | sed 's/^/  /')" >"$log"
find "$logdir" -maxdepth 1 -name 'pre-commit-*.log' | sort | head -n -"$KEEP_LOGS" | xargs -r rm -f --

fail() {
  {
    printf '\033[1;31mpre-commit hook failed:\033[0m %s\n' "$*"
    printf 'Full log: %s\n' "$log"
  } | tee -a "$log" >&2
  exit 1
}

# run CMD...: stdin from /dev/null, output to the terminal and the log; returns CMD's own
# exit code (PIPESTATUS[0]), not tee's.
run() {
  "$@" </dev/null 2>&1 | tee -a "$log"
  return "${PIPESTATUS[0]}"
}

# The checks below run on the working tree, so the working tree must be exactly what is
# being committed: no unstaged change to a tracked file, and no untracked file where code
# lives. Otherwise a commit could pass on content it does not contain (a file left out of
# `git add` but present on disk) - or fail on content it does not contain.
if ! git diff --quiet; then
  { printf 'Unstaged changes to tracked files:\n'; git diff --name-only | sed 's/^/  /'; } |
    tee -a "$log" >&2
  fail "the commit would not contain what the checks test - stage them (git add) or set them aside (git stash push --keep-index)"
fi
untracked="$(git ls-files --others --exclude-standard -- app tests frontend spec)"
if [[ -n "$untracked" ]]; then
  { printf 'Untracked files under app/, tests/, frontend/ or spec/:\n'
    printf '%s\n' "$untracked" | sed 's/^/  /'; } | tee -a "$log" >&2
  fail "the checks would see files the commit does not contain - add them, delete them, or ignore them"
fi

staged="$(git diff --cached --name-only)"

# Markdown only: nothing to lint or test. Not spec/ (tests read spec/README.md) or
# scripts/docs/ (the API notes are sources of the generated docs).
if [[ -n "$staged" ]] && ! grep -vE '\.md$' <<<"$staged" | grep -q . &&
  ! grep -qE '^(spec|scripts/docs)/' <<<"$staged"; then
  echo "pre-commit: Markdown only - lint and tests skipped" | tee -a "$log"
  exit 0
fi

# One run at a time on this machine: two suites side by side (two sessions committing at
# once, 2026-10-23) took 12.5 minutes instead of 5 and failed the timing-sensitive
# worker tests. The lock is in the common git directory, so every worktree shares it, and
# it is released when the hook and the checks it started end, killed or not. 30 minutes
# at most: a run never takes that long, so a holder that old is stuck.
exec 9>>"$(git rev-parse --path-format=absolute --git-common-dir)/pre-commit.lock"
if ! flock -n 9; then
  echo "pre-commit: another commit's checks are running - waiting for them" | tee -a "$log"
  flock -w 1800 9 || fail "another commit's checks still hold the lock after 30 minutes"
fi

# run gives every check </dev/null: docker compose run only allocates a pty when stdin is
# actually a tty, so this keeps the hook from ever blocking on stdin in a non-interactive
# context (GUI clients, editors) as well as a plain terminal commit.
run make lint || fail "make lint"
run make test || fail "make test"

# The generated docs/api and docs/db (~7 s), when their sources change: regenerated here,
# so a commit that leaves them stale is refused with the files to stage.
if grep -qE '^(app|alembic|spec|scripts/docs)/|^tests/docs_collect' <<<"$staged"; then
  run make docs-check || fail "generated docs are out of date - git add docs/api docs/db and commit again"
fi

# The agent guard's cases, only when the commit touches .claude/.
if grep -q '^\.claude/' <<<"$staged"; then
  run .claude/hooks/test-guard.sh || fail "guard regression cases (.claude/hooks/test-guard.sh)"
fi

# The admin UI, only when the commit touches it or the specs its types come from. These
# targets run offline and never install: if node_modules is out of date they fail with
# "run make web-install". A change in app/api or app/internal alone can still break the
# UI; make check (web-e2e) is the gate for that, on demand.
if grep -qE '^(frontend|spec)/' <<<"$staged"; then
  run make web-lint || fail "make web-lint"
  run make web-test || fail "make web-test"
fi
