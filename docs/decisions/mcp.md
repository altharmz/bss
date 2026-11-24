# MCP connector

User-facing description: [docs/mcp.md](../mcp.md).

## Review slice A: tool schemas (2026-10-19)

The MCP end-to-end review found schema gaps, without requiring an API or billing
policy change. The registry now carries body attributes consumed before model parsing
and per-filter schema overrides. `patch_order` exposes nullable `state`, with the
non-null value `acknowledged` to submit a draft; its description distinguishes draft
edits from processed orders. Repeated submission retains the API's existing 422;
changing a processed order's start date is 409 (the review had expected both as 409).
A source-based test checks literal `body.pop` keys in exposed write handlers against
the generated schema. Removing the body-attribute merge made it fail on `state`;
the negative-proof mutation was reverted.

The filter sweep found one date/time equality filter, bill cycles' `billingDate`.
It now declares `format: date-time`, an offset-bearing example and the `BILLING_TZ`
midnight requirement. Unknown registry filter overrides stop generation with
`DriftError`; accepted filter names still come from the handler probe.

Passthrough fields marked "as the spec defines it" gain compact descriptions from
the vendored FVO schemas, including referenced type, array/object shape and inherited
required keys. Shared attribute names can name alternative FVO shapes; these are hints,
and runtime spec validation remains authoritative. Full polymorphic schemas are not
inlined. TMF622's lenient `externalId` model is unchanged.

