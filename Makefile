## The command interface of the project: every regular operation of the agents (Claude,
## Codex, Kimi) and of the human is a target here, so each action has a stable name and an
## honest exit code. Workflow: docs/worktrees.md. No target builds a file of its own name:
## every target is .PHONY (declared here or next to the target).

SHELL := /bin/bash
# Names (never values) of the variables given on the make command line; scripts/task.py
# refuses names a target does not know.
export BSS_MAKE_VARS := $(strip $(foreach v,$(.VARIABLES),$(if $(findstring command line,$(origin $(v))),$(v))))
# The directory of this Makefile, for make -f /abs/worktree/Makefile finish-task. MAKEFILE_LIST
# splits a path with spaces into words: rejoin it before taking the directory.
space := $(subst ,, )
HARNESS_DIR := $(subst |, ,$(dir $(subst $(space),|,$(strip $(MAKEFILE_LIST)))))
COMPOSE_PROD := docker compose -f compose.yaml -f compose.prod.yaml
# Same timeouts as scripts/deploy.sh: a stalled connection fails fast instead of hanging.
SSH_OPTS := -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o BatchMode=yes
REMOTE ?= vps-test
REMOTE_DIR ?= bss
IMAGE_NAME ?= bss-app
TAG ?= $(shell git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)
# Hypothesis examples per property test (tests/conftest.py): dev = 50, ci = 200 (verify).
HYPOTHESIS_PROFILE ?= dev

.PHONY: help help-ops up down restart logs ps psql shell deployed progress wait-local test test-smoke test-changed lint fmt smoke deploy rollback check verify tunnel ssh clean migrate worker-once remote hooks spec-verify-upstream seed web-install deploy-web-install web-dev web-build web-lint web-test web-types web-fmt web-e2e web-shots docs docs-check mcp-smoke mcp-token-create mcp-tokens mcp-token-revoke

help: ## [task] Show the common targets (GROUP=<name> for one group, GROUP=all for every group)
	@python3 -B scripts/make_help.py "$(firstword $(MAKEFILE_LIST))" "$(GROUP)"

help-ops: ## [task] Show the human-only operations (deploy, VPS)
	@python3 -B scripts/make_help.py "$(firstword $(MAKEFILE_LIST))" ops

ifneq ($(origin SLOT), undefined)
export BSS_SLOT := $(SLOT)
endif

.PHONY: start-task new-priority-task release-slot
start-task: export BSS_START_ISSUE := $(ISSUE)
start-task: export BSS_START_APPROVAL := $(HUMAN_APPROVAL)
start-task: ## [task] Prepare the next Ready issue; optional ISSUE=<id> HUMAN_APPROVAL=<same-id> only with explicit human approval (JSON=1 for agents)
	@python3 -B scripts/task.py $@

new-priority-task: export BSS_NEW_TASK_TITLE := $(TITLE)
new-priority-task: export BSS_NEW_TASK_BODY := $(BODY)
new-priority-task: export BSS_NEW_TASK_BODY_FILE := $(BODY_FILE)
new-priority-task: export BSS_NEW_TASK_ISSUE := $(ISSUE)
new-priority-task: export BSS_NEW_TASK_LABELS := $(LABELS)
new-priority-task: ## [task] Create an approved P0 issue first in Ready; start separately (TITLE= BODY=|BODY_FILE=/abs/path [LABELS=a,b]); ISSUE=<n> finishes a preserved one
	@python3 -B scripts/task.py $@

release-slot: ## [task] Release a slot after its task worktree was safely removed (SLOT=worker1)
	@python3 -B scripts/task.py $@

.PHONY: board add-task move-task set-priority reorder-task
board add-task move-task set-priority reorder-task: export BSS_BOARD_JSON := $(JSON)
add-task move-task set-priority reorder-task: export BSS_BOARD_ISSUE := $(ISSUE)
add-task: export BSS_BOARD_TITLE := $(TITLE)
add-task: export BSS_BOARD_BODY := $(BODY)
add-task: export BSS_BOARD_BODY_FILE := $(BODY_FILE)
add-task: export BSS_BOARD_LABELS := $(LABELS)
add-task set-priority: export BSS_BOARD_PRIORITY := $(PRIORITY)
add-task move-task: export BSS_BOARD_STATUS := $(STATUS)
move-task: export BSS_BOARD_WAITING_ON := $(WAITING_ON)
move-task: export BSS_BOARD_UNBLOCKS_WHEN := $(UNBLOCKS_WHEN)
set-priority reorder-task: export BSS_BOARD_APPROVAL := $(HUMAN_APPROVAL)
reorder-task: export BSS_BOARD_AFTER := $(AFTER)

board: ## [task] Show the open board items by status, in board order (JSON=1 for JSON)
	@python3 -B scripts/task.py $@
add-task: ## [task] Create an issue on the board (TITLE= BODY=|BODY_FILE=/abs/path [LABELS=a,b] [PRIORITY=P1|P2|P3] [STATUS=Inbox|Ready])
	@python3 -B scripts/task.py $@
move-task: ## [task] Move an issue to Inbox, Ready or Waiting (ISSUE= STATUS=; Waiting needs WAITING_ON= UNBLOCKS_WHEN=)
	@python3 -B scripts/task.py $@
set-priority: ## [task] Change an issue's priority label with human approval (ISSUE= PRIORITY=P0..P3 HUMAN_APPROVAL=<same issue>)
	@python3 -B scripts/task.py $@
