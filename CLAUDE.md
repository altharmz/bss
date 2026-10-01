# bss

This project was generated from a shared template (`vps-lab-template`). `make init
NAME=... APP_PORT=... HTTPS_PORT=... DOMAIN=...` filled in the project name, the local/VPS
app port, the HTTPS port, and the public domain everywhere they appear (compose files,
Makefile, scripts, this file, README.md), created `.env` and `.env.prod` from
`.env.example` with a generated database password, and then removed itself from the
Makefile so it can't run twice. If an `init:` target still exists in the Makefile, or a
double-underscore token still shows up in a `grep -r` over the repo, `make init` was never
run — do that before anything else, not a manual find-and-replace, since it also creates
the `.env` files and only it knows to remove itself afterward.

FastAPI + PostgreSQL service. Developed locally in WSL2, verified on a remote VPS.

## Environments

| Where | What runs there |
|-------|-----------------|
| WSL2 (Ubuntu) | full dev loop: build, run, tests, lint |
| VPS `vps-test` (Debian 12, 1 vCPU, 1931 MB RAM, 30 GB disk) | verification only — pre-built image, no build |

**Images are built locally and shipped to the VPS. Never run `docker build` on the VPS** —
it has one core and a production VPN server running next to us.

The runtime image is about 74 MB compressed (content size) and 317 MB on disk on the VPS,
321 MB locally (checked at `921603f`): Debian base 88 MB, Python 41 MB, `/opt/venv` 112 MB,
app + alembic ~120 kB. Nothing beyond the venv and app code is in it; inside the venv, pip
(13 MB) is not needed at runtime and sqlalchemy (28 MB) is there only for Alembic.

## Commands — use these, do not invent your own

```
make up        # start local stack (app + postgres) with hot reload
make down      # stop local stack
make logs      # last 5 min of app logs
make test      # pytest inside the container
make lint      # ruff check + format check
make smoke     # smoke-test the local instance
make deploy    # build locally, ship image to VPS, restart there
make verify    # deploy + wait for healthy + remote smoke test
make ssh       # open a shell on the VPS
make migrate   # apply Alembic migrations against the local stack
make fmt       # auto-format (ruff format + check --fix)
make clean     # stop the local stack AND drop the local database volume
make tunnel    # forward the remote app to localhost:18081
make remote CMD="..."  # run a one-off command against the deployed image on the VPS
make hooks     # install the local pre-commit hook (make lint + make test)
make spec-verify-upstream  # re-fetch spec blob SHAs from GitHub, compare with spec/README.md (network)
make seed          # LOCAL demo data through the public TMF API (refuses APP_ENV=prod); re-run = no-op
make web-install   # UI: npm ci into the node_modules volume - the ONLY step that downloads
make web-dev       # UI: Vite dev server on http://localhost:5173 (proxies /tmf-api, /internal)
make web-build     # UI: build frontend/dist (offline)
make web-lint      # UI: tsc, eslint, prettier --check (offline)
make web-test      # UI: vitest (offline)
make web-types     # UI: regenerate TypeScript types from spec/ (offline)
make web-fmt       # UI: prettier --write (offline)
```

`make verify` is the single source of truth for "does it actually work on the server".
It exits non-zero and prints remote logs when anything fails.

## Pre-commit hook

`scripts/pre-commit.sh` runs `make lint` then `make test` (~40s) and blocks the commit on
failure.

- Not installed by default — `.git/hooks/` is never tracked by git, so every clone (this
  one included) needs one `make hooks` run. It installs a symlink, not a copy, so edits to
  `scripts/pre-commit.sh` take effect immediately without reinstalling.
- Runs non-interactively: both `make` calls are given `</dev/null` so the hook never
  blocks waiting on stdin, whether it fires from a terminal, an editor's git integration,
  or any other non-interactive caller.
- To skip it deliberately (e.g. a WIP commit): `git commit --no-verify`. The hook's own
  failure output reminds you of this.
- When the staged files touch `frontend/` or `spec/`, it also runs `make web-lint` and
  `make web-test` (offline, ~+30 s: lint 20 s, test 9 s). They never install: an out-of-date node_modules fails
  with "run make web-install". A change in `app/api` or `app/internal` alone does not
  trigger them, though it can break the UI; `make verify` (`web-e2e`) is the gate for that.
