# Users: UI user management

Index: [README.md](README.md). Related: [internal-api.md](internal-api.md),
[frontend.md](frontend.md), [mcp.md](mcp.md), [overview.md](../overview.md#authentication-and-operator-identity).

## UI user management: the plan (2026-10-19)

**Approved 2026-10-19:** the human agreed to all Q1–Q7 proposed defaults. Implement
the backend slice first; commits and deployment retain their separate approval gates.
Written at the human's request:

> Create user menu in UI. Have two types of users: admin and user. Admin can create and
> manage other users and perform all UI operations. User can perform all UI operations
> except user management. User can change his password.

### Where we start

- **The app has no authentication.** Caddy (vps-infra, not this repo) asks for basic
  auth in front of everything but `/healthz` and `/billing-status`, and sets
  `X-Remote-User`, which the app shows as the operator - **display only, not proof of
  identity**: any container on the `shared` network, or a shell/SSH tunnel on the VPS,
  reaches the app directly (Open check in CLAUDE.md).
- Basic-auth users and their passwords live in vps-infra's Caddyfile: the app can neither
  create users nor change a password, and has no role to check.
- The `smoke` basic-auth user (GET/HEAD only, enforced by Caddy) runs
  `scripts/web-smoke.sh` after every deploy.
- The MCP service calls the app over the `shared` network with the service secret and
  `X-Remote-User: mcp:<app username>` (OAuth, with `X-Bss-Credential-Version`) or
  `mcp-token:<name>` (static token); its scopes are enforced in `bss_mcp`. Its OAuth
  login uses the app's users (Q7).

**Consequence:** roles cannot be enforced while identity is Caddy's. The app has to own
authentication - and then it must authenticate **every** request, not only the UI's,
or a user could do over `/tmf-api` what the UI denies.

### Proposed design

- **U1 Users in the app.** Table `app_user` (migration `0029`): `username` (unique,
  `^[A-Za-z0-9._@-]{1,64}$`, the operator name everywhere), `role` (`admin` | `user`),
  `password_hash` (stdlib scrypt, the parameters of `bss_mcp/passwords.py`: no new
  dependency), `disabled`, `must_change_password`, timestamps, `last_login_at`. Users
  are disabled, never deleted: their names stay on bill history and worker requests.
- **U2 Sessions.** `POST /internal/v1/session` (username, password) sets an HttpOnly,
  Secure (outside dev), `SameSite=Strict` cookie holding a random token; the server
  keeps only its hash in `app_session` (user, created, last seen, expires). `GET
  /internal/v1/session` answers who is signed in (name, role, must-change flag); `DELETE`
  signs out. Idle timeout and absolute lifetime from settings (Q5). Failed logins are
  rate-limited per user name and client address, as `bss_mcp/ratelimit.py` does.
  Disabling a user, a password change or an admin reset revokes their sessions.
- **U3 Enforcement in the app.** One dependency on every `/tmf-api/*` and
  `/internal/*` route: a valid session, or the service credential (U8). `/healthz`,
  `/billing-status` and the session endpoints stay open. A test enumerates
  `app.routes` so a new route cannot ship unprotected. 401 without credentials; 403 for
  a role that may not. Writes with a session cookie also need a custom request header
  the UI always sends (CSRF, with `SameSite=Strict`); it is pinned on both sides like
  every UI write.
- **U4 Roles.** `admin`: everything, plus `/internal/v1/users*`. `user`: everything else
  (all UI operations, the TMF APIs). Guards: the last active admin cannot be disabled or
  demoted; nobody disables themselves. **`mcp`** (issue #173, migration `0051`): the MCP
  connector's OAuth login only - no UI sign-in (the same 401 as a wrong password, and the
  session check refuses the role too) - and the only role MCP accepts
  ([mcp.md](mcp.md)). Its password is the one the administrator sets: it cannot sign in
  to make the forced change, so `must_change_password` is false on create, reset and a
  move to `mcp`, and true on a move from `mcp` to a UI role (CHECK
  `app_user_mcp_password_set`). A role change to or from `mcp` must carry a new password in
  the same PATCH (422 otherwise), so one password never serves both the UI and MCP;
  within `admin`/`user` none is needed.
- **U5 Users API** (admin only, internal API): list; create (username, role, initial
  password - an `admin` or `user` must change it at first sign-in, an `mcp` user keeps it); PATCH role, disabled, reset
  password (sets must-change again). Every change audited in the event/operator style
  already used (who, when).
- **U6 Own password.** `POST /internal/v1/session/password` (current, new). Policy: at
  least 12 characters, not the user name, no other rules (Q4). Other sessions of the
  user are revoked.
- **U7 Operator identity.** The session's username becomes the operator
  (`/internal/v1/context`, `requested_by`, bill hold/release history), replacing
  `X-Remote-User` from Caddy; for the service credential it stays the principal it
  states (`mcp:<name>`).
- **U8 Service credential** for the MCP service (and the smoke test, Q6): a shared
  secret in `.env.prod` for both services, sent as a header; the app accepts the
  principal header only together with it. This also closes the Open check "bss app is
  reachable without auth from any container on `shared`" for everything but
  `/healthz` and `/billing-status`.
- **U9 Bootstrap.** `python -m app.users create <name> --role admin` (also `user` or
  `mcp`; an `mcp` user is created without the forced change) reads the password
  from stdin, prints nothing secret; a make target locally and through `make remote` on
  the VPS (Makefile: harness file, the human's OK). Locally `make seed` creates no user;
  tests sign in through a fixture.
- **U10 UI.**
  - Sign-in screen (route `/sign-in`, not `/login`: Caddy routes `/login` to the MCP
    service). Any 401 sends the browser there and back after sign-in; a must-change user
    lands on Change password first.
  - **User menu** where the sidebar shows the operator today: name and role, *Change
    password*, *Users* (admins only), *Sign out*.
  - **Users screen** (`/users`, admins only; a user gets "not allowed"): list (name,
    role, active, last sign-in), New user, Edit (role, disable/enable), Reset password.
  - Write bodies pinned in component tests and `tests/test_ui_requests.py`; e2e signs in.
- **U11 Caddy.** With the app authenticating, Caddy's basic auth on the bss site is a
  second login. Recommended: a vps-infra change request removing basic auth from the
  bss site block (the app is the boundary), deployed **after** the app with
  authentication is live (until then both ask; nothing is ever open).

### Original questions (all proposed defaults approved)

- **Q1** Remove Caddy's basic auth once the app authenticates (U11, recommended), or keep
  it as an outer fence (two logins)?
- **Q2** Do roles matter beyond user management? Proposed: no - `user` may do everything
  in the UI and the TMF APIs except managing users. And MCP principals: they act as
  today (their scopes), never as admins.
- **Q3** Disable only, or also delete users? Proposed: disable only (history keeps the
  names).
- **Q4** Password policy: proposed at least 12 characters, not the user name; an admin
  reset forces a change at the next sign-in.
- **Q5** Session lifetime: proposed 8 hours idle, 7 days absolute.
- **Q6** The smoke test: a service credential limited to GET/HEAD (proposed), or an app
  user?
- **Q7** Should the MCP login use the app's users? **Yes** (issue #146, done): the
  connector asks `POST /internal/v1/service/credential-check`, grants carry
  `app_user.credential_version`, `MCP_USERS` is removed. See [mcp.md](mcp.md).

### Slices

1. **Backend** (migration `0029`): users, sessions, enforcement on every route, roles,
   the users and password endpoints, the service credential in `bss_mcp`, the bootstrap
   CLI. Tests per rule, negative proofs (a route without the dependency; a user calling
   a users endpoint; a disabled user's session), `make test` x3.
2. **UI**: sign-in, user menu, change password, Users screen; component tests, pinned
   bodies, e2e.
3. **Deploy** (each step the human's approval): `compose.prod.yaml` and `deploy.sh`
   (service credential, smoke) - harness files, diff first; the first admin on the VPS;
   then the vps-infra change request (U11). Rolling back `0029` drops users, sessions
   and their security audit records:
   needs `alembic downgrade 0028` by hand.

### Backend implementation details (2026-10-19)

- App-wide authentication dependency protects new routes automatically. The service
  identity root, health and billing status GETs, and sign-in POST are the explicit
  public allowlist. Interactive API documentation HTTP endpoints are disabled; the
  in-process OpenAPI document remains available to tooling. No unauthenticated mode.
- Cookie name `bss_session`, path `/`, HttpOnly, SameSite Strict; Secure outside dev/test.
  All session writes, including sign-in, require `X-Bss-Request: 1`. Password changes
  revoke **all** sessions, including the current one; sign in again afterward.
- `BSS_SERVICE_SECRET` authenticates MCP with `X-Bss-Service` plus a validated
  `mcp:<name>` in `X-Remote-User`. `BSS_SMOKE_SECRET` is a separate read-only credential.
  Unset credentials never authenticate. Neither can access user or session endpoints.
- Users are never deleted. Role changes also revoke sessions. Security changes are
  recorded in `app_user_audit` with target, operator, action and injected timestamp;
  passwords, hashes and session tokens never enter the audit or response.
- User administration and own password changes serialize with an exclusive table
  lock, including concurrent login, to protect the last administrator invariant.
- Session and user operations are explicitly excluded from MCP generation, separately
  from progress exclusions. Filter introspection runs handlers inside an isolated
  in-process app; it does not disable the running application's authentication.
- Login attempts are limited to 20 per minute per username and peer address. State is
  bounded and process-local (a restart refills it); forwarded headers are not trusted.
  Behind Caddy the peer bucket is shared, providing a conservative global limit.
- Bootstrap: `docker compose run --rm --no-deps app python -m app.users create NAME
  --role admin`, password read from stdin (no echo from the CLI). The make target and
  production credential wiring remain in the deployment slice, requiring harness
  approval. Local seed uses a temporary service credential only inside its process;
  it creates no users or persistent credentials. Smoke and UI clients must be adapted
  before this is deployed.

Negative proofs (each temporary mutation reverted): removing the global dependency
fails both route enumeration and a new-route request (200 instead of 401); removing
the admin guard lets an ordinary user list users (200 instead of 403); removing the
disabled-user predicate keeps a disabled user's existing session alive (200 instead
of 401). The tests observe these failures through real requests and sessions.

Validation: `make test` passed three times (1,345 tests each); `make lint`,
`make web-lint`, `make web-test` (236 tests in UTC and again in New York),
`make decisions-check` and `make docs` passed. The human approved and applied the
documentation generator mappings and the internal API authentication note.
`make progress` passed too (another full backend run and the UI inventory tests),
recording all three new tables and seven new endpoints as implemented and tested.

### UI implementation (slice 2, 2026-10-19)

`SessionGate` checks the cookie session before mounting the shell or loading context.
Protected-request 401s clear the UI and navigate to `/sign-in?next=...`; failed sign-in
stays in its form. Return paths must be local absolute paths, without backslashes or
whitespace, and cannot loop to sign-in or password change. Required password changes
replace the entire protected UI. Successful password changes return to sign-in, matching
the backend's revocation of every session. The operator menu uses session identity and
role, not a context-header identity. No browser storage holds credentials.

`/users` checks the session role before mounting the users list. Administrators create,
edit role/disabled state, and reset passwords. Edits send only changed fields. Editing
one's own security settings returns to sign-in; self-disable is unavailable in the form.
Server rejections (including the last active admin guard) leave the form open. All UI
writes send the CSRF header; users PATCH uses `application/json` from the internal
contract, separately from TMF merge patches. Exact bodies and headers are pinned in
component tests and replayed through real cookie sessions in `test_ui_requests.py`.

The browser fixture is separate from seed: random local admin/user identities, with
credentials in ignored `frontend/.e2e/credentials.json`. It refuses environments other
than `dev` and database hosts other than `db`. Cleanup disables users, revokes sessions
and removes credentials; user records and audit history remain. The Makefile wiring
is a harness change and awaits separate approval. Production wiring remains slice 3.

UI negative proofs: removing the CSRF header fails five pinned request assertions;
bypassing the Users screen's admin check fails the non-admin denial test. Both
temporary edits were reverted. All 250 frontend tests passed in UTC and New York,
and all four browser tests passed against the built UI and real local API (twice,
including after the final password-screen layout changes). Backend lint, frontend
type/lint/format checks and the decision-link check passed. Verified again before the
commit (2026-10-19): `make test` 1,346 passed, `make web-test` 250 in both zones,
`make progress` regenerated. `make web-e2e` cannot run through make until the fixture
is wired (a compose mount for `/e2e` and a Makefile prepare/cleanup step).

### Deployment wiring (slice 3, 2026-10-19)

Applied with the human's OK. `1c9f216` had gone live before this wiring existed, so
production MCP answered 401 and the web smoke could not pass.

- **Secrets** come from `.env.prod`, never from a committed file: `compose.prod.yaml`
  passes `BSS_SERVICE_SECRET` to `app` and `mcp` and `BSS_SMOKE_SECRET` to `app`, each
  `${VAR:?...}`, so a missing one stops the deploy. `.env.example` documents both
  (`openssl rand -hex 32`, two different values). A first draft by hand put literal
  values into `compose.prod.yaml`; they were replaced before any commit and regenerated.
- **Web smoke** (`scripts/web-smoke.sh`): Caddy's basic auth stays until the vps-infra
  change (U11), so the smoke sends both the basic-auth login and `X-Bss-Service` with the
  smoke secret. New check: Caddy's login alone gets a 401 from the app. The operator
  check keeps comparing with the basic-auth user name: the app names the smoke principal
  `smoke`, the same name. `deploy.sh` reads `BSS_SMOKE_SECRET` from `.env.smoke` (the same
  value as in `.env.prod`) and refuses to start when it or either `.env.prod` secret is
  missing.
- **Browser tests**: `make web-e2e` runs `tests/e2e_users.py` in the `app` service with
  `frontend/.e2e` bind-mounted as `/e2e` (both containers run as UID 1000; no compose
  change). A cleanup before `prepare` clears an interrupted run; the cleanup after the
  run disables the users and removes the file whatever the tests' result, and its own
  failure fails the target. Proven: 4 browser tests passed, the file was gone, every
  `e2e-*` user disabled and none with a session.
- **First admin on the VPS**: by the human, `python -m app.users create NAME --role
  admin` through `make remote` (password on stdin).

### After U11: the plan (slice 4, 2026-10-19)

**U11 is live** (vps-infra `7926cc2`, 2026-10-19, the human's OK): Caddy no longer asks
for basic auth on the bss site (`:8445` and `bss2811`); the app is the only boundary.
vps-infra verified, without credentials on both hostnames: `/`, `/subscribers` -> 200
`index.html` (build `7272ffc`); `/internal/v1/context`, `/tmf-api/...` -> 401 JSON from
the app; `/healthz` 200; no `WWW-Authenticate`.

**Caddy facts** (rehearsed by vps-infra on the pinned image):
- `X-Remote-User` is **stripped** on every route (`header_up -X-Remote-User`), never set.
- `X-Forwarded-For`: Caddy's default, no `trusted_proxies` - it **replaces** any
  client-sent value (forged, repeated, comma lists) with the single address it sees.
  The last entry is the real peer; a client cannot choose it.
- `bss2811` is the same site block as `:8445`, so `/internal/v1/session` is reachable
  there too; those requests come through the host nginx, so Caddy sees the docker
  gateway for every client: **all sign-ins on that route share one address bucket**
  (accepted; document it).
- The smoke credential's POST 403 now comes only from the app (`app/auth.py`).

**Broken on production until this ships** (accepted by the human): billing runs started
from the UI record `requestedBy = null`; every sign-in shares Caddy's address in the
throttle; `web-smoke.sh` fails at check 1 (anonymous `GET /` is 200 now), so **`make
deploy`/`make verify` fail at the web smoke** - this change is what unblocks deploys.

**The change** (one slice; the human's OK on the plan before code, harness files with
the Edit tool):

1. **Operator.** `app/internal/billing_runs.py` `_operator()` reads `X-Remote-User`
   (display-only, from the Caddy era) - replace it with `request.state.operator`, the
   source `/context` and bill history use (session username, `mcp:<name>`, `smoke`).
   MCP keeps sending `X-Remote-User` directly on `shared` with the service secret;
   `auth.py` already validates that path. Tests: `tests/test_worker_queue.py:143`
   sends `X-Remote-User: i.petrova` - the same name the fixture signs in with, which is
   why the bug went unseen: send a **forged** name there and assert `requestedBy` is
   the session user; an MCP-principal case. Negative proof: the old `_operator()` fails.
2. **Throttle.** `limit_login` (`app/internal/users.py:76`) keys on
   `request.client.host` (uvicorn has no `--proxy-headers`: always Caddy's address)
   plus the bare username. New setting `APP_TRUST_FORWARDED` (default false; `"true"`
   in `compose.prod.yaml`'s `app`): the client address is the **last**
   `X-Forwarded-For` entry, else the peer address (loopback, SSH tunnel) - the same
   rule as `bss_mcp/login.py:client_ip` (`MCP_TRUST_FORWARDED`); write the helper in
   `app/` (app must not import `bss_mcp`). Keys: **(username, address)** and
   **address**; drop the bare username key, so nobody can lock out a named user from
   elsewhere. Keep the 20/minute limit, `Retry-After: 60` and the 10,000-key bound
   (with a trusted address one source cannot fill it). Tests in `tests/test_users.py`
   (replace `test_rate_limit_by_username_and_address`): one address limited across
   usernames; another address still signs in the same user; a forged multi-entry
   header (`X-Forwarded-For: 1.2.3.4, 5.6.7.8`) is keyed on its last entry; with the
   flag off the header is ignored. Negative proof: keying on the first entry fails.
3. **`scripts/web-smoke.sh`, new state only** (U11 is live, so no two-mode script):
   anonymous `GET /` -> 200 with this build's `index.html`; deep link and asset as
   before; anonymous `GET /internal/v1/context` -> 401 **JSON** from the app (not
   `index.html`); the smoke secret alone (`X-Bss-Service`) -> 200 JSON, operator ==
   `"smoke"` (a literal); `/tmf-api/accountManagement/v5/billingAccount?limit=1` with
   the secret -> 200 JSON + `X-Total-Count`; POST with the secret -> 403 from the app;
   `/healthz` 200. Drop the "Caddy's login alone -> 401" check and **`SMOKE_BASIC_AUTH`
   everywhere**: `web-smoke.sh`, `scripts/deploy.sh` (no longer required in
   `.env.smoke`; its `.env.prod` guard against that line and the dry-run message can
   go), `.env.example`, `docs/operations.md`, `docs/decisions/infra.md`. The smoke
   cannot be rehearsed locally (no Caddy): `bash -n`, then the deploy's own run.
4. **Docs.** The Caddy facts in `infra.md` (done on the infra side); this file (as built, the bss2811 throttle bucket); CLAUDE.md
   (no more "two logins"; the deploy-blocked state resolved).
5. **Ship**: lint, `make test` x3, the human's OK on the diff, commit, push, `make
   verify` (the human's approval). **Then tell vps-infra** the web smoke passed: it
   deletes `BASIC_AUTH_HASH_BSS`/`_BSS_SMOKE` from its `.env.prod` (kept for rollback
   until then) and closes its open check; the human deletes the `SMOKE_BASIC_AUTH`
   line from `.env.smoke`.

### After U11: as built (slice 4, 2026-10-19)

Built to the plan above, which the human confirmed ("continue").

- **Operator.** Billing run requests (`catchUp`, `retry`) take `requestedBy` from
  `operator_of(request)` (`app/api/common.py`), the same source as `/context` and bill
  history; `_operator()` is gone. Until now they were the one place a client-sent
  `X-Remote-User` still reached the database. Tests: a forged header with a session
  records the session user (fails on the old code); an MCP retry records `mcp:demo`.
- **Throttle.** `client_address` (`app/internal/users.py`): with `APP_TRUST_FORWARDED`
  (`"true"` in `compose.prod.yaml`'s `app` only) the last `X-Forwarded-For` entry, else
  the peer - `bss_mcp/login.py`'s rule, written again because `app/` does not import
  `bss_mcp`. Keys `(user, username, address)` and `(address)`, 20 per minute each,
  `Retry-After: 60`, at most 10,000 keys. On `bss2811` every client shares the docker
  gateway's bucket (accepted). Negative proofs, each failing a test and reverted: the old
  keys (a user locked out from elsewhere), the first entry instead of the last, the
  header trusted with the flag off.
- **Web smoke** in the new state only (`scripts/web-smoke.sh`, 7 checks); deploy.sh needs
  only `BSS_SMOKE_SECRET` in `.env.smoke` and no longer looks for `SMOKE_BASIC_AUTH`.
  Checked with `bash -n` only - the first real run is the deploy's.
- **Deployed** with `b4ccd63` (2026-10-19): the web smoke passed 7/7 through Caddy. The
  plan had missed `scripts/mcp-smoke.sh`'s check 6 ("everything but the carve-outs ->
  401 Basic"); since U11 those paths are the bss site, so it now expects the UI's
  `index.html` and no Basic challenge (the carve-outs stay proven exact), 12/12 against
  production. vps-infra then deleted its basic-auth hashes, the human the
  `SMOKE_BASIC_AUTH` line in `.env.smoke`.
- **Not changed:** `worker_request.requested_by`'s column comment still says "from
  X-Remote-User" (migrations on `master` are immutable; a comment-only migration can fix
  it when the next one is due).
