# Decision records

The project's decisions and design detail, by area. [CLAUDE.md](../../CLAUDE.md) keeps
only what every session needs and points here; read the file for an area before
changing it, and record a new decision in it.

| File | Sections (headings kept from the old CLAUDE.md) |
|---|---|
| [accounts.md](accounts.md) | TMF666 financial, party and settlement accounts: owner, links, per-currency balance (2026-10-16); every account attribute, relatedParty roles (2026-10-18) |
| [events.md](events.md) | Event notifications: TMF hubs, callback rules, events and their delivery (2026-10-17) |
| [billing-completion.md](billing-completion.md) | Refund payouts, on-demand bills, presentation preferences and issued JSON documents (2026-10-16) |
| [data-model.md](data-model.md) | Domain model & decisions (SID/TMF, orders, half-open ranges, enums, money, injected clock, cycle day, exclusion constraints, scope, stage order, rounding, billable days) |
| [tmf-api.md](tmf-api.md) | TMF API conventions (stage 2); TMF632, TMF629, TMF666 mapping; TMF676 (v4) mapping; TMF637 and TMF678 mapping; TMF622 mapping and order processing |
| [catalog.md](catalog.md) | TMF620 mapping: lifecycle, price rules, D-W18, D-W21 price change, offering code; every catalog attribute, inline prices (2026-10-18) |
| [billing-core.md](billing-core.md) | Billing (stage 3); Open questions (changing `billCycleDay`, suspension semantics) |
| [charge-periods.md](charge-periods.md) | Product charge intervals and activation-date anniversaries; monthly invoices remain separate (2026-10-20) |
| [prepaid.md](prepaid.md) | Finalized subscriber-number and prepaid design hand-off; implementation pending (2026-10-20) |
| [worker.md](worker.md) | Billing worker (stage 4): slices b-h, Q1-Q14 and D-W decisions as recorded; resolved questions (stale runs, `APP_ENV`) |
| [bills.md](bills.md) | Bill lifecycle (option C): slices 1-4, migration 0006, settlement |
| [internal-api.md](internal-api.md) | Internal API (stage 5): endpoints, `X-Remote-User` trust |
| [mcp.md](mcp.md) | MCP connector: service, SDK, scopes, money as decimal strings, operator `mcp:<name>`, identity check |
| [frontend.md](frontend.md) | Sidebar grouped by TMF area and global Orders screen (2026-10-21); Frontend (stage 5) decisions; Frontend conventions |
| [infra.md](infra.md) | Environments (image size); Base image; Layout; Conventions (healthchecks, tests, local postgres, pinned dependencies, immutable migrations); Shared infrastructure (vps-infra); Known compose pitfalls; Deploying the UI; the two fixes to port to vps-lab-template |
| [users.md](users.md) | UI user management: users, roles (admin, user), sessions, the plan (2026-10-19) |
| [harness.md](harness.md) | Development cycle and gates (hooks); the guard; CI; Codex; UI/MCP parity; Progress tracker (history: [archive](../archive/harness-history.md)) |