- It tests the **working tree, not the index**. With a partial commit (some changes left
  unstaged or untracked) the hook passes or fails on content the commit does not contain.
  To verify one commit in isolation, check it out on its own: stop the local stack
  (`make down`; port 5432/8080 conflict), `git worktree add ../bss-check <commit>`, then
  in that directory `COMPOSE_PROJECT_NAME=bss-check IMAGE_NAME=bss-check-app
  POSTGRES_PASSWORD=throwaway make lint test` (compose.yaml pins `name: bss`, so without
  the override the worktree would share this project's containers and database volume),
  then `docker compose down -v` with the same variables and `git worktree remove`.

## Hard constraints

- The VPS also runs a **SoftEther VPN server**, which is its primary purpose.
  Ports **443, 992, 1194, 500, 4500, 5555** are off limits. Do not touch, inspect
  or reconfigure anything VPN-related.
- Never run `docker system prune`, `docker volume rm`, `docker network prune`,
  or stop/remove containers you did not start. Other people's containers may be there.
- On the VPS the app binds to `127.0.0.1:18081` only. Nothing is published to
  0.0.0.0. To reach it from the workstation use an SSH tunnel: `make tunnel`.
- On the VPS this project lives in `~/bss` (`REMOTE_DIR`, filled in by `make init`).
  Every app from this template gets its own directory; never point two projects at the
  same one — each deploy overwrites `compose*.yaml` and `.env` there.
- Remote commands go through `scripts/deploy.sh` only. Do not open ad-hoc `ssh vps-test '...'`
  sessions that mutate state.
- Never read, print or commit `.env` / `.env.prod`. Use `.env.example` as the reference.
- Never export `.env.prod` variables into a shell that also runs the local stack (e.g.
  `set -a && . ./.env.prod`) — exported variables take precedence over `.env`, so local
  containers silently pick up production values. For a one-off check against prod
  variables, scope them to a single command instead: `env $(grep -v '^#' .env.prod | xargs) <command>`.
- Do not `git push`. Commit locally; the human pushes.
- **Do not add `db` or `caddy` services back to the production configuration**
  (`compose.prod.yaml`), and do not re-add a Caddyfile or backup scripts to this repo.
  On the VPS they live in the separate vps-infra project — see "Shared infrastructure".
- `make deploy` is no longer fully reversible once a migration has shipped. Rolling the
  image back to a previous tag restores the old code, but not the database schema — if a
  migration already ran, reverting the image leaves the schema ahead of the code. Rolling
  back the schema requires running `alembic downgrade <revision>` by hand against the VPS
  database; there is no automated rollback path.

## Agent guardrails: what they are for

`.claude/settings.json` (allow/deny rules) and `.claude/hooks/guard.sh` protect against
**agent mistakes, not a hostile agent**. They stop the obvious slip - reading `.env`, a
destructive docker cleanup, touching the VPN - and explain why. They are not a security
boundary:

- **Docker access is root-equivalent.** `docker compose:*` and `make:*` are allowed, and
  anyone who can start a container can bind-mount any host path into it, read or write any
  file the docker daemon can, and reach every other container on the host.
- **`.env` values are reachable despite the deny rules.** `docker compose config` prints
  the compose file with every `${VAR}` already substituted, and `docker compose run ...
  env` (or `exec ... env`) prints a container's environment. The `Read(./.env)` deny rule
  and the guard's `cat .env` pattern only cover the direct route. Do not run those
  commands; `docker compose config --quiet` validates without printing.
- **`guard.sh` is a pattern matcher over the command text.** It cannot see through
  variables, scripts, encodings or a command it has no pattern for.

- **Harness files need the human's OK.** `.claude/**`, `compose*.yaml`, `Makefile` and
  `scripts/**` are under `"ask"` for the Edit tool, and `guard.sh` blocks the shell writes
  that would skip that prompt (`>`, `>>`, `tee`, `sed -i`, `mv`, `cp` as the destination,
  `rm`). Same limit as above: a write through an interpreter, `git checkout`/`git apply`
  or a container mount is not caught. Change these files with the Edit tool only.
  The `"ask"` rules are unverified under auto mode; if they don't prompt there, these
  paths move to `"deny"`.

So: the rules in "Hard constraints" bind because they are the rules, not because
something enforces them. Anything that needs real enforcement (secrets an agent must
never see, hosts it must never reach) has to be kept out of the environment the agent
runs in, not filtered here.

## Layout

```
app/main.py               FastAPI app: /healthz and a JSON status root
app/config.py              settings from environment variables (pydantic-settings)
app/db.py                  psycopg 3 connection + the liveness probe /healthz calls
app/clock.py               Clock protocol, SystemClock, FixedClock; the only wall-clock reader
app/repository/            data access: plain functions on a psycopg.Connection, never commit
app/billing/               billing core (model.py dataclasses, core.py pure functions) and
                           adapter.py (bill_account: load -> compute -> write). See "Billing"
app/internal/              internal API for the admin UI (/internal/v1): context, first-charge
                           preview, open products, charges, transactions. See "Internal API"
app/api/                   TMF Open API surface: one module per API (router, request models,
                           row -> body mapping) + shared conventions (errors, tmfjson, common,
                           listing, deps). See "TMF API conventions"
spec/                      vendored TMF OpenAPI files (README.md: source, SHA, license);
                           spec/internal/bss-internal.oas.yaml is ours (the internal API)
alembic/                  migrations; alembic.ini reads DATABASE_URL from the env
compose.yaml              base definition: only what both modes share (the app image)
compose.override.yaml     local dev only (own postgres on `lab`, bind mount, --reload, ports;
                          `tools` service: writable mount for make fmt + format hook)
compose.prod.yaml         VPS only (shared network + alias, DATABASE_URL, limits, log rotation)
scripts/init.sh           one-time: fills in placeholders, creates .env/.env.prod
scripts/deploy.sh         build -> save -> ssh load -> migrate -> up -> healthcheck -> smoke
scripts/smoke.sh          HTTP smoke test against a base URL
scripts/pre-commit.sh     make lint + make test; installed via `make hooks`
frontend/                 admin UI (stage 5): Vite + React + TypeScript; see "Frontend conventions"
design-ref/               design handoff for the UI - read-only reference, never edited
tests/                    pytest suite (conftest.py spins up throwaway databases;
                          apiclient.py + contract.py validate every API response against
                          spec/; billing_sim.py: in-memory ledger for the billing core;
                          test_billing_properties.py: hypothesis);
                          test_health.py and test_migrations.py check the infrastructure
                          this template provides and must keep passing on an otherwise
                          empty service
```

`docker compose` picks up `compose.override.yaml` automatically, so plain
`docker compose up` is always the *local dev* stack. Production composition is explicit:
`-f compose.yaml -f compose.prod.yaml`.

Because `compose.yaml` is merged into both, it holds only what both modes share. The
local postgres (`db`, its healthcheck and `pgdata` volume), the app's `depends_on`,
`DATABASE_URL` and the `lab` network all live in `compose.override.yaml`; anything put
back into the base file would also end up in the production stack (a `lab` network there
would attach the prod container to both `lab` and `shared`).

## Conventions

- App listens on **8080** inside the container. Do not hardcode host ports in code.
- Config comes from environment variables only (see `app/config.py`). No config files.
- `/healthz` must stay cheap and must verify the DB connection — Docker's HEALTHCHECK
  and the deploy script both depend on it.
- Python 3.12, `ruff` for lint and format, `pytest` for tests.
- `make test` runs the suite in parallel (`pytest -n auto`, pytest-xdist). Tests share
  nothing: each worker migrates its own template database and every test gets its own
  clone, dropped afterwards (a run leaves no `vpslab_%` database behind). Hypothesis
  examples per property come from a profile selected by `HYPOTHESIS_PROFILE`: `dev` (50)
  for `make test` and the pre-commit hook, `ci` (200) for `make verify` (a target-specific
  variable in the Makefile). Plain `pytest` without the variable uses `dev`.
- **The local postgres runs with `fsync=off`, `full_page_writes=off`,
  `synchronous_commit=off`** (`command:` of `db` in `compose.override.yaml`): every test
  clones and drops a database, and with fsync on each drop waits for a server-wide
  checkpoint. Local only - production uses vps-infra's postgres and never reads
  `compose.override.yaml`, so it is unaffected. Not crash-safe: after a WSL crash (or a
  killed Docker VM) the local database may be corrupt; the recovery is `make clean`
  (drops the local volume) and `make up`.
- Spec YAML is parsed with libyaml (`yaml.CSafeLoader`), not `yaml.safe_load`: same
  documents, ~6x faster, and every xdist worker parses all of `spec/` once.
- Dependencies: add to `requirements.txt` (runtime) or `requirements-dev.txt` (tooling),
  then rebuild. Do not `pip install` inside a running container.
- Before every commit: `make lint && make test`. Small, focused commits.

## Domain model & decisions

- **SID for the database, TMF Open API v5 for the surface** (TMF676 Payment only exists
  as v4, so v4 there). Specs live in `spec/`, pinned by commit; see `spec/README.md`.
- **Orders are the only way to create or terminate products.** `product.created_by_item_id`
  is NOT NULL; termination records `terminated_by_item_id`.
- **Only the billing run writes ABRs** (`applied_customer_billing_rate.billing_run_id` is
  NOT NULL).
- **Half-open ranges everywhere.** `valid_for` is `tstzrange [start, end)`, NULL upper =
  open-ended (TMF TimePeriod without endDateTime). Billing periods are `daterange [start,
  end)`. `product.termination_date` is exclusive. Billability is driven by dates, not
  status: `start_date <= d < termination_date`, excluding only `cancelled`/`aborted`.
- **Enums verbatim from the spec**, camelCase included (`pendingActive`). One exception
  rule: values are compared after `strip()` - upstream `ProductStatusType` has the typo
  `'aborted '`, stored as `aborted`. `tests/test_enum_lock.py` locks TMF-derived enums
  against `spec/`. Where the spec field is a free string we define our own enum ("ours,
  not TMF" in the migration); casing: if the spec gives example values, use their exact
  spelling (`In Design`, `Active`), otherwise lowerCamel.
- **No balance column.** Balance = payments (`done`) - sum of all ABRs; refunds are stored
  negative, so they already count as credits.
- **Money** is `numeric(12,2)` / `Decimal`, never float. One currency per billing account,
  enforced by composite FKs to `billing_account (id, currency)`. Taxes are out of scope.
- **Injected clock.** Nothing in `app/` may call `datetime.now()`, `date.today()`,
  `datetime.utcnow()` or `time.time()` except `app/clock.py` (a test greps for it).
  "Today" for billing is the date in `BILLING_TZ` (default `Europe/Moscow`). Repository
  functions take `now`/`today` as arguments.
- **`bill_cycle_day` 1..28** with calendar-aligned billing periods (no month has fewer
  than 28 days, so every cycle day exists every month).
- **Exclusion constraints** (prices, ABRs, bills) forbid overlapping ranges, not just equal
  ones. `ON CONFLICT` with a conflict target does not work with exclusion constraints;
  only a bare `ON CONFLICT DO NOTHING` does.
- **Out of scope for now:** taxes, `modify` items and cancelProductOrder. A `modify` item
  is still storable - order processing rejects it with a reason instead of the schema
  raising an IntegrityError.
- **Stage order:** 1. schema + repository (done) -> 2. TMF Open API backend (done) -> 3.
  billing core (done, not wired: see "Billing") -> 4. worker with the daily run -> 5.
  frontend. Bill and ABR endpoints are read-only; in production they stay empty until the
  stage 4 worker calls `bill_account`. Stage 3 changes nothing user-visible, so it needs
  no deploy.
- **Rounding:** banker's rounding (`ROUND_HALF_EVEN`) to 2 decimals, applied once per ABR,
  never on intermediate values.
- **Billable days:** the activation day is billed, the termination day is not. This is the
  existing half-open `[start_date, termination_date)`: a product terminated "today" has
  `termination_date = today`, so today is not charged. A product activated and terminated
  on the same day is charged 0 days.
- **`frontend/`** is planned (stage 5): a separate directory built to static files and
  served by the shared Caddy.
- Stage 1 runs the migration as a NOSUPERUSER database owner in a test, like vps-infra's
  `make add-db` does; `btree_gist` works because it is a trusted extension (PG13+).

## TMF API conventions (stage 2)

Built once in `app/api/` and shared by every API. All eight APIs of stage 2 are
implemented; order processing lives in `app/ordering/`.

- **Paths:** `/tmf-api/<apiName>/v5/<resource>`: `productCatalogManagement`,
  `partyManagement`, `customerManagement`, `accountManagement`,
  `productOrderingManagement`, `productInventory`, `customerBillManagement`, and
  `paymentManagement/v4` (TMF676 only exists as v4). `href` is the absolute path, no host.
- **Not exposed:** DELETE anywhere, POST/PATCH on product, cancelProductOrder, `/hub`, the
  billing run. An unsupported method on an exposed path is a 405.
- **Errors:** every non-2xx body is the TMF `Error` (`@type`, `code`, `reason`, `message`,
  `status`), including unknown routes, FastAPI's own validation errors and unhandled
  exceptions (500 says "internal error", nothing more). 400 = malformed JSON, unknown
  attribute, read-only attribute, bad query parameter; 404; 405; 409 = conflicts with
  stored state; 415 = wrong content type; 422 = well-formed but invalid (missing
  mandatory, wrong type or value, reference to something that does not exist). The specs
  do not declare 415 and 422; they are ours.
- **Unknown attributes are rejected (400), never silently dropped.** Always accepted:
  `@type`, `@baseType`, `@schemaLocation`, and `href`, `name`, `@referredType` inside
  references. `id` and `href` are server-managed (400 on POST and PATCH). `lastUpdate` is
  accepted and ignored on POST, because the spec's create schemas make clients send it;
  it is read-only on PATCH.
- **Money** is `{"unit": "<ISO 4217>", "value": <JSON number>}`, Decimal end to end.
  FastAPI's own body parsing goes through float (proven by a test), so handlers parse the
  raw body with `parse_float=Decimal` (`app/api/deps.py`, `tmfjson.loads`). Responses are
  written by `tmfjson.dumps`, which emits Decimals as number tokens and raises on a float.
  An amount with more than 2 decimals is a 422, never rounded (`19.9900` is accepted: it
  equals `19.99`).
- **TimePeriod** `validFor {startDateTime, endDateTime}` <-> `tstzrange [start, end)`;
  no `endDateTime` = open-ended; `startDateTime` is required on create; date-times must
  carry an offset. Output is UTC (`...Z`).
- **Dates stored as `date`** (product start/termination, order requested start, bill and
  ABR periods): an incoming date-time is converted to the calendar date in `BILLING_TZ`
  and the normalized value is returned (`2026-10-01T23:30:00Z` is `2026-10-02` in Moscow);
  outgoing dates are midnight in `BILLING_TZ`.
- **Lists:** `offset` (default 0), `limit` (default 20, max 100; above the max is a 400),
  ordered by creation. Always 200 - none of the vendored specs declares 206 for a list.
  `X-Total-Count` = all matching rows, `X-Result-Count` = rows in this response.
  `fields=` selects top-level attributes (`id`, `href`, `@type` always); an unknown name
  is a 400. Filters are equality only, on the attributes listed per resource; any other
  query parameter is a 400.
- **POST** returns 201 with `Location`. **PATCH** accepts `application/merge-patch+json`
  only (415 otherwise); `null` removes an optional attribute and is a 422 on a mandatory
  one; `@type` may be repeated but not changed.
- **Transactions:** one per request (`app/api/deps.py`, `Tx`), committed when the handler's
  block ends, rolled back if it raises.
- **Clock:** the `get_clock` dependency; tests override it with `FixedClock`.
- **The contract is the vendored TMF specs, not FastAPI's `/openapi.json`.** Response
  bodies are plain dicts written by `tmfjson` (no Pydantic response models), so the
  generated `/docs` has no response schemas - on purpose. Stage 5 generates the frontend
  types from `spec/*.yaml` (and the extension schemas we serve), never from `/openapi.json`.
- **Contract-test limit:** a discriminator that sits next to `allOf` (the TMF subclass
  pattern) is not dispatched on, so a wrong `@type` on a reference (e.g. `IndividualRef`
  where the spec says `PartyRef`) passes schema validation. The API tests assert those
  `@type` values explicitly.
- **Contract tests:** `tests/apiclient.py` validates every response in the suite against
  the schema in `spec/` for its path (`Error` for non-2xx). `tests/contract.py` uses
  openapi-schema-validator's OAS 3.0 dialect with its discriminator handling replaced:
  upstream recurses forever on the TMF v5 schemas (each has a self-mapping discriminator
  next to `allOf`), and plain JSON Schema cannot validate the `...RefOrValue` `oneOf`s.
  Because it overrides the library's keyword handlers, `openapi-schema-validator` is
  pinned to an exact version in `requirements-dev.txt`; re-run the suite when bumping it.

### TMF620 mapping

| Resource | DB column <-> TMF attribute | Filters |
|---|---|---|
| productSpecification | `name`, `description`, `lifecycle_status` <-> `lifecycleStatus`, `updated_at` <-> `lastUpdate` | `name`, `lifecycleStatus` |
| productOffering | `name`, `description`, `lifecycle_status`, `is_sellable` <-> `isSellable`, `valid_for` <-> `validFor`, `product_specification_id` <-> `productSpecification` (ref, mandatory - ours), attached prices <-> `productOfferingPrice[]` (refs), `code` <-> `externalIdentifier[]` (0003; see below) | `name`, `lifecycleStatus`, `isSellable`, `productSpecification.id`, `externalIdentifier.id` |
| productOfferingPrice | `name`, `lifecycle_status`, `price_type` <-> `priceType` (`recurring`/`oneTime`), `recurring_period` <-> `recurringChargePeriodType` (`monthly`), `recurring_period_length` <-> `recurringChargePeriodLength` (1), `amount` + `currency` <-> `price`, `valid_for` <-> `validFor` | `name`, `priceType`, `lifecycleStatus` |

- **An offering's lifecycle only moves forward**: PATCH `lifecycleStatus` allows In Design
  -> Active and Active -> Retired; Retired is final. Any other transition is a 409 with
  the reason, and nothing in that PATCH is applied. Repeating the current status is not
  a transition (200). POST may still create an offering in any status. The admin UI only
  offers the forward step; the rule lives here.
- **Prices attach through the offering**, as in the spec: a price is created on its own
  (`product_offering_id` NULL) and attached by `productOfferingPrice[]` on POST/PATCH of
  the offering; only references are accepted there, not inline prices. A price belongs to
  at most one offering (409) and may not overlap a price of the same type and currency on
  it (409, the exclusion constraint).
- **Prices are never detached or re-priced.** A PATCH that drops a price from
  `productOfferingPrice[]` is a 409; `price`, `priceType` and the recurring period are
  read-only (400); `validFor.startDateTime` cannot move (409). Changing a price = end its
  `validFor` and attach a new one - stage 3 billing needs the historical price.
- **A price never starts in the past.** A price created or attached with
  `validFor.startDateTime` < now is a 409. Stage 3 catch-up billing charges past days at
  the price valid then, so a past-starting price would back-charge them.
- **Business rule: a new price becomes sellable from the next midnight in BILLING_TZ at
  the earliest.** Its start must be >= now, and an order looks up the price at midnight
  (BILLING_TZ) of the product's start date - so a price created today can only price
  products starting tomorrow or later.
- **A price is never ended retroactively.** `validFor.endDateTime` can only be set or
  moved to a value >= now (the injected clock), and only while the current end, if any,
  is not in the past (409 otherwise): a retroactive end, or reopening a price that has
  already ended, would change prices already used for billed periods.
- **Ending a price never leaves a product without one (D-W18).** Setting or moving the
  end E of a recurring price attached to an offering is a 409 unless a price of the same
  offering, type and currency starts exactly at E, or every product of the offering on an
  account in that currency (not cancelled/aborted) is terminated by the first business
  date whose midnight the price no longer covers (E's date if E is midnight, else the
  next day). A gap could never be filled later: prices never start in the past. A
  missing price is therefore a data bug, fixed case by case with a reviewed one-off script
  and an audit note - there is no waiver feature. Ordering keeps the other side closed
  (`priceEnds`, D-W20): together they give the invariant **"a live product has a price
  for every future day"**.
- **Deviation: changing a live offering's price is one internal operation (D-W21).**
  `POST /internal/v1/productOffering/{id}/priceChange {amount, currency, validFrom}` -
  TMF620 cannot express it: there is no cross-request transaction, and with a product
  live neither order of its two steps works (attaching the successor first violates the
  no-overlap constraint; ending first is refused by the rule above). In one transaction
  it ends the open-ended recurring price in `currency` at midnight (BILLING_TZ) of
  `validFrom` and creates the successor, attached, from that moment; all or nothing
  (proven by a failure injected after the end). `validFrom` is a business date >=
  tomorrow (the next midnight); 409 if there is no open-ended price in that currency or
  a price already starts on or after `validFrom`. Existing products pick up the new price
  from their first billing period starting on or after `validFrom` (the core prices each
  period at its first billable day); new orders from `validFrom`.
- **Offering code (migration 0003):** the spec's `externalIdentifier[]` with exactly one
  entry `{id: <code>, externalIdentifierType: "offeringCode", owner: "bss", @type:
  ExternalIdentifier}`. `owner` may be omitted on POST (any other value is a 422). The
  code matches `^[A-Z0-9][A-Z0-9_-]{0,31}$` (upper case, so uniqueness never depends on
  case), is unique (409 "offering code X is already in use"), optional, set on POST only
  and read-only after (400). Offerings without one carry no `externalIdentifier`.
- **Deviation: only references in `productOfferingPrice[]`.** The spec's
  `ProductOfferingPriceRefOrValue` also allows an inline price; we accept references only
  (an entry without `id` is a 422). Create the price first, then reference it.
- `productSpecification` of an offering is read-only on PATCH.
- **Migration 0002** adds what the API needs and 0001 had no place for. Its NOT NULL
  columns have no default on purpose: if the tables are not empty the migration fails
  and the deploy stops before the app starts.

### TMF632, TMF629, TMF666 mapping

| Resource | DB column <-> TMF attribute | Filters |
|---|---|---|
| individual | `party` with `party_type = 'Individual'`: `given_name` <-> `givenName`, `family_name` <-> `familyName` (both mandatory, as in the spec's create schema) | `givenName`, `familyName` |
| organization | `party` with `party_type = 'Organization'`: `organization_name` <-> `name` | `name` |
| customer | `name`, `status` (`Created`/`Active`/`Inactive`, ours), `party_id` <-> `engagedParty` (`PartyRef`, `@referredType` = the party type; fixed at creation) | `name`, `status`, `engagedParty.id` |
| billingAccount | `name`, `state` (`Active`/`Suspended`/`Closed`, ours), `customer_id` <-> `relatedParty[0]` (role `customer`, `PartyRoleRef` to the Customer; exactly one, fixed at creation), `bill_cycle_day` <-> `billCycleDay`, `currency` <-> `currency`, computed <-> `accountBalance[]`, `credit_limit` <-> `creditLimit` (0003; see below) | `name`, `state`, `relatedParty.id` |

An individual is not reachable under `/organization` and vice versa (404).

- **Extension `BssBillingAccount`** (TMF extension pattern). Responses carry
  `@type: BssBillingAccount`, `@baseType: BillingAccount` and `@schemaLocation:
  /tmf-api/schema/BssBillingAccount.schema.json`, a JSON Schema served by the app that
  adds `billCycleDay` (integer 1..28) and `currency` (ISO 4217). Both are required on
  create. Both are fixed after creation: `currency` for good (PATCH is a 400, read-only),
  `billCycleDay` for now (PATCH is a 409 with the reason; see "Open questions"). TMF666 has no currency attribute at all, and
  `billStructure.cycleSpecification.billingDateShift` means an offset of the bill date
  from the period start, not the day the period starts - hence the extension. PATCH
  accepts `@type` `BssBillingAccount` or `BillingAccount`. Every full billingAccount body
  in the tests is validated against both TMF666 and the served extension schema.
- **`creditLimit` (migration 0003)** is TMF666's own Money attribute, always in the
  account's currency (another `unit` is a 422), never negative (422), optional; PATCH
  `null` removes it. **Informational only:** nothing - ordering included - is ever
  refused because of it; the admin UI shows it and warns.
- **Balance** is the spec's `accountBalance[]` with exactly one read-only entry:
  `{"balanceType": "currentBalance", "amount": Money, "@type": "AccountBalance"}`,
  computed on every read as payments (`done`) minus all ABRs. **Positive = the customer
  is in credit** (has paid more than was charged); negative = the customer owes.
  `accountBalance` in a POST or PATCH is a 400.

### TMF676 (v4) mapping

| payment | `billing_account_id` <-> `account` (AccountRef, `@referredType: BillingAccount`), `amount` + `currency` <-> `totalAmount` and `amount` (equal: no taxes), `received_at` <-> `paymentDate` and `statusDate` (the clock at POST), `status` (`done`), `payment_method` <-> `paymentMethod` (`{name, @type}`, both the stored word) | `account.id`, `status` |
|---|---|---|

POST records a received payment: `account`, `totalAmount` and `paymentMethod` (with a
`name` or an `@type`) are mandatory, as in the spec. `amount`, if sent, must equal
`totalAmount`; `taxAmount`, if sent, must be zero. `totalAmount.unit` must be the
account's currency (422); the composite FK behind it maps to 409 if it ever fires.
Payments are never changed or deleted (405).

### TMF637 and TMF678 mapping (read-only)

| Resource | DB column <-> TMF attribute | Filters |
|---|---|---|
| product | `status`, `start_date` <-> `startDate`, `termination_date` <-> `terminationDate` (exclusive: the first day not billed), `created_at` <-> `creationDate`, offering name <-> `name`, `billing_account_id` <-> `billingAccount`, `product_offering_id` <-> `productOffering`, `created_by_item_id` / `terminated_by_item_id` <-> `productOrderItem[]` (`RelatedOrderItem`, role `creation` / `termination`) | `billingAccount.id`, `status` |
| customerBill | `bill_date` <-> `billDate`, `bill_period` <-> `billingPeriod`, `amount_due` + `currency` <-> `amountDue`, `state`, `billing_account_id` <-> `billingAccount` | `billingAccount.id`, `state` |
| appliedCustomerBillingRate | `charge_type` <-> `appliedBillingRateType` (`recurring` -> `appliedBillingCharge`, `refund` -> `appliedBillingCredit`), `amount` + `currency` <-> `taxExcludedAmount` (refunds negative), `period` <-> `periodCoverage`, `customer_bill_id` <-> `bill` and `isBilled`, `product_id` <-> `product`, `billing_account_id` <-> `billingAccount`, `created_at` <-> `date` | `billingAccount.id`, `product.id`, `isBilled` |

- Products are only written by orders; POST/PATCH/DELETE on `/product` are 405.
- Bills and ABRs are only written by the billing run (`app/billing/adapter.py`); in
  production both lists stay empty until the stage 4 worker runs it.
- **Known:** a product ordered with a future start date stays `created` until the stage 4
  worker activates it. Billing is driven by dates, not status, so money is unaffected.
- **Likewise `pendingTerminate`:** a delete of a started product with a termination date
  in the future sets `pendingTerminate`; the stage 4 worker flips it to `terminated` on
  that date. A termination date of today sets `terminated` directly. The product is
  billed up to the day before `termination_date` either way.

### TMF622 mapping and order processing

| productOrder | `state`, `order_date` <-> `creationDate`, `completion_date` <-> `completionDate`, `requested_start_date` <-> `requestedStartDate` (the effective date: today when omitted), `billing_account_id` <-> `billingAccount` (mandatory - ours), items <-> `productOrderItem[]` (`item_id` <-> `id`, `action`, `state`, `product_offering_id` <-> `productOffering`, `product_id` <-> `product`; `quantity` is always 1), `product_order_error_message` <-> `productOrderErrorMessage[]` (`code`, `reason`, `timestamp`, `productOrderItem[]` refs) | `state`, `billingAccount.id` |
|---|---|---|

- **GET list, GET by id, POST only.** PATCH/DELETE are 405, cancelProductOrder is 404.
- **Synchronous, one transaction** (`app/ordering/process.py`). States come from the pure
  function in `app/ordering/states.py`: order `acknowledged -> inProgress -> completed`
  or `acknowledged -> rejected`; items the same, plus `acknowledged -> cancelled`.
- **4xx vs rejected.** Malformed JSON, an unknown attribute or an empty
  `productOrderItem[]` is a 400; a missing mandatory field, a reference to a billing
  account, offering or product that does not exist, duplicate item ids, a product in two
  delete items, `quantity` other than 1, an add item without `productOffering` (or with a
  `product`) and a delete item without `product` (or with a `productOffering`) are 422 -
  nothing is stored. Everything else that is wrong is a business rejection: the order is
  stored as `rejected` with its reasons and the answer is **201**.
- **Rejection codes** (ours, in `productOrderErrorMessage[].code`): order level -
  `accountNotActive`, `requestedStartDateInPast`; item level - `actionNotSupported`
  (modify, noChange), `billingAccountMismatch` (item account differs from the order's),
  `offeringNotSellable` (not `isSellable` or not `Active`), `offeringNotValid` (validFor
  does not cover midnight of the start date), `noRecurringPrice` (none in the account's
  currency at that midnight), `priceEnds` (from the start date, the offering's recurring
  prices in the account currency do not form a gapless chain ending open-ended - the
  whole chain is walked, not just the price at the start; D-W20), `productOfOtherAccount`,
  `productAlreadyEnded`, `openDeleteItem`, `productNotStarted`.
- **All or nothing.** One reason anywhere rejects the order: failing items are
  `rejected`, the rest `cancelled`, nothing is executed. A rejected order is decided
  before its rows are inserted, so its items are stored directly in their final states -
  a rejected delete item must never be stored open, or it would collide with the open
  delete it is rejected for (the `product_order_item_one_open_delete` index would abort
  the transaction and the rejected order could not be stored).
- **Row lock.** The products of delete items are locked (`SELECT ... FOR UPDATE`, in id
  order) before anything about them is read. A concurrent second delete waits, then sees
  the product terminated and is rejected with `productAlreadyEnded` (proven with two
  connections in `tests/test_order_concurrency.py`; without the lock it waits on the
  product UPDATE instead and terminates the product a second time).
- **Dates.** `requestedStartDate` is converted to the date in BILLING_TZ and returned
  normalized (`2026-10-01T23:30:00Z` -> product `startDate` 2026-10-02). It defaults to
  today and may not be in the past. **Our interpretation:** it is the start date of
  `add` items and the termination date of `delete` items (TMF has no per-item date).
- **add** creates the product with `created_by_item_id`; status `active` if it starts
  today, `created` if later (see the stage 4 note above). The item then references it.
- **delete** of a started product sets `termination_date` (exclusive),
  `terminated_by_item_id` and status `terminated` (termination today) or
  `pendingTerminate` (termination in the future; see the stage 4 note above). **delete of a product that has not started**
  cancels it: status `cancelled`, `termination_date = start_date` (0 billable days); a
  requested date after its start date is rejected (`productNotStarted`).

## Billing (stage 3)

`app/billing/core.py` is pure: no database, no clock, no I/O. `compute(state, as_of, tz)`
takes one account's `BillingState` (frozen dataclasses from `model.py`) and returns the
ABRs and bills to write. `app/billing/adapter.py` loads the state through the repository
(`app/repository/billing.py` maps rows <-> dataclasses; the core never sees a dict) and
writes the result: `bill_account(conn, account_id, run)`, one transaction per account
(a savepoint inside the caller's). The stage 4 worker calls it once per account per run.

**Periods.** Calendar-aligned to `bill_cycle_day` D: `[D of month M, D of month M+1)`,
28-31 days. The cycle day is assumed fixed (see Open questions).

**When a period is charged: in advance, once its first billable day is <= as_of.** The
first billable day is `max(period start, start_date)`. Charging waits for the product's
start, not just for the period's: the price is looked up at that day's midnight, which
must be in the past when the charge is written (a future midnight's price can still
change). Consequence: **a product cancelled before its start is never charged**.

**Price.** The offering's monthly recurring price in the account's currency, valid at
midnight (BILLING_TZ) of the period's first billable day. A price change mid-period
applies from the next period. None found -> `MissingPrice` naming product, day and
currency (never skipped); more than one -> `InconsistentState`. Prices need no visibility
filter (unlike orders, below): a price change only takes effect at or after the moment it
was made (prices never start in the past and are never ended retroactively), and the core
only looks prices up at midnights <= as_of, so a replayed earlier date can never see a
later change.

**Expected-vs-actual diff, per product and period:**

- `expected = round_half_even(price x billable_days / period_days, 0.01)`, rounded once.
  `billable_days` counts `start_date <= d < termination_date` inside the period; zero for
  `cancelled`/`aborted`. Every other status is billed by dates alone (`created`,
  `suspended`, `pendingTerminate`, ...).
- Nothing stored -> a `recurring` charge for the billable range, even if it rounds to 0.00.
- A charge stored -> first re-check it still equals `round(price x its days /
  period_days)` (else `InconsistentState`), then `diff = expected - (charge + refunds)`:
  0 -> nothing; < 0 -> a refund of `diff` (negative) for the unbilled tail
  `[termination_date, charge end)`, or the whole charge when nothing is billable any more,
  `refunds_rate_id` -> the charge; > 0 -> `InconsistentState` (impossible by the rules).
- The refund is never priced on its own days: `charge + refund` must equal one correct
  rounding of the used days, or cent drift accumulates (`round(p x 29/30) - round(p x
  22/30)` != `round(p x 7/30)` for p = 0.02; locked by
  `test_refund_keeps_the_period_net_exact`).
- **One refund per charge** (`refunds_rate_id UNIQUE`, and refunds may not overlap).
  Enough because termination dates are immutable: a product is deleted once (a second
  delete is rejected `productAlreadyEnded`), so the expected net of a period drops at most
  once. A second reduction would raise `InconsistentState`.
- If the termination is already known when the period is charged, there is one reduced
  charge and no refund. Daily runs and one catch-up run can therefore store different rows
  (charge + refund vs one reduced charge) with the same net per period.

**Bills.** A bill on cycle day D covers `[D, next D)` and collects every unbilled ABR of
the account whose **run date** (`billing_run.run_date` of the run that wrote it) is <= D.
A run issues the bills for every cycle day after the account's last bill up to as_of;
one that would collect nothing is skipped (a 0.00 bill of 0.00 charges is still issued).
`amount_due` is the sum and may be negative. ABRs written in the run of D join the bill
of D; a product starting mid-cycle, or a late ABR, goes onto the next bill. Run date, not
`created_at`: `created_at` is the database's `now()`, outside the injected clock, and a run
for D that crosses midnight or is retried the next day would date its ABRs D+1.

**What a run sees: order-driven facts completed before 00:00 of its run date** in
BILLING_TZ (end-of-day processing of D-1). The adapter filters products by the
`completion_date` of the order that created them, and terminations/cancellations by the
order that set them (an invisible termination is passed with the status the product had
before it). An order placed on D is first seen by the run of D+1, whatever time the run
of D executes. This makes a replayed date see exactly what the daily run saw.

**Concurrency and guards.** `bill_account` locks the billing account row (`FOR UPDATE`)
before reading: a concurrent second call waits, then sees the first one's ABRs and writes
nothing (without the lock it collides on `abr_no_overlap`; proven with two connections
in `tests/test_billing_db.py`). It only writes into a run that is `running` (re-read
`FOR SHARE`). Any `BillingError` rolls the whole account back.

**Tests.** `tests/test_billing_core.py`: the hand-checked examples (half-even 1.62 vs
1.63, 300 x 17/31, 27/28 vs 27/29 days, refund -116.13, ...). `tests/test_billing_properties.py`
(hypothesis, pinned exactly): idempotence, net per period = one rounding, lifetime without
drift, refund shape, no overlap, precision/currency, bills collect every ABR exactly once,
replay identical, daily vs one catch-up run equal in non-zero net per period. The oracle
counts billable days one by one, independently of `app/billing`. `tests/test_billing_db.py`:
the same through the adapter and the real schema, plus the TMF678 view, visibility on
the day boundary and replay across an outage. Every rule above was broken once to see a
test fail (half-up rounding, refund priced separately, overlapping charges, visibility `<=`,
termination always visible, no account lock, no running-run guard).

**Stage 4 must:**

- run the date D only at or after 00:00 of D (BILLING_TZ). A run for D started earlier
  would miss orders completed between its start and midnight, which a replay would see;
- after an outage, replay every missed date in order, each with its own `billing_run`, so
  run dates and bills match daily runs. A single multi-month `as_of` call is for tests
  only (it puts every catch-up ABR on the bill after as_of);
- call `bill_account` per account and record a failing account without stopping the run.

## Internal API (stage 5)

`app/internal/`, mounted at `/internal/v1`, serves what the admin UI cannot get from the
TMF APIs without re-implementing billing rules. Ours, not TMF; same conventions as
`app/api` (TMF `Error` bodies, Decimal-exact Money `{unit, value}`, one transaction per
request, the injected clock, offset/limit paging with `X-Total-Count`/`X-Result-Count`,
unknown query parameters are a 400), except that **dates are plain business dates**
(`YYYY-MM-DD` in BILLING_TZ). The contract is `spec/internal/bss-internal.oas.yaml`:
strict schemas (`additionalProperties: false`), validated for every response in the
suite by `tests/apiclient.py`, and the source of the UI's TypeScript types (never
FastAPI's `/openapi.json`). OAS 3.0 cannot make a `$ref` nullable (`nullable` next to
`allOf` does not admit null), so nullable variants are spelled out (`NullableMoney`, ...).

| Endpoint | What |
|---|---|
| `GET /context` | businessDate (injected clock), now, timeZone, utcOffset (at now), environment (`APP_ENV`), operator, currency (`UI_CURRENCY`, default RUB), billingRunAt (`BILLING_RUN_AT`, default 00:30) |
| `GET /billingAccount/{id}/firstCharge?productOffering.id=&startDate=` | dry run of an add order: the stage 3 core's `compute()` on the hypothetical product. period, cyclePeriod, billableDays of periodDays, price, amount, chargeRunDate = max(startDate, businessDate + 1), billDate = first cycle day >= chargeRunDate, next full period, balance, balanceAfter (= balance - this charge only), creditLimit, exceedsCreditLimit. startDate in the past or no price in the account currency -> 422 |
| `GET /billingAccount/{id}/products` | not-ended products (created, pendingActive, active, pendingTerminate, suspended) with offering name/code, specification name, price valid today (or on the start date), nextChargeDate (start date if not started, else the next cycle day after today; null if the product ends by then) |
| `GET /billingAccount/{id}/charges` | ABRs newest run first, with runDate, billableDays of periodDays (the proration), offering, bill |
| `POST /productOffering/{id}/priceChange` | the atomic price change of a live offering (see the TMF620 deviation above): ends the open price at midnight of `validFrom`, creates and attaches the successor; 201 `{productOffering, endedPrice {endsOn}, newPrice {price, validFrom}}` |
| `GET /billingAccount/{id}/transactions` | charges, refunds and payments as one ledger with balanceAfter. Positive = credit (as accountBalance); ordered by day (ABR: run date; payment: its date in BILLING_TZ), ABRs before payments on the same day (the run of D happens at its start), then timestamp, id. The newest balanceAfter equals the account balance (tested) |

- **operator** comes from the `X-Remote-User` header, which the shared Caddy must *set*
  (overwrite) from the basic-auth user, so a client-sent header never passes through.
  Anything not matching `^[A-Za-z0-9._@-]{1,64}$` is reported as null.
- **Trust in `X-Remote-User`.** The app believes the header only because, from outside the
  VPS, it is reachable solely through Caddy. It is not proof of identity: any other
  container on the `shared` network can reach `bss:8080` directly and forge it, and so can
  anyone with a shell or an SSH tunnel on the VPS (the app is also published on the host's
  `127.0.0.1:18081`, which `make tunnel` uses). Acceptable for this demo because the value
  is display-only - nothing is authorised by it. Anything that needs a real operator
  identity must not rely on this header.
- Every rule above was broken once to see a test fail (extra attribute vs the strict
  contract, charge run date without the end-of-day rule, ledger sign, creditLimit
  currency, duplicate code).
- Stage 4 adds the billing run endpoints (shape agreed in stage 5 checkpoint 1):
  `GET /billingRun?limit=14`, `GET /billingRun/{id}`, `POST /billingRun/catchUp {upTo}`
  (queues every missing date in order; 202), `POST /billingRun/{id}/retry` (latest run,
  failed only; 409 otherwise).

## Frontend (stage 5) decisions

- **A subscriber row is a billing account** (it has the balance, cycle day and products).
  Name from the customer; the status pill and filter are the account's real `state`
  (Active / Suspended / Closed), which is also what ordering checks (`accountNotActive`).
  **A customer with two accounts shows as two rows.**
- The subscribers list makes no per-row requests: one billingAccount list (balance
  included) and one product list (`status=active`), grouped client-side, page by page.
- "Registered" is replaced by "Cycle day" (no creation date in TMF629/666, and
  `created_at` is the database's `now()`, not the injected clock).
- Credit limit: shown, and the over-limit warning in Add product, informational only.
- Offering code: `externalIdentifier` (above). Subscriber no. and `BA-xxxxxxxx` are
  display ids: the first 8 hex digits of the UUID, upper case.
- Weekly recurrence is not offered (monthly only). One currency in the UI (`UI_CURRENCY`);
  the backend stays multi-currency.
- Pills for statuses the design lacks (`created`, `pendingTerminate`, `suspended`,
  `cancelled`, Suspended/Closed accounts) reuse existing token colours only.
- Retiring an offering does not end its prices; its products keep billing.
- Billing runs screen: hidden until stage 4. The run time is `BILLING_RUN_AT` (00:30).
- Charge timing copy follows the end-of-day rule: an order completed today is charged by
  the run of max(start, tomorrow) and billed on the next cycle day from there - nothing is
  "charged on confirmation".
- Smoke on the VPS: a separate basic-auth user `smoke` (own hash), GET-only in Caddy
  (any other method 403); its plaintext lives only in `.env.prod`, never printed.
- Pre-commit runs `make web-lint` and `make web-test` only when staged files touch
  `frontend/` or `spec/`. **A change in `app/api` or `app/internal` can still break the
  UI** without touching either; `make verify` (which runs `web-e2e`) is the gate that
  catches it.

## vps-infra change request (stage 5, checkpoint 7)

To be applied in the vps-infra project, not here. The `bss` site block on :8445 changes
from "everything to the app" to:

1. **`/tmf-api/*` and `/internal/*` -> `reverse_proxy bss:8080`**, both behind the same
   basic auth. `/internal/*` must be routed explicitly: otherwise it falls through to the
   file server's `try_files ... /index.html` and silently answers HTML with 200.
2. On both of those routes, **`header_up X-Remote-User {http.auth.user.id}`** - a SET
   that overwrites whatever the client sent, never an append or a pass-through.
3. `/healthz` -> `bss:8080` without auth, as today.
4. Everything else: **`root * /srv/bss-web`, `try_files {path} /index.html`,
   `file_server`**; `/assets/*` with `Cache-Control: public, max-age=31536000, immutable`,
   `index.html` with `Cache-Control: no-cache`.
5. **Static mount:** the Caddy container mounts `~/bss/web` read-only at `/srv/bss-web`.
   `deploy.sh` creates the directory before the first mount (otherwise Docker creates it
   root-owned) and only ever rsyncs *into* it - assets first, `index.html` next, then a
   `--delete` pass. The directory itself is never replaced or renamed: the bind mount
   holds its inode, and Caddy would keep serving the old one.
6. **Smoke user:** a second basic-auth user `smoke` with its own bcrypt hash; for that
   user any method other than GET/HEAD is answered 403 by Caddy before reaching the app.
   The plaintext lives only in `.env.prod` here, never printed.

The web smoke test (deploy.sh, checkpoint 7) proves it through Caddy:
`GET /` -> 401 without auth; as `smoke`: `GET /` -> 200 HTML carrying this build's
`bss-build` marker, **`GET /internal/v1/context` -> 200 `application/json`** with a JSON
body (not the index.html fallback) whose `operator` is `smoke` (proves the header is set),
`GET /tmf-api/.../billingAccount?limit=1` -> 200 JSON, and `POST` to a TMF path as `smoke`
-> 403.

## Open questions

- **Changing `billCycleDay`.** PATCH is a 409 today. Not settled in stage 3: a change has
  to decide how the current period is treated (close it early, stretch it, or only apply
  from the next period). The billing core assumes a fixed cycle day; with a changed one,
  stored ABRs no longer sit inside one cycle period and `compute` raises
  `InconsistentState`.
- **Suspension semantics.** Billing is date-driven: `Suspended`/`Closed` billing accounts
  and `suspended` products are billed like any other. Whether a suspension should stop or
  reduce charges (and how a resume re-enters billing) is open; ordering already refuses
  new orders on a non-Active account.

- **Stale billing runs (stage 4).** A run killed mid-way (e.g. by the nightly 03:00
  reboot) stays `running`, and `UNIQUE (run_date)` blocks a new one. `start_run` must be
  able to take over a stale run. Not implemented yet; today it raises
  `BillingRunConflict`.
- **`start_run` semantics (stage 4).** A completed run for the date should be a no-op
  (`AlreadyCompleted`), not an error; only a concurrently running run is a conflict.
  Today both raise `BillingRunConflict`. The stale-running takeover above is still open.
- **Port the format fix to vps-lab-template** (fixed here, not there). In the template,
  `make fmt` and `.claude/hooks/format.sh` both fail silently:
  - `compose.override.yaml` mounts `app/` and `tests/` read-only on `app`, so
    `docker compose run app ruff format` cannot write ("Read-only file system");
  - the hook passes Claude Code's absolute host path into the container, where it does not
    exist, sends format output to /dev/null, prints `ruff check` to stdout (not shown for
    PostToolUse) and always exits 0.

  Fix as done here: a `tools` service in `compose.override.yaml` (same `dev` target and
  image, `profiles: [tools]`, only `./app`, `./tests`, `./alembic` mounted read-write and
  `./pyproject.toml` read-only under `/app` - not the whole project, so the container
  never sees `.env`, `.env.prod`, `.git` or `.claude` - venv stays in `/opt/venv`, no
  ports, no DB, `network_mode: none`); `make fmt` runs
  `docker compose run --rm tools sh -c 'ruff format . && ruff check --fix .'`, `make lint`
  stays on `app`. The hook: no host-ruff branch (the container pins the ruff version);
  acts only on `*.py` under `$CLAUDE_PROJECT_DIR`, passes the project-relative path; a
  `.py` outside the mounted directories is reported as FAILED, not skipped silently; one
  container start running `ruff format --force-exclude` then `ruff check --fix
  --force-exclude --output-format concise`; clean -> exit 0 silently, findings -> stderr +
  exit 2, any failure to run -> "format hook FAILED to run: <reason>" + exit 2; explicit
  `timeout` in `.claude/settings.json`.
- **Port the SSH hardening to vps-lab-template** (fixed here, not there). The template's
  `deploy.sh` opens a new SSH connection per step (~15 per deploy) with no timeouts. The
  VPS intermittently stalls *new* connections at the banner exchange
  (`kex_exchange_identification: read: Connection timed out`); an rsync hung for minutes
  and a `docker save | ssh` ship hung 17 min before failing. Fix as done here:
  - refuse a dirty tree first, before any network call: `git status --porcelain` must be
    empty - the tag is the commit hash, so a dirty build (image or compose files) would
    ship different content under a clean-looking tag;
  - one master per deploy: `SSH_OPTS=(-o ControlMaster=auto -o ControlPath=~/.ssh/cm-%C
    -o ControlPersist=60 -o ConnectTimeout=15 -o ServerAliveInterval=15
    -o ServerAliveCountMax=4 -o BatchMode=yes)`, opened explicitly with `ssh -MNf` before
    the build (or reused if `ssh -O check` already finds one), verified with `ssh -O check`;
  - the master is backgrounded, so it logs to a file: `-o ControlMaster=yes -o
    LogLevel=VERBOSE -E "$(mktemp)"`. One `trap finish EXIT` closes the master with
    `ssh -O exit` (only if this deploy opened it), prints the last 20 lines of that log
    when the exit code is non-zero, and removes the log;
  - every remote call rides the master: an `rssh` wrapper, `rsync -az --timeout=60 -e "ssh
    <same opts>"`, and `timeout N ssh "${SSH_OPTS[@]}"` where `timeout` needs a binary;
  - exactly one retry, 5 s apart, and only when opening the master exits 255; later steps
    are never retried;
  - skip the ship when the server already has the image. Not by `.Id`: both sides use the
    containerd image store, where `.Id` is the manifest digest and differs between two
    builds of identical content. Compare a fingerprint instead, computed by the same `sh`
    snippet locally and remotely: sha256 of `docker image inspect --format '{{json
    .RootFS.Layers}}{{json .Config}}'` (Layers alone miss a CMD/ENV/HEALTHCHECK-only
    change). A missing image yields an empty fingerprint and never matches;
  - Makefile `ssh`/`tunnel`/`remote` get the same timeouts and `BatchMode=yes` (no
    multiplexing there).

  Proven here: 1 TCP connection per deploy (sampled with `ss`), a dead endpoint (silent
  listener or blackhole IP) fails in ~35 s (15 + 5 + 15) with a clear message.
  Skip proven across hosts: two consecutive plain `make verify` runs of the same commit
  (`921603f`) against the VPS - the first shipped the image, the second printed "image
  already on server, skipping ship (fingerprint 8258832b6d9c)", so the Layers+Config
  fingerprint survives `docker save | docker load`. The master-open retry also fired once
  in real use, in the second run (exit 255, then success on the second attempt).
  Connection counts were not sampled in these two runs. A run where the retry fires shows
  2 connection attempts by design: the failed one plus the successful one.

## Open checks

Nothing below has been run; remove an item only when it has passed.

- **Restore drill.** On 2026-09-30 `~/infra/backups` on the VPS was empty and no backup
  cron was installed; this is being fixed in vps-infra. Use the first cron-made `bss` dump:
  `pg_restore --list` on the server (no databases created and no `pg_restore` into a
  database there), copy it here, restore into a local `postgres:16-alpine` as a
  NOSUPERUSER role `bss` with `pg_restore --no-owner --role=bss`, compare tables, enum
  types and `alembic_version` with prod (14 tables incl. `alembic_version`, 14 enums,
  `0001`), report every warning, clean up locally.
- **`"ask"` rules.** Verify in a fresh session, in the normal mode, that an Edit to
  `Makefile` prompts; if it doesn't, switch these paths from `"ask"` to `"deny"`.

## Working style

- For anything that changes the Dockerfile, compose files, deploy script or the data
  model: propose a plan first, then implement.
- After changing runtime behaviour, run `make test`; before saying "it works on the
  server", run `make verify`.
- When a container misbehaves, read `make logs` before changing code.
- **Files are edited only with the Edit/Write tools** - never through shell heredocs,
  `sed -i`, `python -c`/scripts that rewrite files, or redirects (`>`, `>>`, `tee`).
  Temporary negative-proof mutations included: make them with Edit and revert with Edit.
- **If the guard (or any permission rule) blocks a shell command, stop and report it.**
  Do not reach the same result another way (another tool, a script file, a different
  path spelling) - that is exactly what the block exists to prevent, even when the block
  looks like a false positive. Say what was blocked and why it seemed needed; the human
  decides.

## Shared infrastructure (vps-infra)

On the VPS, Postgres and Caddy are **not ours**. The separate vps-infra project is
deployed once per host and runs one Postgres and one Caddy for every app there, owning
their config, data, TLS and backups. This repo only ships the app container.

- **Network**: the app joins the external network `shared`, created by vps-infra. It must
  exist before `make deploy`; `scripts/deploy.sh` checks for it and fails early with
  "deploy vps-infra first" otherwise. vps-infra is never needed locally — do not start it
  for development.
- **Alias**: on `shared` the app is reachable as `bss` (network alias in
  `compose.prod.yaml`). Every app there names its service `app`, so that name is
  ambiguous; always use `bss`.
- **Database**: `DATABASE_URL` on the VPS is `postgres:5432/bss`, role
  `bss`; `postgres` is the service name in vps-infra, and the database + role are
  created there with `make add-db NAME=bss`. The password is `POSTGRES_PASSWORD`
  from `.env.prod`, which must equal the role password `make add-db` used (`DB_PASSWORD_<NAME>` in
  vps-infra's `.env.prod`) — the value `make init`
  generated is temporary, and a mismatch makes migrations hang until the 120s timeout
  instead of failing clearly. Migrations still run from this repo (`deploy.sh`, before the app
  starts).
- **Reverse proxy**: this app's site block (TLS + basic auth on `8445`,
  `https://vpn2811.duckdns.org:8445`, `/healthz` carved out without auth for external
  monitoring) is added **by hand** to vps-infra's Caddyfile and proxies to
  `bss:8080`. Routing, auth and TLS changes are made there, not here. `/healthz`
  must still stay cheap, check the DB and return nothing beyond status — the external
  monitoring depends on it.
- **Backups**: vps-infra dumps all databases, this one included. This project has no
  backup or restore tooling. Make sure a fresh vps-infra dump exists before any migration
  that alters or removes existing data.
- The VPS reboots nightly at 03:00 (root cron). Everything must come back on its own —
  never rely on manually started containers.

## Known compose pitfalls (found on live deploys)

Both of these only showed up against the real VPS, never locally:

- **`docker compose run --name ...` hangs** instead of starting the container. Don't use it
  to get a predictable name for the one-off migration container; `deploy.sh` finds a
  leftover one by compose's labels (`com.docker.compose.project=bss` +
  `com.docker.compose.oneoff=True`) instead.
- **`--quiet-pull` hangs when the image was only loaded locally.** The image reaches the
  VPS via `docker save | docker load`, with no registry behind it; with `--quiet-pull`
  compose tries to check that nonexistent repository and never returns. Keep it off every
  remote compose call.

## Frontend conventions

The admin UI (stage 5) lives in `frontend/`: Vite + React + TypeScript, built to static
files and served by the shared Caddy on :8445 (same origin as the API, same basic auth -
no CORS). It never enters the backend image (`.dockerignore`). `design-ref/` is the design
handoff: a pristine reference, never edited (settings.json deny + guard.sh). Types come
from `spec/` (TMF and `spec/internal/`), never from `/openapi.json`. Fonts are
self-hosted (`@fontsource`), no CDN at runtime. Every dependency needs a stated reason:
`package.json` carries them under `"//"`.

- **Node runs only in a container, never on the host.** Two compose services (local
  only, `profiles: [web]`), same image (`node:22-alpine` pinned by digest), same mounts
  (`./frontend` at /web, `./spec` read-only, `node_modules` and the npm cache as named
  volumes - the host's `frontend/node_modules` is only the empty mount point):
  - `web` has network: `make web-dev` (Vite on 0.0.0.0 inside, published on
    127.0.0.1:5173, proxying `/tmf-api` and `/internal` to `app`) and `make web-install`;
  - `web-offline` has `network_mode: none`: build, lint, test, types, fmt.
- **Installs are explicit.** Only `make web-install` downloads (`npm ci`, exactly the
  lockfile) and stores the lockfile's hash in the volume. Every other target first runs
  `frontend/scripts/check-deps.sh`, which only compares that hash and fails with "run make
  web-install" - never an implicit install, in particular not from the pre-commit hook.
  Proven: `make web-build` succeeds offline (and the container cannot reach the
  registry); a changed lockfile makes `make web-test` fail with the hint.
- **npm hardening** (`frontend/.npmrc`): `ignore-scripts=true` (no dependency runs code
  at install time; nothing in the tree needs it), `save-exact=true`, no audit/fund calls.
  TypeScript is 5.x on purpose: `openapi-typescript` requires `^5` and `typescript-eslint`
  `<6.1`, and npm enforces peer ranges.
- **Generated types** (`frontend/scripts/gen-types.mjs`, openapi-typescript) for TMF620,
  622, 637, 666 and the internal API go to `src/api/generated/`, which is git-ignored
  (~1 MB) and regenerated by every build, lint and test run - it cannot go stale.
- **Money in the UI:** amounts become integer cents at the edge (`src/format/money.ts`);
  the UI never does arithmetic on binary floats, and typed amounts are sent as the
  validated decimal string. Negatives use U+2212. Dates are business dates in BILLING_TZ
  (from `/internal/v1/context`) via `Intl`, never the browser's zone.
- **Dates the UI sends** (requestedStartDate at noon, validFor at midnight) carry
  BILLING_TZ's offset **for that date** (`zonedDateTime`: `Intl.DateTimeFormat` with
  `timeZone`, so a DST zone gets the right offset either side of a change) - never the
  browser's offset, and not `/internal/v1/context`'s `utcOffset`, which is the offset at
  *now* (top bar only). `npm test` runs the suite twice, in UTC and with
  `TZ=America/New_York`, so every request body is proven independent of the browser's
  zone; a local-`Date`-constructor bug passes the UTC run and fails the second one.
- **Harness:** `settings.json` denies `npm`, `npx`, `node`, `pnpm`, `yarn`, `corepack` on
  the host and edits under `design-ref/`; `guard.sh` blocks a command segment that starts
  with a Node tool (also via `sh -c`) unless that segment runs in the `web`/`web-offline`
  service, and shell writes into `design-ref/`. It splits at `&&`, `;` and `|` without
  understanding quotes or heredocs, so a chained `sh -c` inside `docker compose run`, or
  documentation text containing such a chain, is blocked too - use the make targets. The
  format hook runs prettier (in `web-offline`) on edited UI files.
- **Structure** (`frontend/src/`): `api/` (fetch client: every failure is an `ApiError`
  whose message is the call, "GET /path → 503 Service Unavailable"; `getPage` requires
  `X-Total-Count`; `getAll` pages by 100; `useResource` = loading/error/ok + retry, never
  shows a stale or other key's result), `app/` (router, shell, system context and
  breadcrumbs), `ui/` (tokens, kit, `Table` on CSS grid, the four list states),
  `screens/`, `format/`. **Every list renders through `ListState`**: skeleton (8 rows
  sized by the table's columns), error (call + Retry), empty (copy + optional action),
  no results (Reset filters). The context (`/internal/v1/context`) loads before any
  screen: no date can be shown without the business date and zone.
- **Demo data: `make seed`** (`tests/seed.py`, also the e2e fixture). Everything goes
  through the public TMF API - never SQL - run in-process with a FixedClock that starts
  120 days before today and moves forward a day at a time: each day the billing run for
  that date (`bill_account` for every account, then `finish_run`) and then the day's
  orders and payments. The API refuses past-dated prices and orders, which is why the
  real clock cannot build history. Dates are relative to today, so future-dated and
  pendingTerminate products are really in the future. 4 offerings with codes (one In
  Design), 7 accounts (one Suspended, one Closed, several negative balances, credit
  limits), refunds, payments, bills. Refuses `APP_ENV=prod` and any database but the
  local `db`. Idempotent: if its catalog exists it does nothing; a partial seed is
  reported (reset with `make clean && make up`), never patched. Needs migrations at head
  (`make migrate`). `tests/test_seed.py` runs it on a throwaway database.
- **Catalog:** lifecycle steps (In Design -> Active -> Retired, Retired final) are made
  from an offering detail dialog (row click), with an inline confirm; the table has no
  room for an action column at 1280px. New offering = TMF620 price, then the In Design
  offering referencing it; the amount goes out as an exact JSON number token
  (`jsonWithAmount`), "Valid to" is inclusive in the UI and sent as the next midnight.
  **Change price** (offering dialog, only when there is an open-ended price in the UI
  currency) calls the atomic `priceChange` (D-W21); its copy states that existing products
  pick up the new price from their next billing period starting on or after the date,
  and new orders from the date.
- **The UI's write requests are pinned on both sides**: component tests assert the exact
  bodies, and `tests/test_ui_requests.py` replays the same bodies against the real API.
  Change both together.
- **Routes:** `/subscribers`, `/subscribers/{accountId}`, `/catalog`; `/` redirects to
  `/subscribers`; anything else is a not-found page. No Billing runs route or nav item
  until stage 4 (D6).
