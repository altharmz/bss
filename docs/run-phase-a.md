# Orchestrator prompt: Phase A with one subagent per slice

To start, paste this into a new session, or say
"Read docs/run-phase-a.md and follow it."

---

You are the **orchestrator** for Phase A (slices 1-8) of
[docs/next-slices.md](next-slices.md). You do not write code yourself. You run each
slice in a **new, clean subagent** (Agent tool, `subagent_type: "general-purpose"`,
`model: "sonnet"`), one at a time and in order. You check its result and then start the
next slice. Do not run two subagents at once: they share the working tree, the local
stack and the pre-commit lock.

## Before the first slice

1. Read CLAUDE.md and docs/next-slices.md, then run `git status` and `git log --oneline -5`.
   The tree must be clean on `master`. If it is not, stop and tell the human.
2. Find the first slice whose status is not `done`. Start there. This lets you resume a
   run that was interrupted.
3. Ask the human now, in one AskUserQuestion, for the **slice 8 decision**: build
   future-dated prepaid suspend/resume (a data-model change; a plan for the human comes
   first), or keep today-only (record that decision and mark the row done). Ask it now so
   the run does not stop for it later.

## For each slice

1. **Slices that need design approval (4, 5, and 8 if it will be built):**
   1. Start a design subagent (the prompt is below, with `MODE: design`). It writes the
      design note into the decision file the slice names. It does not commit or write code.
   2. Show the human the note's key choices and ask for approval with AskUserQuestion.
      Offer "Approve", "Approve with changes" (they type the changes) and "Skip slice".
   3. On approval, start a new implementation subagent and give it the approved changes.
2. **All other slices:** start one implementation subagent (`MODE: implement`).
3. **Check the subagent's report.** Do not trust it blindly:
   - `git log --oneline -3` shows the new commit, and `git status` is clean.
   - `git show --stat HEAD` contains tests, and frontend changes have `scope.yaml` and
     `docs/ui.md` updates.
   - The slice's row in docs/next-slices.md says `done: <commit subject>`.
   - `git status -sb` shows that the push happened.
4. **When a slice fails** (it is not committed, the hook fails, or the subagent is
   blocked): start **one** new subagent with the first one's report and the failure
   output. Tell it to continue from the current tree and not to start over. If that also
   fails, stop and report to the human. Never make a fix yourself.
5. **When a slice splits** (the subagent committed `4a` and updated the rows): continue
   with the next row that is not done, which is `4b`.
6. After each slice, give the human one line: the slice number, the commit subject, and
   the test counts the subagent reported.

## When to stop and ask the human

- A subagent finds that a slice needs a backend, migration or data-model change the plan
  did not expect (slice 2's transactions question can be one).
- Two failed attempts on one slice.
- A guard block that the subagent believes is legitimate.
- Anything touching the deploy, the VPS or secrets. **Never deploy, and never suggest
  deploying.**

## At the end

After slice 8 (or after it is recorded as not built), update the **Estimate** table in
docs/next-slices.md with the commits actually used and the splits. Add one line to the
CLAUDE.md "Current state" saying Phase A is done and Phase B starts at slice 9. Commit
and push this as one Markdown-only commit. Then report the list of commits to the human.

## Subagent prompt (fill in N, MODE and the extras)

```
You are completing slice N of docs/next-slices.md in /home/petrg/projects/bss.
MODE: implement | design

Read first: CLAUDE.md (hard constraints, working style, code rules), the
"How to run a slice" section and row N of docs/next-slices.md, the decision file(s)
the row links, and the code it points to. Re-measure the relevant part of the gap
before you change anything.

MODE design: write a short design note for slice N into the decision file the row
names (choices, the API bodies, what the UI explains to the operator, open
questions with your recommendation). Do not write code, do not commit. Report the
note's location and its key choices in at most 15 lines.

MODE implement:
- Build exactly row N, nothing more. If it needs a backend, migration or data-model
  change the row did not plan, or a decision the human has not made, STOP without
  committing and report the question.
- New tests that would fail without the change; pin every write body in the
  component test and tests/test_ui_requests.py. Never weaken, skip or delete an
  existing test (TEST-CHANGE rule in CLAUDE.md).
- Update docs/ui.md, docs/progress/scope.yaml, the decision entry, and row N's
  status (`done: <commit subject>`). Run `make docs` if the API changed.
- Targeted tests while developing (make web-test, a single pytest file); the
  pre-commit hook is the one full run. Never use --no-verify.
- If the slice is too large, split it: commit Na with its rows updated in
  docs/next-slices.md, and report that Nb remains.
- One commit (message ends with the Co-Authored-By line from your instructions), then
  `git push`. Never deploy.

Approved design changes (slices 4, 5, 8 only): <paste or "none">
Previous attempt report (retries only): <paste or "none">

Report back in at most 15 lines: the commit hash and subject, the files changed, the
tests added, the hook's test counts, decisions recorded, anything left open.
```
