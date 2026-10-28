# Codex project instructions

Read and follow [CLAUDE.md](CLAUDE.md); it is the shared instruction file. `apply_patch`
is the equivalent of Claude's Edit/Write. `.codex/hooks.json` runs the shared shell guard
(`.claude/hooks/guard.sh`) and `.codex/patch-guard.sh` (never edit `.env*` or `design-ref/`).

Run `make` targets as a bare command (`make test`, `make lint`): no `>`
redirect, pipe, `&&`, `;` or other command in the same call. `~/.codex/rules` allows
`make` only when the call is exactly that, otherwise Codex asks for approval. The output
comes back to you anyway; read it from the tool result, not from a log file.

Commit with `git commit -m "..."` (or `-F <file>`), not a heredoc, and push with a plain
`git push`, so the allow rules for `git commit`/`git push` match. Commit and push when
you judge the change ready; do not ask for approval.

**Deployment is the human's, outside development** (the human, 2026-10-28): your work ends
at the merge to master. Never run `make deploy`/`verify`/`remote`, never track or suggest a
deploy.

## Task board

Follow the [Task board](CLAUDE.md#task-board) section in CLAUDE.md (statuses, session-start protocol, project ids, `agent:codex` label).