reorder-task: ## [task] Move an issue first on the board, or after AFTER=<issue>, with human approval (ISSUE= HUMAN_APPROVAL=<same issue>)
	@python3 -B scripts/task.py $@

up: ## [stack] Start the local stack (app + postgres, hot reload)
	./scripts/task-compose up -d --build
	@$(MAKE) --no-print-directory wait-local

down: ## [stack] Stop the local stack (volumes are kept)
	./scripts/task-compose --profile '*' down

restart: down up ## [stack] Restart the local stack

logs: ## [stack] Last 5 minutes of app logs
	./scripts/task-compose logs --since=5m --tail=200 app

ps: ## [stack] Container status, including health
	./scripts/task-compose ps

shell: ## [stack] Shell inside the running app container
	./scripts/task-compose exec app /bin/bash

psql: ## [stack] psql in the local database, through compose exec (the db port is not published)
	./scripts/task-compose exec db sh -c 'exec psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

mcp-smoke: ## [mcp] Discovery/401/carve-out checks, prepaid operations and the OAuth login (throwaway mcp user) against this task's MCP
	./scripts/mcp-smoke.sh $$(./scripts/task-compose --url mcp) local
	@# the prepaid part needs a write credential: a throwaway local token, revoked afterwards
	@set -e; out="$$(mktemp)"; err="$$(mktemp)"; trap 'rm -f "$$out" "$$err"' EXIT; \
	./scripts/task-compose run --rm -T --no-deps mcp python -m bss_mcp.tokens create "smoke-$$$$" --scope write >"$$out" 2>"$$err"; \
	id="$$(sed -n 's/^id \([^ ]*\) .*/\1/p' "$$err" | head -1)"; \
	[ -n "$$id" ] || { echo 'mcp-smoke: could not create the local smoke token' >&2; exit 1; }; \
	status=0; MCP_SMOKE_TOKEN="$$(head -1 "$$out")" python3 scripts/mcp-prepaid-smoke.py $$(./scripts/task-compose --url mcp) || status=$$?; \
	./scripts/task-compose run --rm -T --no-deps mcp python -m bss_mcp.tokens revoke "$$id" >/dev/null 2>&1; \
	exit $$status
	@# the OAuth login: throwaway mcp and user accounts with random passwords (never printed),
	@# disabled afterwards whatever the result (users are never deleted)
	@set -e; run="smoke-$$$$-$$(date +%s)"; \
	mcp_pw="$$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"; \
	ui_pw="$$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"; \
	retire() { for who in "mcp-$$run" "ui-$$run"; do \
	  ./scripts/task-compose run --rm -T --no-deps app python -m app.users disable "$$who" >/dev/null 2>&1 || true; done; }; \
	trap retire EXIT; \
	printf '%s\n' "$$mcp_pw" | ./scripts/task-compose run --rm -T --no-deps app python -m app.users create "mcp-$$run" --role mcp >/dev/null; \
	printf '%s\n' "$$ui_pw" | ./scripts/task-compose run --rm -T --no-deps app python -m app.users create "ui-$$run" --role user >/dev/null; \
	MCP_SMOKE_USER="mcp-$$run" MCP_SMOKE_PASSWORD="$$mcp_pw" MCP_SMOKE_UI_USER="ui-$$run" MCP_SMOKE_UI_PASSWORD="$$ui_pw" \
	  python3 scripts/mcp-oauth-smoke.py $$(./scripts/task-compose --url mcp) local
	@# as deploy.sh does: remove the smoke's client registrations, at least the one just made
	@./scripts/task-compose run --rm -T --no-deps mcp python -m bss_mcp.tokens prune-smoke-clients 2>/dev/null \
	  | grep -E '^pruned [1-9]' || { echo 'mcp-smoke: the smoke client registration was not pruned' >&2; exit 1; }

mcp-token-create: ## [mcp] Local MCP static token, printed once: make mcp-token-create NAME=ci SCOPE=read|write
	@if [ -z "$(NAME)" ] || [ -z "$(SCOPE)" ]; then \
		echo 'usage: make mcp-token-create NAME=ci SCOPE=read|write' >&2; \
		exit 1; \
	fi
	./scripts/task-compose run --rm --no-deps mcp python -m bss_mcp.tokens create '$(NAME)' --scope '$(SCOPE)'

mcp-tokens: ## [mcp] Local MCP static tokens and live OAuth grants (ids only, never a token)
	./scripts/task-compose run --rm --no-deps mcp python -m bss_mcp.tokens list

mcp-token-revoke: ## [mcp] Revoke a local MCP static token (ID=<id>) or OAuth grant (GRANT=<grant id>)
	@if [ -n "$(ID)" ] && [ -z "$(GRANT)" ]; then \
		./scripts/task-compose run --rm --no-deps mcp python -m bss_mcp.tokens revoke '$(ID)'; \
	elif [ -n "$(GRANT)" ] && [ -z "$(ID)" ]; then \
		./scripts/task-compose run --rm --no-deps mcp python -m bss_mcp.tokens revoke-grant '$(GRANT)'; \
	else \
		echo 'usage: make mcp-token-revoke ID=<token id> | GRANT=<grant id>' >&2; \
		exit 1; \
	fi

