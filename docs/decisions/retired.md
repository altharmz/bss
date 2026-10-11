# Retired rules and decisions

A line that `scripts/check-decisions.py` tracks (from CLAUDE.md at `910ec0a`: a decision
id or a never/must/always rule) may only disappear from CLAUDE.md and `docs/decisions/`
when it is listed here, quoted exactly as it was (one line of the old file, without its
list marker), with the date and the reason - for example an open check that passed, or a
rule the human changed.

Format: one entry per line, `- "<the old line>" - <date>: <reason>`.

- "**Progress tracker steps 3-5** (after slice b): "tested" (pytest plugin: requests recorded by `tests/apiclient.py` matched to OpenAPI templates, `pg_stat_user_tables` of each test database before it is dropped; UI: done for vitest - `expectUi(id)`, see "Progress tracker"; Playwright could record the same way), the drift check in the pre-commit hook and `make verify` (with negative proofs), and "deployed" from `docs/progress/deployed.txt`. Slice b also defines the `worker` area (capabilities proven by `@pytest.mark.capability(...)` tests)." - 2026-10-11: "tested" for db and api is built (`cloud/quality-1`); the requests are recorded at the starlette `TestClient` rather than in `tests/apiclient.py`, so `test_health.py`'s raw clients count too. The rest of the item (drift check, "deployed") stays in CLAUDE.md, reworded ([harness.md](harness.md#progress-tracker)).
- "**operator** comes from the `X-Remote-User` header, which the shared Caddy must *set* (overwrite) from the basic-auth user, so a client-sent header never passes through. Anything not matching `^[A-Za-z0-9._@-]{1,64}$` is reported as null." - 2026-10-11: the pattern gained `:` for the MCP connector's principal `mcp:<name>` (plan decision D3, agreed with the human); the rule is otherwise unchanged and lives on, reworded, in [internal-api.md](internal-api.md) ([mcp.md](mcp.md)).
