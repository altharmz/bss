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
# The fake make records its target, and in ${held} whether it ran with the lock held.
printf '#!/bin/sh\necho "$1" >>"%s"\necho "${BSS_TEST_LOCK_HELD:-unset}" >>"%s"\n' \
  "${calls}" "${held}" >"${tmp}/bin/make"
chmod +x "${tmp}/bin/make"
export PATH="${tmp}/bin:${PATH}"
git init -q "${tmp}/primary"
git -C "${tmp}/primary" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
git -C "${tmp}/primary" worktree add -q -b issue-1-lock "${repo}"
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

expect 0 "lint test-smoke test-changed" "a free lock runs the checks at once"
# Without it the make targets, now locked themselves, would wait for the hook: a deadlock.
if [[ "$(sort -u "${held}")" != 1 ]]; then
  printf 'FAIL the hook runs make with BSS_TEST_LOCK_HELD=1: saw [%s]\n' \
    "$(sort -u "${held}" | tr '\n' ' ')" >&2
  failed=$((failed + 1))
fi
rm -f "${held}"

hold 1 "echo released >>${calls}"
expect 0 "released lint test-smoke test-changed" "a held lock: the checks run after it is released"
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
expect 0 "lint test-smoke test-changed test-files web-lint web-test" \
  "a UI source runs the backend tests that read it, not the whole suite"

echo m >"${repo}/alembic/m.py"
git -C "${repo}" add alembic
expect 0 "lint test-recorded docs-check web-lint web-test" \
  "a migration runs the whole suite, recorded, without the harness and deploy suites"

mkdir -p "${repo}/.testmon"
(cd "${repo}" && python3 -B "$(dirname "${hook}")/hook_selection.py" digest) >"${repo}/.testmon/full-suite"
expect 0 "lint test-smoke test-changed test-files docs-check web-lint web-test" \
  "a retry on the content the whole suite ran on runs the failed and affected tests"

echo fixed >"${repo}/alembic/m.py"
git -C "${repo}" add alembic
expect 0 "lint test-recorded docs-check web-lint web-test" \
  "a changed migration runs the whole suite again"

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
