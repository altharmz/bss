#!/usr/bin/env bash
# Cases for the run lock of scripts/pre-commit.sh, the test runs it plans and the same lock
# taken by scripts/with-test-lock for the heavy make targets (#348). Run by `make test` (on
# the host, a few seconds): the real hook and wrapper in a throwaway repository, with a
# `make` that only records its targets, while another process holds the lock or not.
set -uo pipefail

hook="$(cd "$(dirname "$0")" && pwd)/pre-commit.sh"
wrapper="$(dirname "${hook}")/with-test-lock"
# The hook runs this script with the lock held (BSS_TEST_LOCK_HELD=1); the cases below
# need the wrapper to take its fixture lock itself.
unset BSS_TEST_LOCK_HELD BSS_TEST_LOCK_WAIT PRE_COMMIT_LOCK_WAIT
# This fixture creates foreign repositories. Git hooks export absolute GIT_DIR in a
# linked worktree; never let that select the caller's real repository in the fixture.
while IFS= read -r variable; do unset "$variable"; done < <(git rev-parse --local-env-vars)
tmp="$(mktemp -d)"
trap 'wait; rm -rf "${tmp}"' EXIT
repo="${tmp}/repo"
calls="${tmp}/calls"
mkdir -p "${tmp}/bin" "${repo}"
held="${tmp}/held"
# The fake make records its target, and in ${held} whether it ran with the lock held; the
# target named in FAKE_MAKE_FAIL fails.
printf '#!/bin/sh\necho "$1" >>"%s"\necho "${BSS_TEST_LOCK_HELD:-unset}" >>"%s"\n%s\n%s\n' \
  "${calls}" "${held}" 'echo "make $*"' '[ "$1" != "${FAKE_MAKE_FAIL:-}" ]' >"${tmp}/bin/make"
chmod +x "${tmp}/bin/make"
export PATH="${tmp}/bin:${PATH}"
git init -q "${tmp}/primary"
git -C "${tmp}/primary" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
git -C "${tmp}/primary" worktree add -q -b issue-1-lock "${repo}"
# The hook's state stays out of the content a full run records, as .gitignore does in bss.
echo .testmon/ >>"${tmp}/primary/.git/info/exclude"
echo one >"${repo}/tracked.txt"
git -C "${repo}" add tracked.txt
lock="${tmp}/primary/.git/pre-commit.lock"
total=0
failed=0

# hold <seconds> [command]: hold the lock in the background, then run the command
hold() {
  flock "${lock}" sh -c "sleep $1; ${2:-true}" &
  while flock -n "${lock}" true; do sleep 0.05; done
}

# expect <exit code> <make targets, space separated> <case>
expect() {
  local want="$1" targets="$2" name="$3" got
  touch "${calls}"
  (cd "${repo}" && bash "${hook}") >"${tmp}/out" 2>&1 </dev/null
  got=$?
  wait
  total=$((total + 1))
  local seen
  seen="$(tr '\n' ' ' <"${calls}" | sed 's/ $//')"
  if [[ "${got}" != "${want}" || "${seen}" != "${targets}" ]]; then
    printf 'FAIL %s: exit %s (want %s), make [%s] (want [%s])\n' \
      "${name}" "${got}" "${want}" "${seen}" "${targets}" >&2
    sed 's/^/  | /' "${tmp}/out" >&2
    failed=$((failed + 1))
  fi
  rm -f "${calls}"
}

commit() { git -C "${repo}" -c user.name=t -c user.email=t@t commit -q "$@"; }
# said <text> <case>: the last hook run printed the text
said() {
  grep -qF -- "$1" "${tmp}/out" || {
    printf 'FAIL %s: the hook did not print [%s]\n' "$2" "$1" >&2
    sed 's/^/  | /' "${tmp}/out" >&2
    failed=$((failed + 1))
  }
}

expect 0 "lint test-smoke test-changed" "a free lock runs the checks at once"
# Without it the make targets, now locked themselves, would wait for the hook: a deadlock.
if [[ "$(sort -u "${held}")" != 1 ]]; then
  printf 'FAIL the hook runs make with BSS_TEST_LOCK_HELD=1: saw [%s]\n' \
    "$(sort -u "${held}" | tr '\n' ' ')" >&2
  failed=$((failed + 1))
fi
rm -f "${held}"

# The smoke run passed on this backend content: it is not repeated from here on (#369).
hold 1 "echo released >>${calls}"
expect 0 "released lint test-changed" "a held lock: the checks run after it is released"
grep -q 'waiting for them' "${tmp}/out" || {
  echo 'FAIL a held lock: no waiting message' >&2
  failed=$((failed + 1))
}

hold 1 "echo two >${repo}/tracked.txt"
expect 1 "" "a tree changed during the wait is checked again"
git -C "${repo}" checkout -q -- tracked.txt

hold 3
PRE_COMMIT_LOCK_WAIT=1 expect 1 "" "a lock held past the wait limit fails the commit"