Server instructions name the write-scope and read-scope tool counts, the API families,
and searching by family when a client's discovery layer shows fewer. The inventory
family no longer says read-only. The counts live only in `bss_mcp/server.py`, where a
test checks them against the generated tools; this record deliberately does not repeat
them, so adding a tool needs no edit here. See the shared
[documentation count rule](harness.md#documentation-no-hand-written-counts).
The human subsequently approved same-day status undo and future-product termination
(slices B/C); their decisions are recorded in `tmf-api.md` and `worker.md`.

## Slice 1: server, static tokens, scopes (2026-10-11)

Decisions as taken (plan D1-D6 agreed with the human):

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
  `app/main.py`; `tests/tmf/test_operation_ids.py` keeps them unique, `^[a-z0-9_]+$` and at
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

## Slice 2: OAuth 2.1 (2026-10-11)

- **The SDK's authorization-server routes** (`create_auth_routes`) on our provider
  (`bss_mcp/oauth.py`), not a hand-rolled OAuth: the SDK validates the requests, checks
  PKCE S256 and the redirect URI at /token, refuses `plain`. Three adjustments, each
  in `bss_mcp/routes.py` with its reason:
  - the RFC 8414 metadata is served by us with `none` added to the auth methods (MCP
    clients register as public clients; the SDK accepts them but does not list it);
  - the issuer is passed as a string so it stays exactly `https://host` (an `AnyHttpUrl`
    becomes `https://host/`);
  - `/revoke`: mcp 2.3.0 types `client_secret` as `str | None` *without a default*, so a
    public client could not revoke (400) - an absent one is sent on as empty
    (`PublicClientRevoke`).
- **Expiry with the injected clock only.** The SDK compares code and refresh expiry with
  `time.time()`; the provider hands it models without an expiry (codes: `inf`) and the
  store decides with `bss_mcp/clock.py`. Expiry is therefore testable, and a frozen test
  clock never meets the wall clock.
- **Login and consent are one page** (`/login`): the carve-out list stays at the paths the
  human named (no `/consent`). The `request` id is the only handle on the parked
  /authorize request - unguessable, single use, 10 minutes, 5 wrong passwords - and so
  the form's CSRF token. No `form-action` in the CSP: Chrome applies it to the redirect
  after the POST, which goes to the client.
- **Users** (issue #146): the app's. `POST /internal/v1/service/credential-check` (service
  credential only) answers 200 `{username, role, credentialVersion}` or 401
  `invalidCredentials` (unknown, wrong password, disabled and a UI role look the same; an
  unknown user costs a scrypt too); it uses the app's sign-in rate limiter.
- **Role `mcp`** (issue #173, decided 2026-11-05 by delegation of the human): the only
  role MCP accepts; it cannot sign in to the UI (users.md U4). `credential-check` refuses
  `admin` and `user` with the same 401; `check_principal` also requires the role on every
  call, a second line behind the credential version. An `mcp` user never carries
  `must_change_password` (CHECK `app_user_mcp_password_set`), so the 403
  `passwordChangeRequired` answer and the login page's "change your password in the BSS
  UI" are removed: unreachable code with a database guarantee behind it. Static tokens
  are not users and are unchanged; scopes stay the consent choice. Deployment: no role
  migration - the migration cannot tell which `user` accounts are meant for MCP, and
  guessing would grant or remove access silently. The #146 `user` accounts lose MCP at
  the deploy until the human converts them (role `mcp` + new password, [docs/mcp.md](../mcp.md#production)):
  a short planned gap on an administrative channel, safer than UI credentials on MCP. Grants carry the user's `credential_version` (`app_user`, migration `0048`, +1
  on password change, reset, role change and disable). Principals sent to the app: `mcp:<username>`
  with `X-Bss-Credential-Version`, checked on every request (401 `credentialRevoked`: the
  connector ends the grant and answers the client 401 `invalid_token`), and
  `mcp-token:<name>` for static tokens, not checked against users. Grants from before
  the change (no stored version) are deleted at start. `MCP_USERS` is gone.
- **Scopes**: registration defaults to `read write`; consent narrows to read or keeps
  write (only if the client asked for it); a token's scopes are `[read]` or `[read,
  write]`; a refresh can narrow, never widen (the SDK's check).
- **Tokens**: opaque random strings, SHA-256 in SQLite; refresh rotates (the old one is
  spent); revoking a refresh token revokes its grant. Redirect URIs: https, or http on
  localhost/127.0.0.1/[::1] - custom schemes refused (the human's policy; a client
  needing one, e.g. some desktop apps, will not connect).
- **Rate limits on the auth endpoints** per client IP (and failed logins per user name);
  behind Caddy the IP is the last `X-Forwarded-For` entry (`MCP_TRUST_FORWARDED`, set in
  prod in slice 4).

Negative proofs (slice 2, each made with Edit and reverted): redirect-URI policy off ->
the 5 refusal cases fail; password check passing any known user -> wrong-password,
other-user's-password and the password unit test fail; client name unescaped -> the
escaping test fails; per-IP limit off -> no 429; consent offering write to a read-only
client -> its test fails; refresh not rotated -> the old refresh token works again;
grant not revoked with its refresh token -> the access token still works; token expiry
ignored -> 200 after 3601 s. `use_code` always succeeding did **not** fail the "code
works once" test - the spent code is already gone when loaded; its rowcount guards the
race of two exchanges that both loaded the code first, so a test for exactly that was
added, and it fails without the guard ("DID NOT RAISE TokenError").

## Slice 3: tools generated from the operations (2026-10-11)

- **Generated at startup, in process**: `bss_mcp.tools.generate(app.main.app)` in the
  lifespan, from `app.openapi()` plus the registry `bss_mcp/operations.py` (D2). Not at
  build time: the image carries the app's code anyway, and a registry out of step stops
  the service (`DriftError`) rather than serving a wrong tool set.
- **The registry holds what app.openapi() lacks** (the app parses query parameters and
  bodies itself): the request model per write, the query shape per read (TMF list, TMF
  get-one, paging, none, plus extra parameters), a one-line note. `retry` is the one POST
  without a body (`no_body`).
- **List filters are probed, not listed**: a TMF list answers an unknown query parameter
  with its filters before it opens a database connection - the same probe `make docs`
  uses (`tests/docs_collect.py`), here through httpx2's ASGI transport. The filters
  object then names exactly those attributes (`additionalProperties: false`).
- **Exclusions = `scope.yaml`'s**: `EXCLUDED` (today `GET /`) is a copy because the runtime
  image has no `docs/`; the drift test reads `docs/progress/scope.yaml` (mounted into
  the test container, compose.override.yaml - approved 2026-10-11) and requires equality.
  `/healthz` and `/billing-status` are read tools.
- **Descriptions are curated, not copied**: 51 of 58 handlers have no docstring, and the
  API notes (`scripts/docs/api-notes/`) are long and full of test references. Each
  operation gets one line condensed from them; the generator adds the summary, the API,
  the docstring if any, filters, merge-patch and write-scope hints. Kept short: the
  whole `tools/list` is about 60 KB.
- **Every Decimal is a decimal string**, not only `Money.value`: `PriceChange.amount` is a
  bare Decimal. Pydantic's Decimal schema (number or numeric string) is replaced
  wherever it occurs; the request models have exactly these two Decimal fields, both
  with the 2-decimal rule.
- **Bodies keep the TMF meta attributes** (`@type`, `@baseType`, `@schemaLocation`): the
  app accepts them and some handlers check `@type`.

Negative proofs (slice 3, each made with Edit and reverted): a new route in
`app/main.py` -> the drift test fails and the connector refuses to start ("no entry for
['drift_probe']"); `EXCLUDED` with `/healthz` added -> "EXCLUDED == scope.yaml" fails;
`patch_bill` registered with `CustomerPatch` -> "handler parses ['BillPatch'], registry
['CustomerPatch']"; the Decimal rewrite off -> the schema test finds a JSON number, and
an integer amount `10` reached the app and was stored (three behavioural tests fail).
The registry's own refusals (missing or unknown entry, a write without a body, a read
with one) are unit-tested.

## Slice 4: production service and routing (2026-10-11)

- **`compose.prod.yaml` service `mcp`** (diff approved): same image, one uvicorn worker
  with `--limit-concurrency 32`, 192 MB / 0.25 CPU (measured: ~100 MB in dev with the
  reloader, 73 MB prod-like after startup; scrypt adds 16 MB per login in flight), the
  app's hardening (read-only root, tmpfs, no-new-privileges), `restart: unless-stopped`,
  no published port, alias `bss-mcp` on `shared`, volume `mcp-data`. Budget: 384 + 256 +
  192 = 832 MB of the VPS's 1931 MB.
- **No `depends_on`** in prod (the base compose rule) and none needed: after the nightly
  reboot Docker restarts each container on its own; the identity check waits up to 60 s
  for the app and the restart policy covers a longer wait.
- **Exact carve-outs need an `expression` matcher**: Caddy's `path` matcher is
  case-insensitive - proven in the rehearsal (`/MCP` reached the connector with a `path`
  matcher). The human's brief assumed the existing carve-outs were case-sensitive; they
  are not (harmless for `/healthz` and `/billing-status`, recorded in the change request).
- **X-Forwarded-For**: Caddy replaces a client-sent value (no `trusted_proxies`), so the
  connector trusts the last entry (`MCP_TRUST_FORWARDED=true` in prod only) - proven:
  a spoofed `6.6.6.6` was audited as the real client.
- **The deploy proves the routing**: `scripts/mcp-smoke.sh <url> caddy` after the web
  smoke test - no credentials, nothing changed; it checks both the open paths and that
  `/`, `/MCP`, `/Mcp`, `/mcp/x`, `/authorize/x` and an upper-case well-known path still
  get the Basic challenge. `make mcp-smoke` runs the local half.
- **The change request keeps the applied stage-5 request** below the new one (infra.md
  links to it as the reference for the current block).

Rehearsal and proofs (Caddy 2.11.4 on the workstation, prod-like `mcp` from the runtime
image): the smoke test passes through Caddy; with a `path` matcher it fails ("GET /MCP
answered 404 without basic auth"); with `/token` dropped from the list it fails ("POST
/token answered 401 Basic"). The prod composition validates (`config --quiet`, dummy
values).

## Slice 5: token tooling and client docs (2026-10-11)

- **One CLI, `python -m bss_mcp.tokens`** (`create`, `list`, `revoke`, `revoke-grant`),
  run in the `mcp` service so it opens the very store the server reads (SQLite WAL; the
  server looks a token up on every request, so a revocation is immediate - no cache).
  The token goes to stdout alone, everything else to stderr, so it can be piped.
- **Make targets locally, `make remote SERVICE=mcp` on the VPS**: `make remote` gained a
  `SERVICE` (default `app`) instead of a second set of remote targets; `--no-deps`, so
  the app need not be up.
- **A production token is created by the human**, not by a session (it would land in
  the transcript); CLAUDE.md says so next to the rule that the connector bypasses the
  guard.
- **Cursor gets static tokens only**: its OAuth redirect is a custom scheme, which the
  agreed redirect policy refuses; the policy stays.
- **Not tried against the hosted clients yet** - they need the deployed instance. The
  client instructions in docs/mcp.md follow each client's documented setup; the OAuth
  flow itself is proven with the official SDK client.

## P5b: MCP for prepaid (2026-10-23)

The TMF654 read/write tools and the internal previews (`first_charge`, the prepaid
command tools) existed since P4a-P4i because every API operation is a tool; P5b adds what
the plan still asked for.

- **`idempotency_key`** is an optional top-level argument (not inside `body`) of exactly
  the six POSTs whose handler keeps an Idempotency-Key journal: `create_order`,
  `create_payment`, `create_topup_balance`, `create_adjust_balance`,
  `create_reserve_balance`, `create_transfer_balance`. The registry marks them with
  `Op(idempotent=True)`; the connector sends the value as the `Idempotency-Key` header
  (`Bss.request(headers=...)`) and the app's journal does the rest (same key and body:
  the first answer; same key, other body: 409). The schema is 1-255 visible ASCII, so a
  malformed key is refused before the app. No key means no journal, as over HTTP. The
  journal is scoped by operator, and the connector's operator is `mcp:<token name>`.
  Any new journaled POST must set `idempotent=True` (tests/mcp/test_mcp_prepaid.py pins the
  list). It is optional, not required: the API does not require it either.
- **Decimal strings** needed nothing new: the TMF654 amounts are `Quantity` strings and a
  JSON number with a fraction is already refused by the connector.
- **`make mcp-smoke`** runs the old no-credential checks, then
  `scripts/mcp-prepaid-smoke.py`: it creates a throwaway local write token, builds a plan,
  customer and prepaid account through the tools, records one top-up (20.00) and one
  future-start purchase (the offering cannot start in the past, so the plan starts at the
  next UTC midnight and the order is requested for noon the day after), each sent twice
  with its key to prove the replay, and revokes the token. The script refuses any host
  but localhost, and `deploy.sh` still runs only the credential-free `mcp-smoke.sh`.
  Decision: the local app never accepted the connector (compose passed no
  `BSS_SERVICE_SECRET`), so `compose.override.yaml` now passes `BSS_SERVICE_SECRET`
  (from `.env`, same value for app and mcp) to both; with it empty the prepaid part
  prints SKIPPED and the target still passes. *Superseded by issue #174, below.*
- **Authenticated OAuth smoke** (issue #174, 2026-11-05): `scripts/mcp-oauth-smoke.py`
  logs in the way a client does - register, authorize with PKCE S256, `/login` as an
  `mcp` user consenting to **read** only, token; `tools/list`, `get_context`, a write
  tool refused (403), refresh, revoke (in a `finally`), the revoked token refused (401);
  locally also an active `user` with the right password refused on `/login` (401). Python,
  not bash: PKCE and the login form are fragile in curl. `mcp-smoke.sh` stays
  credential-free.
  - **Local smoke users are throwaway**, not seeded: `make mcp-smoke` creates
    `mcp-smoke-<pid>-<time>` (role `mcp`) and `ui-smoke-...` (role `user`) with random
    passwords through `python -m app.users create` and disables both afterwards
    (`python -m app.users disable`, new; users are never deleted), as the prepaid part
    does with its throwaway token. Why not the issue's "`make seed` creates it, password in
    the task secret file": U9 in [users.md](users.md) keeps users out of the seed, and a
    persistent local password would need a new secret key, its allowlist entry and its
    plumbing into the container - for nothing a per-run password does not give.
  - **No skip locally**: `make start-task` now writes a random `BSS_SERVICE_SECRET` into
    a new task's secret file (app and mcp share it). An older task file lacks it; the
    prepaid part then **fails** with the line to add instead of printing SKIPPED.
  - **Production**: `deploy.sh` runs the OAuth smoke through Caddy after `mcp-smoke.sh`
    with `MCP_SMOKE_USER` and `MCP_SMOKE_PASSWORD` from `.env.smoke` (read by the script
    only, never printed). Without them the step prints SKIPPED and the deploy goes on.
    The UI-user refusal is checked locally only (no UI password belongs in `.env.smoke`).
  - **What it writes**: one client registration (`client_name` `bss-mcp-smoke`) with its
    pending request, code and tokens, and the user's `last_login_at`. The grant is revoked
    in the script. There is no client deletion endpoint (RFC 7592 is not implemented), so
    each run would leave a registration against `MCP_MAX_CLIENTS` (1000; at the cap new
    clients cannot register). **Pruned** (the human chose this, 2026-11-05):
    `python -m bss_mcp.tokens prune-smoke-clients` deletes registrations named exactly
    `bss-mcp-smoke` that hold no live (unrevoked, unexpired) token, with their rows; a
    parked login link or unused code of such a client dies with it. `deploy.sh` runs it
    in the `mcp` container after the OAuth smoke (a failure only warns: everything is
    deployed by then); `make mcp-smoke` runs it too and fails unless it pruned the
    registration just made. The name is fixed in the code (`SMOKE_CLIENT_NAME`; a harness
    test pins the script's copy), never an argument. Real clients never use it; one that
    copies the self-declared name loses only a registration without a live grant.
    Rejected: wiping the store (ends every connection and static token); expiring unused
    clients (claude.ai keeps its client id, and a forgotten one may not reconnect).
  - **The smoke credential can obtain write**: scope is the consent choice, not the role,
    so `MCP_SMOKE_PASSWORD` could get a read+write grant. Accepted: it is an `mcp` user's
    password like any other, kept in the gitignored, never-shipped `.env.smoke` beside
    `BSS_SMOKE_SECRET`; the smoke consents to read only; every call is audited as
    `mcp:<smoke user>`; the human can disable the user at any time. A per-user scope cap
    would be a data-model change and is not justified for a demo instance.