# Heavy test runs go through scripts/with-test-lock: one at a time on the workstation, in
# any worktree, queued behind the commit hook's run and each other (#348). Light targets
# (lint, format, docs, builds) never take the lock; aggregates lock stage by stage.
LOCKED := ./scripts/with-test-lock

.PHONY: hook-cases
hook-cases: ## [test] The shell cases of the hook's own scripts (the hook's lock and plan, the billing status check); the commit hook runs them when scripts/ changes
	./scripts/test-billing-status-check.sh
	./scripts/test-pre-commit-lock.sh

# A pass on a clean tree is recorded (scripts/full_suite.py, .testmon/full-suite-passed.json):
# make check-pr reports it as full_suite, so a resumed session need not run the suite again.
.PHONY: full-suite-snapshot
test: full-suite-snapshot runtime-check deploy-check hook-cases ## [test] The FULL suite in the container (parallel; HYPOTHESIS_PROFILE=dev|ci), plus the harness and deploy script tests (runtime-check, deploy-check, hook-cases): nightly in CI, by hand before a PR (a pass on a clean tree is recorded for make check-pr)
	$(LOCKED) ./scripts/task-compose run --rm -e HYPOTHESIS_PROFILE=$(HYPOTHESIS_PROFILE) $(if $(PLAN_CAPTURE_DIR),-e BSS_PLAN_CAPTURE_DIR=$(PLAN_CAPTURE_DIR)) oneoff pytest -n auto
	@python3 -B scripts/full_suite.py record

full-suite-snapshot: | testmon-dir
	@python3 -B scripts/full_suite.py snapshot

test-smoke: ## [test] The smoke subset (pytest -m smoke): what CI runs on pull requests that can affect it
	$(LOCKED) ./scripts/task-compose run --rm -e HYPOTHESIS_PROFILE=$(HYPOTHESIS_PROFILE) oneoff pytest -n auto -m smoke

# pytest-testmon runs the tests whose code changed since their last recorded run, plus new and
# previously failed ones (selection is off under -m or -k, so this never filters). The first
# run on a fresh checkout is the whole suite; it records into .testmon/.testmondata.
test-changed: ## [test] Only the tests affected by the change since their last run (pytest-testmon); the pre-commit hook runs it after test-smoke
	$(LOCKED) ./scripts/task-compose run --rm -e HYPOTHESIS_PROFILE=$(HYPOTHESIS_PROFILE) oneoff pytest -n auto --testmon

# The whole backend suite for the commit hook (scripts/hook_selection.py), recorded by
# testmon (--testmon-noselect: every test runs), so a failure is followed by a short retry.
# When pytest ran every test (exit 0 or 1), the digest of the files that called for the
# suite goes to .testmon/full-suite (emptied first); the hook then knows this content was
# tested in full. Without runtime-check and deploy-check: the hook runs those by its own
# path rules.
.PHONY: test-recorded test-files
test-recorded: ## [test] The hook's full backend suite, recorded for testmon (no harness or deploy suites; by hand use make test)
	@: >.testmon/full-suite
	$(LOCKED) ./scripts/task-compose run --rm -e HYPOTHESIS_PROFILE=$(HYPOTHESIS_PROFILE) -e FULL_SUITE_DIGEST="$$(python3 -B scripts/hook_selection.py digest)" oneoff sh -c 'pytest -n auto --testmon-noselect; status=$$?; if [ $$status -le 1 ]; then echo "$$FULL_SUITE_DIGEST" >/testmon/full-suite; fi; exit $$status'

comma := ,
test-files: ## [test] The listed test files (TESTS=tests/a.py,tests/b.py): the hook runs the readers of a staged data file
	@test -n "$(TESTS)" || { echo "TESTS=tests/a.py,tests/b.py is required" >&2; exit 2; }
	$(LOCKED) ./scripts/task-compose run --rm -e HYPOTHESIS_PROFILE=$(HYPOTHESIS_PROFILE) oneoff pytest -n auto $(subst $(comma), ,$(TESTS))

.PHONY: testmon-dir
test test-smoke test-changed test-recorded test-files check-preflight: | testmon-dir
testmon-dir:
	@# Recover only an empty directory auto-created by Docker; preserve existing data.
	@if [ -d .testmon ] && [ ! -w .testmon ]; then rmdir .testmon; fi
	@mkdir -p .testmon

seed: ## [stack] LOCAL demo data through the public TMF API (tests/seed.py); refuses APP_ENV=prod; a second run is a no-op
	./scripts/task-compose run --rm app python -m tests.seed

# --- the local load-test stack (compose.loadtest.yaml, docs/loadtest.md) ---
LT := ./scripts/task-compose --loadtest
LT_SERVICES := lt-db lt-app lt-worker lt-mcp
LT_SECRET := $(or $(XDG_CONFIG_HOME),$(HOME)/.config)/bss/loadtest/operator-password
LT_PSQL := $(LT) exec -T lt-db sh -c 'exec psql -X -q -v ON_ERROR_STOP=1 -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -f -'
N ?=
PROCESSES ?= 4
DAYS ?= 90
USAGE_DAYS ?= 30
SEED_ARGS ?=
LT_DATA := $(or $(XDG_DATA_HOME),$(HOME)/.local/share)/bss/loadtest
.PHONY: loadtest loadtest-round-trips loadtest-up loadtest-down loadtest-ps loadtest-logs loadtest-migrate loadtest-reset loadtest-secret loadtest-seed loadtest-dump loadtest-restore loadtest-pg-stats-reset loadtest-pg-stats loadtest-billing-run loadtest-growth

