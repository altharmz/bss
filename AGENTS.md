# Codex project instructions

Read and follow [CLAUDE.md](CLAUDE.md); it is the shared instruction file. `apply_patch`
is the equivalent of Claude's Edit/Write. `.codex/hooks.json` runs the shared shell guard
(`.claude/hooks/guard.sh`) and `.codex/patch-guard.sh` (never edit `.env*` or `design-ref/`).

Run `make` targets as a bare command (`make test`, `make lint`, `make deploy`): no `>`
redirect, pipe, `&&`, `;` or other command in the same call. `~/.codex/rules` allows
`make` only when the call is exactly that, otherwise Codex asks for approval. The output
comes back to you anyway; read it from the tool result, not from a log file.

Commit with `git commit -m "..."` (or `-F <file>`), not a heredoc, and push with a plain
`git push`, so the allow rules for `git commit`/`git push` match. Commit, push and
`make deploy` when you judge the change ready; do not ask for approval.
