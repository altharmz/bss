#!/usr/bin/env bash
# Invoked by the shared worktree dispatcher installed by `make hooks`.
# Refuses a push when a commit being pushed
#   - adds a line containing a bcrypt hash ($2a$/$2b$/$2y$ + cost), unless that line carries
#     the marker `pre-push: allow-dummy-hash` (a known dummy, e.g. a test fixture);
#   - adds or changes a path matching (^|/)\.env (.env.example excepted);
#   - deletes a remote branch, or is not a fast-forward (a force push).
# Only the commits being pushed are scanned (not already on a remote), and only their
# added lines - an existing hash elsewhere in a touched file does not count. Never prints a
# matched value, only the commit and the file. Ported from vps-lab (a3ada0b).
# git feeds one line per ref on stdin: <local ref> <local oid> <remote ref> <remote oid>.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

problems=()
while read -r local_ref local_oid remote_ref remote_oid; do
  if [[ "${local_oid}" =~ ^0+$ ]]; then
    problems+=("${remote_ref}: deletes a remote branch")
    continue
  fi
  if [[ "${remote_oid}" =~ ^0+$ ]]; then
    range=("${local_oid}" --not --remotes) # new branch: what no remote has yet
  else
    if git cat-file -e "${remote_oid}^{commit}" 2>/dev/null &&
      ! git merge-base --is-ancestor "${remote_oid}" "${local_oid}"; then
      problems+=("${remote_ref}: not a fast-forward (force push)")
      continue
    fi
    range=("${remote_oid}..${local_oid}")
  fi
  for commit in $(git rev-list "${range[@]}"); do
    short="$(git rev-parse --short "${commit}")"
    # paths added/copied/modified/renamed by this commit
    while IFS= read -r path; do
      [[ "${path}" =~ (^|/)\.env ]] || continue
      [[ "${path}" =~ (^|/)\.env\.example$ ]] && continue
      problems+=("${short}: adds or changes ${path}")
    done < <(git diff-tree --no-commit-id --name-only -r --root -m --first-parent \
      --diff-filter=ACMR "${commit}")
    # added lines with a bcrypt hash and no marker; reports the file, never the line
    while IFS= read -r file; do
      problems+=("${short}: adds a bcrypt hash in ${file} (mark a known dummy with: pre-push: allow-dummy-hash)")
    done < <(git diff-tree -p -U0 --no-color --no-ext-diff --root -m --first-parent \
      --no-commit-id "${commit}" | awk '
        /^\+\+\+ / { file = substr($0, 7); next }
        /^\+/ && /\$2[aby]\$[0-9][0-9]\$/ && index($0, "pre-push: allow-dummy-hash") == 0 {
          print file
        }' | sort -u)
  done
done

if ((${#problems[@]} > 0)); then
  printf '\033[1;31mpre-push hook refused the push:\033[0m\n' >&2
  printf '  %s\n' "${problems[@]}" >&2
  echo "push refused - nothing was sent" >&2
  exit 1
fi
exit 0