# The test runs planned for the staged files (scripts/hook_selection.py, #344).
mkdir -p "${repo}/frontend/src" "${repo}/alembic"
echo a >"${repo}/frontend/src/a.ts"
git -C "${repo}" add frontend
expect 0 "lint test-changed test-files web-lint web-test" \
  "a UI source runs the backend tests that read it, not the whole suite"
grep -qF 'test-smoke: passed on this content already; skipped' "${tmp}/out" || {
  echo 'FAIL a smoke run on unchanged backend content: no skipped line' >&2
  failed=$((failed + 1))
}

echo m >"${repo}/alembic/m.py"
git -C "${repo}" add alembic
FAKE_MAKE_FAIL=test-recorded expect 1 "import-check lint test-recorded" \
  "a migration runs the import check, then the whole suite, recorded, without the harness and deploy suites"
[[ ! -e "${repo}/.testmon/full-suite-passed.json" ]] || {
  echo 'FAIL a failing whole-suite run in the hook wrote a full-suite record' >&2
  failed=$((failed + 1))
}

# The suite ran to its end (and failed): its testmon stamp, as make test-recorded writes it.
mkdir -p "${repo}/.testmon"
(cd "${repo}" && python3 -B "$(dirname "${hook}")/hook_selection.py" digest) >"${repo}/.testmon/full-suite"
expect 0 "import-check lint test-smoke test-changed test-files docs-check web-lint web-test" \
  "a retry on the content the whole suite ran on runs the failed and affected tests"

echo fixed >"${repo}/alembic/m.py"
git -C "${repo}" add alembic
FAKE_MAKE_FAIL=docs-check expect 1 "import-check lint test-recorded docs-check" \
  "a changed migration runs the whole suite again"

# #476: a whole-suite run the hook passed is recorded for the staged tree (make check-pr
# accepts it), and a retry on that content runs no suite again: only the marked tests the
# run left out (the fake make does not see MARKS).
expect 0 "import-check lint test-marked docs-check web-lint web-test" \
  "a retry on the content the hook's whole suite passed on runs no suite again"
said 'full suite: passed on exactly the staged content already' "a covered retry"

commit -m base
# #476: make test passed on the work before it was staged; a commit of exactly that content
# runs the import check, lint and the generated docs, and no suite again.
mkdir -p "${repo}/app" "${repo}/scripts"
echo x >"${repo}/app/x.py"
echo b >"${repo}/scripts/b.py"
(cd "${repo}" && python3 -B "$(dirname "${hook}")/full_suite.py" snapshot &&
  python3 -B "$(dirname "${hook}")/full_suite.py" record) >"${tmp}/out" 2>&1
said 'full suite: passed, recorded' "make test on work not yet staged"
git -C "${repo}" add app scripts
expect 0 "import-check lint docs-check" "make test on exactly the staged content: no suite runs again"
said 'runtime-check: passed in the full run on this content; skipped' "a covered harness suite"
echo y >"${repo}/app/x.py"
git -C "${repo}" add app
expect 0 "import-check lint test-smoke test-changed docs-check runtime-check hook-cases" \
  "a change after make test runs the usual checks"
commit -m covered

# A script suite is not repeated on content it already passed on (#366).
mkdir -p "${repo}/scripts"
echo a >"${repo}/scripts/a.py"
git -C "${repo}" add scripts
expect 0 "lint runtime-check hook-cases" \
  "a harness path runs the harness suite and the hook cases, and no backend test"
said 'backend tests: no selected path can affect them; skipped' "a harness-only commit"
expect 0 "lint" "a retry on the same harness content skips the suite"
said 'runtime-check: passed on this content already; skipped' "a skipped harness suite"

echo b >"${repo}/scripts/a.py"
git -C "${repo}" add scripts
FAKE_MAKE_FAIL=runtime-check expect 1 "lint runtime-check" \
  "a changed harness path runs the suite again"
expect 0 "lint runtime-check hook-cases" "a failed suite leaves no stamp: it runs again"

echo d >"${repo}/scripts/deploy.sh"
git -C "${repo}" add scripts
FAKE_MAKE_FAIL=deploy-check expect 1 "lint runtime-check deploy-check" \
  "a deploy script runs both script suites"
expect 0 "lint deploy-check hook-cases" \
  "each suite has its own stamp: only the failed one and what follows it run"
expect 0 "lint" "every suite passed on this content"
said 'deploy-check: passed on this content already; skipped' "a skipped deploy suite"

# Of the Makefile only the recipe the deploy suite reads runs that suite (#369).
printf 'deployed:\n\techo one\n' >"${repo}/Makefile"
git -C "${repo}" add Makefile
expect 0 "lint runtime-check deploy-check" "the deploy recipe of the Makefile runs the deploy suite"
commit -m recipe
printf 'deployed:\n\techo one\n\nother:\n\techo a\n' >"${repo}/Makefile"
git -C "${repo}" add Makefile
expect 0 "lint runtime-check" "another Makefile change runs the harness suites only"

