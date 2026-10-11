# MCP connector

User-facing description: [docs/mcp.md](../mcp.md). Decisions as taken (2026-10-11, plan
D1-D6 agreed with the human):

- **Same image, own service, HTTP only (D5).** `bss_mcp/` sits next to `app/`, not in it
  (`app/`'s wall-clock rule stays as it is; bss_mcp has its own `bss_mcp/clock.py`). It
  imports the app for `app.openapi()` and the request models only, and calls the app over
  HTTP - never the database. One image means one ship and one tag, and the tools always
  match the API they call.
- **SDK, not hand-rolled.** The official `mcp` package, pinned exactly (`mcp==2.3.0`; its
  dependencies frozen in both constraints files - resolved in the pinned base image,
  `rpds-py` kept at the dev image's `2026.6.3` so both images run the same version). The
  low-level `Server`, because `tools/list` depends on the caller's scope; the SDK's
  `BearerAuthBackend` and `RequireAuthMiddleware` for 401 + `WWW-Authenticate`.
- **Stateless Streamable HTTP with JSON responses**: no SSE, no sessions to keep in a
  1-vCPU container.
- **operationId = the handler's function name (D1)**: `generate_unique_id_function` in
  `app/main.py`; `tests/test_operation_ids.py` keeps them unique, `^[a-z0-9_]+$` and at
  most 64 characters (clients' tool-name limit). `/openapi.json` changes with it; it was
  never the contract (the vendored specs are).
- **Scope enforcement before the SDK**: `Guard` reads a `tools/call` and answers a write
  tool with a read token by HTTP 403 `insufficient_scope` - the request never reaches the
  app. `call_tool` checks again (defence in depth); `tools/list` hides write tools from a
  read token.
- **Money as decimal strings, floats refused outright**: the app reads exact JSON numbers
  only; an MCP client's parser makes them floats. A float anywhere in the arguments is
  refused first, then the arguments are checked against the tool's schema (proof: with
  the float check removed a float is still refused, by the schema, only the message
  differs).
- **`X-Remote-User: mcp:<name>` (D3)**: the app's operator pattern gained `:`; a
  basic-auth user cannot contain one, so `:` marks MCP.
- **API URL per environment (D4)**: `http://bss:8080` in prod (the alias on `shared`,
  where `app` is ambiguous), `http://app:8080` locally; plus the **startup identity check**
  (`GET /` must be exactly bss's answer, else the service does not start).
- **Static tokens**: `bss_st_` + 32 random bytes, SHA-256 in SQLite, names
  `^[a-z0-9][a-z0-9._-]{0,47}$` (so `mcp:<name>` fits the operator pattern), unique.
- **Rate limit** in memory, token bucket per principal (one process; a restart refills).
  **Body limit** read in full before anything else on every route.
- **Audit** names its fields explicitly - there is no way to pass a body or token to it.

Negative proofs (slice 1, each made with Edit and reverted): Guard's scope check off ->
the 403 test fails (200); `tools/list` unfiltered -> the read-token listing test fails;
rate limit off -> 200 instead of 429; no `X-Remote-User` -> operator None; the app's
operator pattern without `:` -> both operator tests fail; identity check accepting any
200 -> "DID NOT RAISE NotBss"; float check off -> the float tests fail on the message
(still refused by the schema).
