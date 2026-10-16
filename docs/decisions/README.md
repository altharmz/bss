# Decision records

The project's decisions and design detail, by area. Until `910ec0a` they all lived in
[CLAUDE.md](../../CLAUDE.md); it now keeps only what an agent needs every session (hard
constraints, harness rules, commands, workflow, next steps, open checks) and points here.
These files are as binding as CLAUDE.md: read the one for the area before changing it,
and record a new decision in it.

| File | Sections (headings kept from the old CLAUDE.md) |
|---|---|
| [billing-completion.md](billing-completion.md) | Refund payouts, on-demand bills, presentation preferences and issued JSON documents (2026-10-16) |
| [data-model.md](data-model.md) | Domain model & decisions (SID/TMF, orders, half-open ranges, enums, money, injected clock, cycle day, exclusion constraints, scope, stage order, rounding, billable days) |
| [tmf-api.md](tmf-api.md) | TMF API conventions (stage 2); TMF632, TMF629, TMF666 mapping; TMF676 (v4) mapping; TMF637 and TMF678 mapping; TMF622 mapping and order processing |
| [catalog.md](catalog.md) | TMF620 mapping: lifecycle, price rules, D-W18, D-W21 price change, offering code |
| [billing-core.md](billing-core.md) | Billing (stage 3); Open questions (changing `billCycleDay`, suspension semantics) |
| [worker.md](worker.md) | Billing worker (stage 4): slices b-h, Q1-Q14 and D-W decisions as recorded; resolved questions (stale runs, `APP_ENV`) |
| [bills.md](bills.md) | Bill lifecycle (option C): slices 1-4, migration 0006, settlement |
| [internal-api.md](internal-api.md) | Internal API (stage 5): endpoints, `X-Remote-User` trust |
| [mcp.md](mcp.md) | MCP connector: service, SDK, scopes, money as decimal strings, operator `mcp:<name>`, identity check |
| [frontend.md](frontend.md) | Frontend (stage 5) decisions; Frontend conventions |
| [infra.md](infra.md) | Template origin (`make init`); Environments (image size); Base image; Layout; Conventions (healthchecks, tests, local postgres, pinned dependencies, immutable migrations); Shared infrastructure (vps-infra); Known compose pitfalls; Deploying the UI; the two fixes to port to vps-lab-template |
| [harness.md](harness.md) | Pre-commit hook; Pre-push hook; Progress tracker; Agent guardrails: what they are for |
| [retired.md](retired.md) | Rules or decisions removed on purpose, with the reason |

**Nothing was lost in the move**: `scripts/check-decisions.py` (run by `make check`)
takes every line of CLAUDE.md at `910ec0a` that carries a decision id (D-W.., Q.., a
slice or checkpoint) or a never/must/always rule and fails unless each one still appears,
word for word, in CLAUDE.md or a file here (or is listed in [retired.md](retired.md)). It
also checks that every relative link in these files and CLAUDE.md resolves. Moving text
between these files is fine; rewording a rule line needs the old line retired with a
reason.