# Only the suites that depend on the selected paths (#369); a partial run leaves no stamp.
commit -m makefile
echo 'NAMES = "a.py"' >"${repo}/scripts/test_x.py"
echo 'NAMES = "deploy.sh"' >"${repo}/scripts/test_y.py"
git -C "${repo}" add scripts
commit -m suites
echo c >"${repo}/scripts/a.py"
git -C "${repo}" add scripts
expect 0 "lint runtime-check hook-cases" "a script runs the suites that name it"
said 'RUNTIME_SUITES=scripts/test_x.py' "a partial harness run"
expect 0 "lint runtime-check" "a partial run is not recorded: a retry runs it again"

# A merge commit: the checks are chosen from the task's side, not from what comes in (#366).
commit -m harness
trunk="$(git -C "${tmp}/primary" symbolic-ref --short HEAD)"
# incoming <name>: master moves on with a migration, worker code, a script and a UI source
incoming() {
  git -C "${tmp}/primary" -c user.name=t -c user.email=t@t merge -q --ff-only "$(git -C "${repo}" rev-parse HEAD)"
  mkdir -p "${tmp}/primary/app/worker" "${tmp}/primary/scripts"
  for file in "alembic/$1.py" "app/worker/$1.py" "scripts/$1.py" "frontend/src/$1.ts" Makefile; do
    echo "$1" >>"${tmp}/primary/${file}"
  done
  git -C "${tmp}/primary" add -A
  git -C "${tmp}/primary" -c user.name=t -c user.email=t@t commit -q -m "incoming $1"
}
incoming one
mkdir -p "${repo}/docs"
echo task >"${repo}/docs/notes.txt"
git -C "${repo}" add docs
commit -m task
git -C "${repo}" -c user.name=t -c user.email=t@t merge -q --no-ff --no-commit "${trunk}" >/dev/null 2>&1
expect 0 "lint" \
  "a merge runs no backend test or script suite for what only comes in"
said "selection: merge with $(git -C "${tmp}/primary" rev-parse --short HEAD), 5 incoming paths left out" \
  "a merge commit"
commit -m merge

incoming two
mkdir -p "${repo}/app/worker"
echo task >"${repo}/app/worker/task.py"
echo task >"${repo}/scripts/task.py"
git -C "${repo}" add app scripts
commit -m task
git -C "${repo}" -c user.name=t -c user.email=t@t merge -q --no-ff --no-commit "${trunk}" >/dev/null 2>&1
expect 0 "import-check lint test-recorded docs-check runtime-check hook-cases" \
  "a merge runs the suites the task's own side calls for"
commit -m merge

# wrapped <exit code> <recorded lines, space separated> <case> <command...>: the wrapper,
# run in the task worktree (its lock is the one in the common git directory, as the hook's).
wrapped() {
  local want="$1" lines="$2" name="$3" got seen
  shift 3
  touch "${calls}"
  (cd "${repo}" && "${wrapper}" "$@") >"${tmp}/out" 2>&1 </dev/null
  got=$?
  wait
  total=$((total + 1))
  seen="$(tr '\n' ' ' <"${calls}" | sed 's/ $//')"
  if [[ "${got}" != "${want}" || "${seen}" != "${lines}" ]]; then
    printf 'FAIL %s: exit %s (want %s), recorded [%s] (want [%s])\n' \
      "${name}" "${got}" "${want}" "${seen}" "${lines}" >&2
    sed 's/^/  | /' "${tmp}/out" >&2
    failed=$((failed + 1))
  fi
  rm -f "${calls}"
}
record="echo ran >>${calls}"

wrapped 0 "ran" "test lock: a free lock runs the command at once" sh -c "${record}"
wrapped 3 "ran" "test lock: the command's exit status is returned" sh -c "${record}; exit 3"
wrapped 0 "1" "test lock: the command runs with BSS_TEST_LOCK_HELD=1" \
  sh -c "echo \"\${BSS_TEST_LOCK_HELD}\" >>${calls}"

hold 1 "echo released >>${calls}"
wrapped 0 "released ran" "test lock: a held lock runs the command after its release" \
  sh -c "${record}"
grep -q 'test lock: another test run is in progress - waiting' "${tmp}/out" || {
  echo 'FAIL test lock: a held lock prints no waiting line' >&2
  failed=$((failed + 1))
}

hold 3
BSS_TEST_LOCK_WAIT=1 wrapped 1 "" "test lock: a lock held past the wait limit fails, command not run" \
  sh -c "${record}"

hold 2 "echo released >>${calls}"
BSS_TEST_LOCK_HELD=1 wrapped 0 "ran released" \
  "test lock: BSS_TEST_LOCK_HELD=1 runs the command at once beside the holder" sh -c "${record}"

if ((failed > 0)); then
  printf 'pre-commit lock: %d of %d cases failed\n' "${failed}" "${total}" >&2
  exit 1
fi
printf 'pre-commit lock: all %d cases pass\n' "${total}"
