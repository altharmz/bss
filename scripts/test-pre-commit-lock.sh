#!/usr/bin/env bash
# Cases for the run lock of scripts/pre-commit.sh. Run by `make test` (on the host, a few
# seconds): the real hook in a throwaway repository, with a `make` that only records its
# targets, while another process holds the lock or not.
set -uo pipefail

hook="$(cd "$(dirname "$0")" && pwd)/pre-commit.sh"
tmp="$(mktemp -d)"
trap 'wait; rm -rf "${tmp}"' EXIT
repo="${tmp}/repo"
calls="${tmp}/calls"
mkdir -p "${tmp}/bin" "${repo}"
printf '#!/bin/sh\necho "$1" >>"%s"\n' "${calls}" >"${tmp}/bin/make"
chmod +x "${tmp}/bin/make"
export PATH="${tmp}/bin:${PATH}"
git -C "${repo}" init -q
git -C "${repo}" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
echo one >"${repo}/tracked.txt"
git -C "${repo}" add tracked.txt
lock="${repo}/.git/pre-commit.lock"
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

expect 0 "lint test" "a free lock runs the checks at once"

hold 1 "echo released >>${calls}"
expect 0 "released lint test" "a held lock: the checks run after it is released"
grep -q 'waiting for them' "${tmp}/out" || {
  echo 'FAIL a held lock: no waiting message' >&2
  failed=$((failed + 1))
}

hold 1 "echo two >${repo}/tracked.txt"
expect 1 "" "a tree changed during the wait is checked again"
git -C "${repo}" checkout -q -- tracked.txt

hold 3
PRE_COMMIT_LOCK_WAIT=1 expect 1 "" "a lock held past the wait limit fails the commit"

if ((failed > 0)); then
  printf 'pre-commit lock: %d of %d cases failed\n' "${failed}" "${total}" >&2
  exit 1
fi
printf 'pre-commit lock: all %d cases pass\n' "${total}"