.PHONY: loadtest-plan-sweep
loadtest-plan-sweep: ## [loadtest] Collect full-suite SQL and EXPLAIN on seed100k (PHASE=all|collect|measure [SWEEP=plan-sweep-<stamp>]); restore seed100k first
	@mkdir -p loadtest/out
	python3 -B loadtest/plan_sweep.py --phase $(or $(PHASE),all) $(if $(SWEEP),--run $(SWEEP))

loadtest-up: ## [loadtest] Start the load-test stack (lt-db, lt-app, lt-worker, lt-mcp; pinned to LOADTEST_SUT_CPUS, default 4-7), migrated
	./scripts/task-compose build app
	$(LT) build lt-app
	$(LT) up -d --wait lt-db
	@$(MAKE) --no-print-directory loadtest-migrate
	$(LT) up -d --wait $(LT_SERVICES)

loadtest-down: ## [loadtest] Stop and remove the load-test containers (its database volume is kept)
	$(LT) rm --stop --force $(LT_SERVICES) lt-tools

loadtest-ps: ## [loadtest] Load-test containers, their health and limits
	$(LT) ps $(LT_SERVICES)

loadtest-logs: ## [loadtest] Last 5 minutes of the load-test app and worker logs
	$(LT) logs --since=5m --tail=200 lt-app lt-worker

loadtest-migrate: ## [loadtest] Apply Alembic migrations to the load-test database
	@mkdir -p loadtest/out
	$(LT) run --rm lt-tools alembic upgrade head

loadtest-reset: ## [loadtest] Empty the load-test database (drop, create, migrate); the app and worker are stopped first
	$(LT) rm --stop --force lt-app lt-worker lt-mcp
	$(LT) up -d --wait lt-db
	$(LT) exec -T lt-db sh -c 'dropdb -U "$$POSTGRES_USER" --force --if-exists "$$POSTGRES_DB" && createdb -U "$$POSTGRES_USER" "$$POSTGRES_DB"'
	@$(MAKE) --no-print-directory loadtest-migrate

loadtest-secret: ## [loadtest] Create the load-test operators' password file outside the repo (once; never printed)
	@if [ -s "$(LT_SECRET)" ]; then echo "loadtest-secret: $(LT_SECRET) exists"; else \
	  mkdir -p "$$(dirname "$(LT_SECRET)")" && umask 077 && \
	  python3 -c 'import secrets; print(secrets.token_urlsafe(24))' > "$(LT_SECRET)" && \
	  echo "loadtest-secret: created $(LT_SECRET)"; fi

loadtest-seed: ## [loadtest] Build the load-test dataset in lt-db (N=<subscribers> [PROCESSES=4] [DAYS=90] [USAGE_DAYS=30] [SEED_ARGS=...]); resumable
	@test -n "$(N)" || { echo "loadtest-seed: N=<subscribers> is required" >&2; exit 2; }
	@test -s "$(LT_SECRET)" || { echo "loadtest-seed: run make loadtest-secret first" >&2; exit 2; }
	@mkdir -p loadtest/out
	$(LT) rm --stop --force lt-worker
	$(LT) up -d --wait lt-db
	LOADTEST_OPERATOR_PASSWORD="$$(cat "$(LT_SECRET)")" $(LT) run --rm lt-tools \
	  python -m loadtest.seed --n $(N) --processes $(PROCESSES) --days $(DAYS) --usage-days $(USAGE_DAYS) $(SEED_ARGS)

loadtest-dump: ## [loadtest] Save the load-test database to ~/.local/share/bss/loadtest/<NAME>.dump (outside every worktree)
	@test -n "$(NAME)" || { echo "loadtest-dump: NAME=<name> is required" >&2; exit 2; }
	@mkdir -p "$(LT_DATA)"
	$(LT) exec -T lt-db sh -c 'exec pg_dump -Fc -U "$$POSTGRES_USER" "$$POSTGRES_DB"' > "$(LT_DATA)/$(NAME).tmp"
	mv "$(LT_DATA)/$(NAME).tmp" "$(LT_DATA)/$(NAME).dump"
	@ls -lh "$(LT_DATA)/$(NAME).dump"

loadtest-restore: ## [loadtest] Replace the load-test database with ~/.local/share/bss/loadtest/<NAME>.dump, then migrate it
	@test -s "$(LT_DATA)/$(NAME).dump" || { echo "loadtest-restore: no $(LT_DATA)/$(NAME).dump" >&2; exit 2; }
	$(LT) rm --stop --force lt-app lt-worker lt-mcp
	$(LT) up -d --wait lt-db
	$(LT) exec -T lt-db sh -c 'dropdb -U "$$POSTGRES_USER" --force --if-exists "$$POSTGRES_DB" && createdb -U "$$POSTGRES_USER" "$$POSTGRES_DB"'
	$(LT) exec -T lt-db sh -c 'exec pg_restore -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" --no-owner -j 1' < "$(LT_DATA)/$(NAME).dump"
	@$(MAKE) --no-print-directory loadtest-migrate
	@# pg_restore leaves no statistics: plans without them scan whole tables
	$(LT) exec -T lt-db sh -c 'exec psql -X -q -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -c ANALYZE'

