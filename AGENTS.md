# Codex project instructions

Read and follow [CLAUDE.md](CLAUDE.md); it is the shared instruction file. `apply_patch`
is the equivalent of Claude's Edit/Write. `.codex/hooks.json` runs the shared shell guard
(`.claude/hooks/guard.sh`) and `.codex/patch-guard.sh` (never edit `.env*` or `design-ref/`).
