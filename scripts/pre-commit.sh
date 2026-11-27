#!/usr/bin/env bash
# Invoked by the shared worktree dispatcher installed by `make hooks`.
# The one local test run of a development cycle: lint + tests before every
# commit (the guard blocks --no-verify): an import check, lint, the smoke tests and the tests
# the change affects, not the full suite - none of the suites when a passing full run is
# recorded for exactly the staged content (#476). Deploying does not test again.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if [[ "$(git rev-parse --absolute-git-dir)" == "$(git rev-parse --path-format=absolute --git-common-dir)" ]]; then
  echo 'pre-commit: primary checkout is administration-only; commit in a linked task worktree' >&2
  exit 1
fi

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
check_tree() {
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
  # What the checks below are chosen from (scripts/hook_selection.py paths): the staged
  # paths, or on a merge commit the task's own side only. What merely comes in from master
  # passed the hook and CI there; a sync merge once ran the whole backend suite and both
  # script suites for it, over 10 minutes, on a task that touched none of that (#366).
  selected="$(python3 -B "$selection" paths 2>&1)" ||
    fail "scripts/hook_selection.py paths: ${selected}"
}
selection="$(dirname "${BASH_SOURCE[0]}")/hook_selection.py"
full_suite="$(dirname "${BASH_SOURCE[0]}")/full_suite.py"
check_tree
if merging="$(git rev-parse -q --verify --short MERGE_HEAD)"; then
  printf 'selection: merge with %s, %s incoming paths left out\n' "$merging" \
    "$(comm -23 <(git diff --cached --name-only | sort) <(sort <<<"$selected") | wc -l)" |
    tee -a "$log"
fi

# Markdown links and the commands the agent docs name (cheap, no Docker), for any staged .md.
if grep -qE '\.md$' <<<"$selected"; then
  run make docs-links || fail "make docs-links: fix the broken links above"
fi

# Markdown only: nothing to lint or test. Not spec/ (tests read spec/README.md) or
# scripts/docs/ (the API notes are sources of the generated docs).
if [[ -n "$selected" ]] && ! grep -vE '\.md$' <<<"$selected" | grep -q . &&
  ! grep -qE '^(spec|scripts/docs)/' <<<"$selected"; then
  echo "pre-commit: Markdown only - lint and tests skipped" | tee -a "$log"
  exit 0
fi

# One test run at a time on the workstation (all worktrees: the lock is in the common git
# directory): two suites side by side (two sessions committing at once, 2026-10-23) took
# 12.5 minutes instead of 5 and failed the timing-sensitive worker tests. The heavy make
# targets take the same lock through scripts/with-test-lock when run by hand (#348);
# BSS_TEST_LOCK_HELD=1 lets the ones this hook runs pass, as it holds the lock already.
# The lock is released when the hook and the checks it started end, killed or not. 60
# minutes at most: a waiter can stand behind more than one queued run. After a wait the
# tree is checked again: a session sharing this checkout may have changed it meanwhile.
exec 9>>"$(git rev-parse --path-format=absolute --git-common-dir)/pre-commit.lock"
if ! flock -n 9; then
  echo "pre-commit: other test runs hold the lock - waiting for them" | tee -a "$log"
  flock -w "${PRE_COMMIT_LOCK_WAIT:-3600}" 9 ||
    fail "another test run still holds the lock after ${PRE_COMMIT_LOCK_WAIT:-3600} s"
  check_tree
fi
export BSS_TEST_LOCK_HELD=1

# First, in seconds: each entry point (and app.auth) imported first in a fresh interpreter
# (#476). An import cycle that shows only in one import order once surfaced 13 minutes into
# the suite, as worker processes crashing at startup; this stops on it with the traceback.
imports="$(python3 -B "$selection" imports 2>&1)" ||
  fail "scripts/hook_selection.py imports: ${imports}"
if [[ "$imports" == run ]]; then
  run make import-check || fail "make import-check: the traceback above names the import that failed"
fi

# run gives every check </dev/null: docker compose run only allocates a pty when stdin is
# actually a tty, so this keeps the hook from ever blocking on stdin in a non-interactive
# context (GUI clients, editors) as well as a plain terminal commit.
run make lint || fail "make lint"