loadtest-pg-stats-reset: ## [loadtest] Reset pg_stat_statements in the load-test database
	$(LT_PSQL) < loadtest/pg-stats-reset.sql

loadtest-pg-stats: ## [loadtest] Top 20 statements by total time since the last reset, also to loadtest/out/pg-stats-<time>.txt
	@mkdir -p loadtest/out
	$(LT_PSQL) < loadtest/pg-stats.sql | tee "loadtest/out/pg-stats-$$(date +%Y%m%dT%H%M%S).txt"

loadtest-growth: ## [loadtest] Rows and bytes per table, and rows per month of the growing tables (#282), also to loadtest/out/growth-<time>.txt
	@mkdir -p loadtest/out
	$(LT_PSQL) < loadtest/growth.sql | tee "loadtest/out/growth-$$(date +%Y%m%dT%H%M%S).txt"

loadtest: ## [loadtest] Run a k6 scenario on the load-test stack (SCENARIO=smoke|steady|stress|soak|billing-night|prod-shaped [MULT=1] [DURATION=]); report in loadtest/out/
	@test -n "$(SCENARIO)" || { echo "loadtest: SCENARIO=<name> is required" >&2; exit 2; }
	@mkdir -p loadtest/out
	python3 -B loadtest/run.py --scenario $(SCENARIO) --mult $(or $(MULT),1) $(if $(DURATION),--duration $(DURATION))

loadtest-round-trips: ## [loadtest] Statements per billed account, swept account and usage record, and ledger checks per prepaid transaction, on the restored dataset, rolled back (DATE=YYYY-MM-DD [ACCOUNTS=2000] [RECORDS=5000] [LEDGER=500]); report in loadtest/out/
	@test -n "$(DATE)" || { echo "loadtest-round-trips: DATE=<run date> is required" >&2; exit 2; }
	@mkdir -p loadtest/out
	@# nothing else may write the dataset while it is measured
	$(LT) rm --stop --force lt-app lt-worker lt-mcp
	$(LT) up -d --wait lt-db
	$(LT) run --rm lt-tools python -m loadtest.round_trips --date $(DATE) \
	  --accounts $(or $(ACCOUNTS),2000) --records $(or $(RECORDS),5000) --ledger $(or $(LEDGER),500)

loadtest-billing-run: ## [loadtest] Measure the daily billing run of DATE on the restored dataset (DATE=YYYY-MM-DD [CPUS=1]); report in loadtest/out/
	@test -n "$(DATE)" || { echo "loadtest-billing-run: DATE=<run date> is required" >&2; exit 2; }
	@mkdir -p loadtest/out
	$(LT) up -d --wait lt-db lt-app
	python3 -B loadtest/billing_run.py --date $(DATE) --cpus $(or $(CPUS),1)

worker-once: ## [stack] One wake-up of the billing worker against the local stack (due dates, queue, flips), then exit
	./scripts/task-compose run --rm app python -m app.worker --once

migrate: ## [stack] Apply Alembic migrations against the local stack
	./scripts/task-compose run --rm app alembic upgrade head

lint: ## [test] Lint and format check
	./scripts/task-compose run --rm --no-deps app ruff check .
	./scripts/task-compose run --rm --no-deps app ruff format --check .

fmt: ## [test] Auto-format the code (writable `tools` service; `app` mounts sources read-only)
	./scripts/task-compose run --rm tools sh -c 'ruff format . && ruff check --fix .'

.PHONY: scripts-lint scripts-fmt docs-links
# Selection is JSON data, including when supplied on Make's command line. Do not
# evaluate Make expressions in it, or automatically export the original variable.
unexport SCRIPT_FILES
scripts-lint scripts-fmt: export BSS_SCRIPT_FILES := $(value SCRIPT_FILES)
scripts-lint: ## [test] Lint/check formatting of changed Python harness scripts (optional SCRIPT_FILES=JSON-list)
	python3 -B scripts/script_quality.py lint

scripts-fmt: ## [test] Format changed Python harness scripts (optional SCRIPT_FILES=JSON-list)
	python3 -B scripts/script_quality.py format

# --- admin UI (frontend/): Node runs only in the `web` container, never on the host ---
# Only web-install downloads (npm ci, `web` service). Every other target runs in the
# offline `web-offline` service and first only CHECKS that node_modules matches
# package-lock.json (frontend/scripts/check-deps.sh), failing with "run make web-install"
# - never an implicit install. Lint, test and build regenerate the types from spec/.
WEB := ./scripts/task-compose run --rm --no-deps web-offline
WEB_DEPS := sh scripts/check-deps.sh

web-install: ## [ui] Install the UI's npm dependencies exactly as locked (npm ci; the only step with network)
	./scripts/task-compose run --rm --no-deps web sh scripts/install-deps.sh

# Run from the PRIMARY checkout (administration-only: web-install is refused there). deploy.sh
# builds the UI there and only checks node_modules against the lockfile; this refreshes it.
deploy-web-install: ## [ops] Primary checkout only: npm ci for the deploy's UI build, after package-lock.json changed
	./scripts/task-compose --deploy-web-install

web-dev: ## [ui] UI dev server on the task WEB_PORT (starts the local stack; proxies /tmf-api and /internal)
	./scripts/task-compose run --rm --service-ports web sh -c '$(WEB_DEPS) && npm run dev'

