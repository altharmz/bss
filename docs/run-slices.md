# Orchestrator prompt: one subagent per slice

To start, paste one of these lines into a new session:

- "Read docs/run-slices.md and follow it for Phase A (slices 1-8)."
- "Read docs/run-slices.md and follow it for Phase B (slices 9-24)."

---

You are the **orchestrator** for the phase named in the request, taken from
[docs/next-slices.md](next-slices.md). Every design decision is already made in
[docs/next-slices-design.md](next-slices-design.md), so nothing needs the human's
approval. Your job:
- Run each slice in a **new, clean subagent** (Agent tool, `subagent_type:
  "general-purpose"`, `model: "sonnet"`), one at a time and in order.
- Check what it did, then start the next slice.

You do not write code. Do not run two subagents at once: they share the working tree,
the local stack and the pre-commit lock.

## Before the first slice

1. Read CLAUDE.md, docs/next-slices.md, and the "Rules for every slice" section of the
   design.
2. Run `git status` and `git log --oneline -5`. The tree must be clean on `master`; if it
   is not, stop and tell the human.
3. Run `make ps`. If the local stack is down, run `make up`.
4. Start at the first row of the phase whose status is not `done`. This also resumes an
   interrupted run.

## For each slice

1. **Start one implementation subagent** with the prompt below.
2. **Check its report.** Do not trust it blindly:
   - `git log --oneline -3` shows the new commit, and `git status` is clean.
   - `git status -sb` shows that the push happened (no "ahead").
   - `git show --stat HEAD` contains new or changed tests. A frontend slice also touches
     `docs/progress/scope.yaml` and `docs/ui.md`. A backend slice touches `docs/api` if
     the contract changed.
   - The row in docs/next-slices.md says `done: <commit subject>`, or it was split into
     `Na`/`Nb` with `Na` done.
3. **When a slice fails** (it is not committed, the hook fails, the subagent is blocked,
   or a check above fails): start **one** new subagent. Give it the first one's report,
   the failure output and `git status`, and tell it to continue from the current tree.
   If that also fails, stop and report to the human. Never fix anything yourself.
4. **When a slice splits:** continue with its next row that is not done (`4b`), as its
   own subagent.
5. **After each slice,** give the human one line: the slice number, the commit subject,
   the hook's test counts, and any recorded deviation from the design.

## When to stop and ask the human

- A subagent reports that the slice needs a migration the design does not plan, or a
  billing change. This is the only stop the design allows.
- Two failed attempts on one slice.
- A guard block that the subagent believes is legitimate.
- Anything touching the deploy, the VPS or secrets. **Never deploy, and never suggest
  deploying.**

## At the end of the phase

- Update the **Estimate** table in docs/next-slices.md: the commits used, the splits,
  and the deviations.
- Update the CLAUDE.md "Current state" (the `Next` bullet: which phase is done and
  where the next one starts).
- Commit and push this as one Markdown-only commit.
- Report the list of commits to the human.

## Subagent prompt (replace N; add the retry block only on a retry)

```
You are implementing slice N of docs/next-slices.md in /home/petrg/projects/bss.
Every design decision is made; do not ask for approval, build what the design says.

Read first: CLAUDE.md (hard constraints, working style, code rules); in
docs/next-slices-design.md the sections "Rules for every slice", "When the spec is
wrong" and section N; the decision files and code section N names. Look at the
pattern sources the rules list before writing new components.

Build exactly section N, nothing more:
- New tests that fail without the change; pin every write body in the component
  test and tests/test_ui_requests.py (replayed against the backend). Never weaken,
  skip or delete an existing test (TEST-CHANGE rule in CLAUDE.md).
- Backend parts: contract first (spec/internal/bss-internal.oas.yaml), then code,
  then `make docs`; register any new operation in the MCP registry.
- Update docs/ui.md, docs/progress/scope.yaml, the decision entry the section names
  (what was built, tests, deviations; link the design instead of copying it), and
  row N of docs/next-slices.md (`done: <commit subject>`). If decisions/prepaid.md
  or decisions/frontend.md carries a "decided, not built" note for this slice,
  replace that part with your entry.
- If the code contradicts the design, follow "When the spec is wrong" and record
  the deviation. Stop WITHOUT committing only if you would need an unplanned
  migration or a billing change; report why.
- Targeted tests while developing (make web-test, single pytest files); the
  pre-commit hook is the one full run. Never use --no-verify. If the hook fails, fix
  the cause and commit again.
- Too large for one commit: split into Na/Nb rows in docs/next-slices.md, commit Na
  complete with its tests, report that Nb remains.
- One commit (the message ends with the Co-Authored-By line from your
  instructions), then `git push`. Never deploy.

Report back in at most 15 lines: the commit hash and subject, files changed, tests
added, the hook's test counts, deviations recorded, anything left open.

[retry only] Previous attempt report and failure output:
<paste>
Continue from the current working tree; do not start over.
```
