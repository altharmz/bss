---
description: Resume known work or use make start-task to select and claim the next issue
---

Follow this when the human says "continue" or starts a session without a concrete task.
A concrete request takes precedence: inspect only the state it needs and do not take
unrelated Ready work. The full workflow is in
[docs/worktrees.md](../../docs/worktrees.md#normal-agent-workflow); board rules are in
[docs/board.md](../../docs/board.md).

1. **Master health.** `gh run list --branch master --event schedule --limit 3` (pushes to
   `master` start no run). If the latest nightly run failed, revert the offending PR (on a
   branch, as a PR), note it on the `Nightly CI failed:` issue the run opened, then carry
   on (board.md, "Red master").
2. **Resume known work.** In a task worktree, `make review-task` shows its state and
   `make check-pr` its PR. Failing checks or requested changes come before new work. Run
   `make telemetry-collect` to bind the resumed session (Kimi:
   `make telemetry-collect CLIENT=kimi SESSION_ID=<session-uuid>`).
3. **New work.** From clean primary `master`: `make start-task BSS_TASK_OWNER=<client>`
   (`claude`, `codex`, `kimi`). It selects the issue and a free slot and claims both; use
   exactly what it returns. Do not inspect or claim board items yourself.
4. **If startup fails or finds nothing**, stop and report the result.

"Deliver the next task" means step 3 directly, without the checks above. Report the
returned issue, slot and worktree, then work. In every case a successful `make start-task`
is the go-ahead: do not ask the human whether to start; carry on through delivery and stop
only at a human gate.