# The bss-build marker in index.html: deploy.sh passes the commit (WEB_BUILD=<tag>), the web
# smoke test reads it back through Caddy.
WEB_BUILD ?= dev

web-build: ## [ui] Build the UI to frontend/dist (WEB_BUILD=<tag> sets its bss-build marker)
	./scripts/task-compose run --rm --no-deps -e VITE_BSS_BUILD=$(WEB_BUILD) web-offline sh -c '$(WEB_DEPS) && npm run build'

web-lint: ## [ui] UI type check, eslint and prettier --check
	$(WEB) sh -c '$(WEB_DEPS) && npm run lint'

web-test: ## [ui] UI tests; second timezone in CI or when frontend/src changed
	python3 -B scripts/task_quality.py web-test

.PHONY: web-test-once web-test-timezone
web-test-once:
	$(LOCKED) $(WEB) sh -c '$(WEB_DEPS) && npm run test:local'

web-test-timezone:
	$(LOCKED) $(WEB) sh -c '$(WEB_DEPS) && npm run test:timezone'

web-types: ## [ui] Regenerate the UI's TypeScript types from spec/
	$(WEB) sh -c '$(WEB_DEPS) && npm run types'

web-fmt: ## [ui] Format the UI sources (prettier --write)
	$(WEB) sh -c '$(WEB_DEPS) && npm run format'

# Disposable local sign-in users for the browser tests (tests/e2e_users.py): credentials in
# the gitignored frontend/.e2e/, which the e2e service sees as /web/.e2e. A cleanup first
# clears what an interrupted run left; the one after the run disables the users and removes
# the file whether the tests passed or not.
E2E_USERS = ./scripts/task-compose run --rm -v $(CURDIR)/frontend/.e2e:/e2e app python -m tests.e2e_users

web-e2e: ## [ui] e2e smoke (Playwright) on the built UI and the seeded local stack; billing data read-only
	@$(MAKE) --no-print-directory seed
	@$(MAKE) --no-print-directory web-build
	mkdir -p frontend/.e2e
	$(E2E_USERS) cleanup
	$(E2E_USERS) prepare --numbered-offering
	./scripts/task-compose up -d e2e-worker
	$(LOCKED) ./scripts/task-compose run --rm e2e; rc=$$?; $(E2E_USERS) cleanup || rc=1; ./scripts/task-compose rm -sf web-preview e2e-worker e2e-care >/dev/null 2>&1; exit $$rc

web-shots: ## [ui] Capture Ledger UI reference PNGs at 1280 and 1600 px
	rm -rf frontend/.shots
	mkdir -p frontend/.shots
	@$(MAKE) --no-print-directory seed
	@$(MAKE) --no-print-directory web-build
	mkdir -p frontend/.e2e
	$(E2E_USERS) cleanup
	$(E2E_USERS) prepare
	$(LOCKED) ./scripts/task-compose run --rm e2e npx playwright test -c playwright.shots.config.ts; rc=$$?; $(E2E_USERS) cleanup || rc=1; ./scripts/task-compose rm -sf web-preview >/dev/null 2>&1; exit $$rc

spec-verify-upstream: ## [docs] Re-fetch the spec blob SHAs from GitHub and compare with spec/README.md (network; not in make test)
	python3 scripts/spec-verify-upstream.py

smoke: ## [stack] Smoke-test the local instance
	./scripts/smoke.sh http://$$(./scripts/task-compose port app 8080)

deploy: ## [ops] Build locally and ship to the VPS (a failure after the new release starts rolls back)
	./scripts/deploy.sh

rollback: ## [ops] Roll the VPS back to the previous release (DRY_RUN=1 shows the plan)
	DRY_RUN=$(DRY_RUN) ./scripts/deploy.sh --rollback

# On demand only, when a change needs stronger testing; the pre-commit hook is the
# regular gate. Target-specific: also applies to the `test` prerequisite.
check: ## [task] Technical readiness in a validated task (full existing local checks)
	@python3 -B scripts/task.py $@

.PHONY: format review-task show-task
format: ## [task] Format backend, changed harness scripts and frontend in a validated task
	@python3 -B scripts/task.py $@

review-task: ## [task] Non-destructive review of task identity and local Git safety
	@python3 -B scripts/task.py $@

show-task: ## [task] Show this task's issue (title, body, labels, comments) as JSON
	@python3 -B scripts/task.py $@

.PHONY: check-preflight
check-preflight: migrate
	$(LOCKED) ./scripts/task-compose run --rm oneoff pytest -q tests/test_migrations.py::test_alembic_upgrade_head_on_clean_database tests/test_migrations.py::test_cloned_test_databases_are_at_the_current_head tests/test_constraints.py tests/test_worker_schema.py

# The commit was tested by the pre-commit hook: deploying does not test again.
deployed: ## [ops] Show the last deploy recorded by scripts/deploy.sh and the commits not deployed yet
	@if [ ! -f .deployed ]; then \
		echo "no deploy recorded on this workstation (.deployed is written by make deploy)"; \
	else \
		cat .deployed; \
		if grep -q '^checks=rolled-back$$' .deployed; then \
			echo "rolled back from $$(sed -n 's/^rolled_back_from=//p' .deployed): the VPS runs the release above"; \
		fi; \
		echo "not deployed yet:"; \
		git log --oneline "$$(sed -n 's/^commit=//p' .deployed)..HEAD" | sed 's/^/  /' | grep . \
			|| echo "  (nothing - HEAD is deployed)"; \
	fi