# suite TARGET: a run the hook does by path (a script suite, the smoke tests).
# scripts/hook_selection.py holds its trigger paths and says `none` when no selected path
# is one of them, `passed` when the run already passed on exactly this content of all its
# trigger paths, else `run`. A passing run records that content (.testmon/TARGET), so a
# retry of the commit - after a lint failure, say - does not run it again (#366).
# Of the harness suites only those that depend on the selected paths run (#369); such a
# partial run records nothing, and `all` is the whole of make runtime-check.
suite() {
  local state chosen
  if [[ -n "$covered" ]]; then
    echo "$1: passed in the full run on this content; skipped" | tee -a "$log"
    return 0
  fi
  state="$(python3 -B "$selection" suite "$1" 2>&1)" ||
    fail "scripts/hook_selection.py suite $1: ${state}"
  case "$state" in
    run)
      if [[ "$1" == runtime-check ]]; then
        chosen="$(python3 -B "$selection" suites 2>&1)" ||
          fail "scripts/hook_selection.py suites: ${chosen}"
        if [[ -z "$chosen" ]]; then
          echo "runtime-check: no harness suite depends on the selected paths; skipped" | tee -a "$log"
          return 0
        elif [[ "$chosen" != all ]]; then
          echo "runtime-check: the suites that depend on the selected paths" | tee -a "$log"
          run make runtime-check "RUNTIME_SUITES=${chosen}" || fail "make runtime-check RUNTIME_SUITES=\"${chosen}\""
          return 0
        fi
      fi
      run make "$1" || fail "make $1"
      run python3 -B "$selection" stamp "$1" || fail "scripts/hook_selection.py stamp $1"
      ;;
    passed) echo "$1: passed on this content already; skipped" | tee -a "$log" ;;
  esac
}

# Tests: the plan of scripts/hook_selection.py for the selected paths (#344). Usually the
# smoke subset plus the tests this change affects (pytest-testmon, which follows the Python
# code each test ran), plus the tests that read a staged data file testmon cannot see (the
# UI sources, the scope file). The whole suite (make test-recorded) only for what any test
# may depend on - the specs, the dependency pins, the image or compose files, app/worker/
# (its tests run it in subprocesses), alembic/ - and not again when it already ran to its
# end on that content: a retry then runs the failed and the affected tests. The full
# `make test` is nightly in CI and by hand (2026-10-28).
# No backend test at all for a commit no backend test can depend on - the harness, the
# docs, the UI, CI (#369) - and no second smoke run on backend content it passed on.
# A passing full run recorded for exactly the staged tree (`make test` on the content being
# committed, or this hook's own whole-suite run on an earlier attempt; scripts/full_suite.py,
# #476) leaves only the marked tests it left out: no smoke, affected, whole-suite or script
# suite run again. A whole-suite run this hook makes and passes is recorded the same way,
# so `make check-pr` accepts it without `make test` on the same content.
covered=""
if found="$(python3 -B "$full_suite" covers 2>&1)"; then
  covered=1
  echo "$found" | tee -a "$log"
elif [[ -n "$found" ]]; then
  fail "scripts/full_suite.py covers: ${found}"
fi
plan="$(python3 -B "$selection" plan ${covered:+covered} 2>&1)" ||
  fail "scripts/hook_selection.py plan: ${plan}"
if [[ -z "$plan" && -z "$covered" ]]; then
  echo "backend tests: no selected path can affect them; skipped" | tee -a "$log"
fi
while IFS= read -r step; do
  [[ -n "$step" ]] || continue
  if [[ "$step" == test-smoke ]]; then
    suite test-smoke
    continue
  fi
  # shellcheck disable=SC2086 # a step is a make target and its variables
  run make $step || fail "make ${step}"
  if [[ "$step" == test-recorded* ]]; then
    run python3 -B "$full_suite" record-index || fail "scripts/full_suite.py record-index"
  fi
done <<<"$plan"

# The generated docs/api and docs/db (~7 s), when their sources change: regenerated here,
# so a commit that leaves them stale is refused with the files to stage.
if grep -qE '^(app|alembic|spec|scripts/docs)/|^tests/docs_collect' <<<"$selected"; then
  run make docs-check || fail "generated docs are out of date - git add docs/api docs/db and commit again"
fi

# The harness script tests, only when the commit touches the harness (scripts/, .claude/,
# .codex/, the workflows, the Makefile, compose.prod.yaml: the same paths as the CI harness
# job). `make test` (so the nightly
# run) includes them too; the hook's own full-suite run (make test-recorded) does not, so
# they never run twice or unasked.
suite runtime-check

# The deploy script's tests, only when the commit touches what they run: the deploy and
# smoke scripts, the compose files, the suite itself, its runner or the one Makefile
# recipe the suite reads.
suite deploy-check

# The shell cases of the hook's own scripts (this hook's lock and plan, the billing status
# check), when scripts/ changes. They were part of make test-smoke until #369.
suite hook-cases

# The admin UI, only when the commit touches it or the specs its types come from. These
# targets run offline and never install: if node_modules is out of date they fail with
# "run make web-install". A change in app/api or app/internal alone can still break the
# UI; make check (web-e2e) is the gate for that, on demand.
if grep -qE '^(frontend|spec)/' <<<"$selected"; then
  run make web-lint || fail "make web-lint"
  run make web-test || fail "make web-test"
fi