verify: deploy ## [ops] Deploy and confirm the remote instance is healthy (health, smoke, UI, MCP)
	@echo "verified: the remote instance is healthy"

tunnel: ## [ops] Forward the remote app to localhost:18081 (Ctrl-C to stop)
	ssh $(SSH_OPTS) -N -L 18081:127.0.0.1:18081 $(REMOTE)

ssh: ## [ops] Shell on the VPS
	ssh $(SSH_OPTS) $(REMOTE)

# the service `make remote` runs in; SERVICE=mcp for the token tooling (its volume)
SERVICE := app
remote: ## [ops] Run a one-off command against the deployed image on the VPS: make remote CMD="alembic current" [SERVICE=mcp]
	@if [ -z "$(CMD)" ]; then \
		echo 'usage: make remote CMD="alembic current"' >&2; \
		exit 1; \
	fi
	ssh $(SSH_OPTS) $(REMOTE) "cd $(REMOTE_DIR) && IMAGE_NAME=$(IMAGE_NAME) TAG=$(TAG) $(COMPOSE_PROD) run --rm --no-deps $(SERVICE) $(CMD)"

progress: ## [docs] Write .progress/PROGRESS.md + status.json (scope.yaml vs the code; reruns the full suite, over 10 min)
	@mkdir -p .progress
	$(LOCKED) ./scripts/task-compose run --rm -T app python -m tests.progress_collect < docs/progress/scope.yaml > .progress/backend.json
	./scripts/task-compose run --rm --no-deps -T web-offline sh -c '$(WEB_DEPS) && node scripts/ui-markers.mjs' > .progress/ui.json
	$(LOCKED) ./scripts/task-compose run --rm --no-deps -T web-offline sh -c '$(WEB_DEPS) && npm run types >&2 && UI_TESTED_OUT=/tmp/ui-tested.json npx vitest run >&2 && cat /tmp/ui-tested.json' > .progress/ui-tested.json
	@python3 scripts/progress/generate.py; result=$$?; echo "Progress report: .progress/PROGRESS.md (.progress/status.json)"; exit $$result

docs: ## [docs] Regenerate docs/api/ and docs/db/ (app.openapi() vs spec/, pg_catalog at migration head)
	@mkdir -p .docs
	./scripts/task-compose run --rm -T app python -m tests.docs_collect > .docs/collected.json
	python3 scripts/docs/generate.py

docs-check: docs ## [docs] Fail if the committed docs/api/ or docs/db/ differ from a fresh `make docs`
	@if ! git diff --quiet -- docs/api docs/db || [ -n "$$(git ls-files --others --exclude-standard -- docs/api docs/db)" ]; then \
		git status --short -- docs/api docs/db; \
		echo "docs-check: docs/api or docs/db is out of date - commit the output of make docs" >&2; \
		exit 1; \
	fi
	@echo "docs-check: docs/api and docs/db match the code"

docs-links: ## [docs] Check Markdown links, anchors and the make targets and scripts the agent docs name (no Docker)
	python3 -B scripts/docs_links.py

# Every scripts/test_*.py is a suite: a new file cannot be forgotten (test_smoke_retry.py
# was, while the list was kept by hand). test_telemetry_database.py needs the running
# telemetry database and is `make telemetry-test`.
# The deploy script's suite is the longest by far and tests the human's deploy, not the
# agents' workflow: it is `make deploy-check`, run when what it reads changes.
DEPLOY_SUITES := scripts/test_deploy_rollback.py
RUNTIME_SUITES := $(filter-out scripts/test_telemetry_database.py $(DEPLOY_SUITES),$(sort $(wildcard scripts/test_*.py)))

runtime-check: ## [test] The harness script tests (every scripts/test_*.py but the deploy suite, in parallel parts), no Docker or secrets; the commit hook runs them when scripts/, Makefile, .claude/ or .codex/ change
	$(LOCKED) python3 -B scripts/run_suites.py $(RUNTIME_SUITES)

.PHONY: deploy-check
deploy-check: ## [test] The deploy script's tests (scripts/test_deploy_rollback.py), no Docker or network; the commit hook runs them when the deploy or smoke scripts, the compose files or the Makefile change
	$(LOCKED) python3 -B scripts/run_suites.py $(DEPLOY_SUITES)

.PHONY: telemetry-collect telemetry-probe telemetry-watch
telemetry-watch: export BSS_COLLECT_TASK = $(TASK)
telemetry-watch: ## [telemetry] Retry final usage after an ended session (optional TASK=; bounded to two minutes)
	python3 -B scripts/task_collect.py --watch

telemetry-probe: export BSS_COLLECT_CLIENT = $(CLIENT)
telemetry-probe: export BSS_COLLECT_SESSION = $(SESSION_ID)
telemetry-probe: ## [telemetry] Verify a real client source without attributing historical usage to an issue
	python3 -B scripts/task_collect.py --probe

telemetry-collect: export BSS_COLLECT_TASK = $(TASK)
telemetry-collect: export BSS_COLLECT_CLIENT = $(CLIENT)
telemetry-collect: export BSS_COLLECT_SESSION = $(SESSION_ID)
telemetry-collect: export BSS_COLLECT_CLOSE = $(CLOSE)
telemetry-collect: export BSS_COLLECT_RESET = $(RESET)
telemetry-collect: ## [telemetry] Collect bound machine usage (optional TASK= CLIENT= SESSION_ID= CLOSE=1; RESET=1 re-attributes TASK from sources)
	python3 -B scripts/task_collect.py

.PHONY: telemetry-report telemetry-summary telemetry-facts
telemetry-report: ## [telemetry] Report task usage, priced subtotals and observed lifecycle metrics as JSON
	python3 -B scripts/task_usage.py report

telemetry-summary: ## [telemetry] One row per issue (tokens, cost, active/wall time, prompts, friction, PR size) and rollups (JSON=1 for JSON)
	python3 -B scripts/task_summary.py $(if $(filter 1,$(JSON)),--json,)

telemetry-facts: export BSS_FACTS_TASK = $(TASK)
telemetry-facts: ## [telemetry] Capture or refresh GitHub facts (PR size, labels, epic, follow-ups) for merged tasks (optional TASK=)
	python3 -B scripts/task_facts.py $(if $(TASK),--task "$$BSS_FACTS_TASK",)

.PHONY: telemetry-up telemetry-down telemetry-migrate telemetry-health telemetry-test telemetry-prices
telemetry-test: ## [telemetry] Test the running telemetry schema (all test data rolled back)
	python3 -B scripts/test_telemetry_database.py

telemetry-up: ## [telemetry] Start shared persistent telemetry storage and apply its migrations
	python3 -B scripts/task_telemetry.py up

telemetry-down: ## [telemetry] Stop shared telemetry storage, preserving its volume
	python3 -B scripts/task_telemetry.py down

telemetry-migrate: ## [telemetry] Apply versioned telemetry schema migrations
	python3 -B scripts/task_telemetry.py migrate

telemetry-prices: ## [telemetry] Load the hand-maintained model list prices (scripts/telemetry/prices.json)
	python3 -B scripts/task_telemetry.py prices

telemetry-health: ## [telemetry] Check shared telemetry database and schema
	python3 -B scripts/task_telemetry.py health

hooks: ## [stack] Install the pre-commit (lint + test) and pre-push (secrets) hooks
	python3 scripts/install-hooks.py


clean: ## [stack] Remove local containers AND the local database volume
	./scripts/task-compose down -v

wait-local:
	@echo "waiting for app to become healthy..."; \
	for i in $$(seq 1 30); do \
	  cid=$$(./scripts/task-compose ps -q app); \
	  st=$$(docker inspect -f '{{.State.Health.Status}}' $$cid 2>/dev/null || echo starting); \
	  if [ "$$st" = "healthy" ]; then echo "app is healthy on http://$$(./scripts/task-compose port app 8080)"; exit 0; fi; \
	  if [ "$$st" = "unhealthy" ]; then ./scripts/task-compose logs --tail=50 app; exit 1; fi; \
	  sleep 2; \
	done; \
	./scripts/task-compose logs --tail=50 app; echo "app did not become healthy in time"; exit 1

.PHONY: runtime-check runtime-context
runtime-context:
	@./scripts/task-compose --check

up down restart logs ps shell psql mcp-smoke mcp-token-create mcp-tokens mcp-token-revoke test test-smoke test-changed seed worker-once migrate lint fmt web-install web-dev web-build web-lint web-test web-types web-fmt web-e2e web-shots smoke progress docs clean wait-local: | runtime-context

.PHONY: commit push
commit: export MESSAGE := $(MESSAGE)
commit: export MESSAGE_FILE := $(MESSAGE_FILE)
commit: export FILES := $(FILES)
commit: export FILES_FILE := $(FILES_FILE)
commit: export DRY_RUN := $(or $(DRY_RUN),0)
commit: ## [task] Commit explicit task files (FILES="a b" or a JSON array, or FILES_FILE=/abs/path; MESSAGE or MESSAGE_FILE=/abs/path)
	@python3 -B scripts/task.py $@

push: ## [task] Publish only the current validated task branch to origin
	@python3 -B scripts/task.py $@

.PHONY: sync-task
sync-task: ## [task] Merge current origin/master into a clean task, preserving history and hooks
	@python3 -B scripts/task.py $@

.PHONY: create-pr check-pr
create-pr: export PR_TITLE := $(PR_TITLE)
create-pr: export PR_BODY_FILE := $(PR_BODY_FILE)
create-pr: ## [task] Publish the task branch and create or confirm its PR
	@python3 -B scripts/task.py $@

check-pr: export HUMAN_APPROVAL := $(HUMAN_APPROVAL)
check-pr: ## [task] Report task PR readiness, including human review gates (never merges)
	@python3 -B scripts/task.py $@

.PHONY: clean-tasks
clean-tasks: export CONFIRM := $(CONFIRM)
clean-tasks: ## [task] List leftovers (volumes, images, branches, credentials) of finished tasks; CONFIRM=1 removes them. Primary only
	@python3 -B scripts/task.py $@

.PHONY: merge-task finish-task
merge-task: export HUMAN_APPROVAL := $(HUMAN_APPROVAL)
merge-task: ## [task] Merge this task PR only when all repository and human gates permit
	@python3 -B scripts/task.py $@

finish-task: ## [task] Safely finish a completed slot task, from primary master (SLOT=worker1)
	@python3 -B "$(HARNESS_DIR)scripts/task.py" $@
