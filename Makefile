## The only supported command interface. Claude Code is told to use these targets and
## nothing else, so every action has a stable name and an honest exit code.

SHELL := /bin/bash
COMPOSE_PROD := docker compose -f compose.yaml -f compose.prod.yaml
# Same timeouts as scripts/deploy.sh: a stalled connection fails fast instead of hanging.
SSH_OPTS := -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o BatchMode=yes
REMOTE ?= vps-test
REMOTE_DIR ?= bss
IMAGE_NAME ?= bss-app
TAG ?= $(shell git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)
# Hypothesis examples per property test (tests/conftest.py): dev = 50, ci = 200 (verify).
HYPOTHESIS_PROFILE ?= dev

.PHONY: help up down restart logs ps shell test test-smoke test-changed lint fmt smoke deploy rollback check verify tunnel ssh clean migrate worker-once remote hooks spec-verify-upstream seed web-install deploy-web-install web-dev web-build web-lint web-test web-types web-fmt web-e2e web-shots docs docs-check mcp-smoke mcp-token-create mcp-tokens mcp-token-revoke

help: ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

ifneq ($(origin SLOT), undefined)
export BSS_SLOT := $(SLOT)
endif

.PHONY: start-task new-priority-task release-slot
start-task: ## Prepare the next Ready issue in an automatically selected worker slot (JSON=1 for agents)
	@python3 -B scripts/start_task.py $(if $(filter 1,$(JSON)),--json,)

new-priority-task: export BSS_NEW_TASK_TITLE := $(TITLE)
new-priority-task: export BSS_NEW_TASK_BODY := $(BODY)
new-priority-task: export BSS_NEW_TASK_BODY_FILE := $(BODY_FILE)
new-priority-task: export BSS_NEW_TASK_ISSUE := $(ISSUE)
new-priority-task: export BSS_NEW_TASK_LABELS := $(LABELS)
new-priority-task: ## Create an approved P0 issue first in Ready; start separately (TITLE= BODY=|BODY_FILE=/abs/path [LABELS=a,b]); ISSUE=<n> finishes a preserved one
	@python3 -B scripts/start_task.py --new-priority-task $(if $(filter 1,$(JSON)),--json,)

release-slot: ## Release a slot after its task worktree was safely removed (SLOT=worker1)
	@python3 -B scripts/start_task.py --release-slot

up: ## Start the local stack (app + postgres, hot reload)
	./scripts/task-compose up -d --build
	@$(MAKE) --no-print-directory wait-local

down: ## Stop the local stack (volumes are kept)
	./scripts/task-compose --profile '*' down

restart: down up ## Restart the local stack

logs: ## Last 5 minutes of app logs
	./scripts/task-compose logs --since=5m --tail=200 app

ps: ## Container status, including health
	./scripts/task-compose ps

shell: ## Shell inside the running app container
	./scripts/task-compose exec app /bin/bash

psql: ## psql in the local database, through compose exec (the db port is not published)
	./scripts/task-compose exec db sh -c 'exec psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

mcp-smoke: ## Discovery/401/carve-out checks, prepaid operations and the OAuth login (throwaway mcp user) against this task's MCP
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

mcp-token-create: ## Local MCP static token, printed once: make mcp-token-create NAME=ci SCOPE=read|write
	@if [ -z "$(NAME)" ] || [ -z "$(SCOPE)" ]; then \
		echo 'usage: make mcp-token-create NAME=ci SCOPE=read|write' >&2; \
		exit 1; \
	fi
	./scripts/task-compose run --rm --no-deps mcp python -m bss_mcp.tokens create '$(NAME)' --scope '$(SCOPE)'

mcp-tokens: ## Local MCP static tokens and live OAuth grants (ids only, never a token)
	./scripts/task-compose run --rm --no-deps mcp python -m bss_mcp.tokens list

mcp-token-revoke: ## Revoke a local MCP static token (ID=<id>) or OAuth grant (GRANT=<grant id>)
	@if [ -n "$(ID)" ] && [ -z "$(GRANT)" ]; then \
		./scripts/task-compose run --rm --no-deps mcp python -m bss_mcp.tokens revoke '$(ID)'; \
	elif [ -n "$(GRANT)" ] && [ -z "$(ID)" ]; then \
		./scripts/task-compose run --rm --no-deps mcp python -m bss_mcp.tokens revoke-grant '$(GRANT)'; \
	else \
		echo 'usage: make mcp-token-revoke ID=<token id> | GRANT=<grant id>' >&2; \
		exit 1; \
	fi

test: runtime-check ## The FULL suite in the container (parallel; HYPOTHESIS_PROFILE=dev|ci), plus the harness script tests (runtime-check): nightly in CI, by hand before a PR
	./scripts/test-billing-status-check.sh
	./scripts/test-pre-commit-lock.sh
	./scripts/task-compose run --rm -e HYPOTHESIS_PROFILE=$(HYPOTHESIS_PROFILE) oneoff pytest -n auto

test-smoke: ## The smoke subset (pytest -m smoke) and the scripts' own cases: what CI runs on pull requests and master
	./scripts/test-billing-status-check.sh
	./scripts/test-pre-commit-lock.sh
	./scripts/task-compose run --rm -e HYPOTHESIS_PROFILE=$(HYPOTHESIS_PROFILE) oneoff pytest -n auto -m smoke

# pytest-testmon runs the tests whose code changed since their last recorded run, plus new and
# previously failed ones (selection is off under -m or -k, so this never filters). The first
# run on a fresh checkout is the whole suite; it records into .testmon/.testmondata.
test-changed: ## Only the tests affected by the change since their last run (pytest-testmon); the pre-commit hook runs it after test-smoke
	./scripts/task-compose run --rm -e HYPOTHESIS_PROFILE=$(HYPOTHESIS_PROFILE) oneoff pytest -n auto --testmon

.PHONY: testmon-dir
test test-smoke test-changed check-preflight: | testmon-dir
testmon-dir:
	@# Recover only an empty directory auto-created by Docker; preserve existing data.
	@if [ -d .testmon ] && [ ! -w .testmon ]; then rmdir .testmon; fi
	@mkdir -p .testmon

seed: ## LOCAL demo data through the public TMF API (tests/seed.py); refuses APP_ENV=prod; a second run is a no-op
	./scripts/task-compose run --rm app python -m tests.seed

worker-once: ## One wake-up of the billing worker against the local stack (due dates, queue, flips), then exit
	./scripts/task-compose run --rm app python -m app.worker --once

migrate: ## Apply Alembic migrations against the local stack
	./scripts/task-compose run --rm app alembic upgrade head

lint: ## Lint and format check
	./scripts/task-compose run --rm --no-deps app ruff check .
	./scripts/task-compose run --rm --no-deps app ruff format --check .

fmt: ## Auto-format the code (writable `tools` service; `app` mounts sources read-only)
	./scripts/task-compose run --rm tools sh -c 'ruff format . && ruff check --fix .'

.PHONY: scripts-lint scripts-fmt docs-links
# Selection is JSON data, including when supplied on Make's command line. Do not
# evaluate Make expressions in it, or automatically export the original variable.
unexport SCRIPT_FILES
scripts-lint scripts-fmt: export BSS_SCRIPT_FILES := $(value SCRIPT_FILES)
scripts-lint: ## Lint/check formatting of changed Python harness scripts (optional SCRIPT_FILES=JSON-list)
	python3 -B scripts/script_quality.py lint

scripts-fmt: ## Format changed Python harness scripts (optional SCRIPT_FILES=JSON-list)
	python3 -B scripts/script_quality.py format

# --- admin UI (frontend/): Node runs only in the `web` container, never on the host ---
# Only web-install downloads (npm ci, `web` service). Every other target runs in the
# offline `web-offline` service and first only CHECKS that node_modules matches
# package-lock.json (frontend/scripts/check-deps.sh), failing with "run make web-install"
# - never an implicit install. Lint, test and build regenerate the types from spec/.
WEB := ./scripts/task-compose run --rm --no-deps web-offline
WEB_DEPS := sh scripts/check-deps.sh

web-install: ## Install the UI's npm dependencies exactly as locked (npm ci; the only step with network)
	./scripts/task-compose run --rm --no-deps web sh scripts/install-deps.sh

# Run from the PRIMARY checkout (administration-only: web-install is refused there). deploy.sh
# builds the UI there and only checks node_modules against the lockfile; this refreshes it.
deploy-web-install: ## Primary checkout only: npm ci for the deploy's UI build, after package-lock.json changed
	./scripts/task-compose --deploy-web-install

web-dev: ## UI dev server on the task WEB_PORT (starts the local stack; proxies /tmf-api and /internal)
	./scripts/task-compose run --rm --service-ports web sh -c '$(WEB_DEPS) && npm run dev'

# The bss-build marker in index.html: deploy.sh passes the commit (WEB_BUILD=<tag>), the web
# smoke test reads it back through Caddy.
WEB_BUILD ?= dev

web-build: ## Build the UI to frontend/dist (WEB_BUILD=<tag> sets its bss-build marker)
	./scripts/task-compose run --rm --no-deps -e VITE_BSS_BUILD=$(WEB_BUILD) web-offline sh -c '$(WEB_DEPS) && npm run build'

web-lint: ## UI type check, eslint and prettier --check
	$(WEB) sh -c '$(WEB_DEPS) && npm run lint'

web-test: ## UI tests; second timezone in CI or when frontend/src changed
	python3 -B scripts/task_quality.py web-test

.PHONY: web-test-once web-test-timezone
web-test-once:
	$(WEB) sh -c '$(WEB_DEPS) && npm run test:local'

web-test-timezone:
	$(WEB) sh -c '$(WEB_DEPS) && npm run test:timezone'

web-types: ## Regenerate the UI's TypeScript types from spec/
	$(WEB) sh -c '$(WEB_DEPS) && npm run types'

web-fmt: ## Format the UI sources (prettier --write)
	$(WEB) sh -c '$(WEB_DEPS) && npm run format'

# Disposable local sign-in users for the browser tests (tests/e2e_users.py): credentials in
# the gitignored frontend/.e2e/, which the e2e service sees as /web/.e2e. A cleanup first
# clears what an interrupted run left; the one after the run disables the users and removes
# the file whether the tests passed or not.
E2E_USERS = ./scripts/task-compose run --rm -v $(CURDIR)/frontend/.e2e:/e2e app python -m tests.e2e_users

web-e2e: ## e2e smoke (Playwright) on the built UI and the seeded local stack; billing data read-only
	@$(MAKE) --no-print-directory seed
	@$(MAKE) --no-print-directory web-build
	mkdir -p frontend/.e2e
	$(E2E_USERS) cleanup
	$(E2E_USERS) prepare --numbered-offering
	./scripts/task-compose up -d e2e-worker
	./scripts/task-compose run --rm e2e; rc=$$?; $(E2E_USERS) cleanup || rc=1; ./scripts/task-compose rm -sf web-preview e2e-worker >/dev/null 2>&1; exit $$rc

web-shots: ## Capture Ledger UI reference PNGs at 1280 and 1600 px
	rm -rf frontend/.shots
	mkdir -p frontend/.shots
	@$(MAKE) --no-print-directory seed
	@$(MAKE) --no-print-directory web-build
	mkdir -p frontend/.e2e
	$(E2E_USERS) cleanup
	$(E2E_USERS) prepare
	./scripts/task-compose run --rm e2e npx playwright test -c playwright.shots.config.ts; rc=$$?; $(E2E_USERS) cleanup || rc=1; ./scripts/task-compose rm -sf web-preview >/dev/null 2>&1; exit $$rc

spec-verify-upstream: ## Re-fetch the spec blob SHAs from GitHub and compare with spec/README.md (network; not in make test)
	python3 scripts/spec-verify-upstream.py

smoke: ## Smoke-test the local instance
	./scripts/smoke.sh http://$$(./scripts/task-compose port app 8080)

deploy: ## Build locally and ship to the VPS (a failure after the new release starts rolls back)
	./scripts/deploy.sh

rollback: ## Roll the VPS back to the previous release (DRY_RUN=1 shows the plan)
	DRY_RUN=$(DRY_RUN) ./scripts/deploy.sh --rollback

# On demand only, when a change needs stronger testing; the pre-commit hook is the
# regular gate. Target-specific: also applies to the `test` prerequisite.
check: ## Technical readiness in a validated task (full existing local checks)
	python3 -B scripts/task_quality.py check

.PHONY: format review-task show-task check-local
format: ## Format backend and frontend in a validated task
	python3 -B scripts/task_quality.py format

review-task: ## Non-destructive review of task identity and local Git safety
	python3 -B scripts/task_quality.py review-task

show-task: ## Show this task's issue (title, body, labels, comments) as JSON
	python3 -B scripts/task_quality.py show-task

check-local: check

.PHONY: check-preflight guard-check
check-preflight: migrate
	./scripts/task-compose run --rm oneoff pytest -q tests/test_migrations.py::test_alembic_upgrade_head_on_clean_database tests/test_migrations.py::test_cloned_test_databases_are_at_the_current_head tests/test_constraints.py tests/test_worker_schema.py

guard-check:
	.claude/hooks/test-guard.sh

# The commit was tested by the pre-commit hook: deploying does not test again.
deployed: ## Show the last deploy recorded by scripts/deploy.sh and the commits not deployed yet
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

verify: deploy ## Deploy and confirm the remote instance is healthy (health, smoke, UI, MCP)
	@echo "verified: the remote instance is healthy"

tunnel: ## Forward the remote app to localhost:18081 (Ctrl-C to stop)
	ssh $(SSH_OPTS) -N -L 18081:127.0.0.1:18081 $(REMOTE)

ssh: ## Shell on the VPS
	ssh $(SSH_OPTS) $(REMOTE)

# the service `make remote` runs in; SERVICE=mcp for the token tooling (its volume)
SERVICE := app
remote: ## Run a one-off command against the deployed image on the VPS: make remote CMD="alembic current" [SERVICE=mcp]
	@if [ -z "$(CMD)" ]; then \
		echo 'usage: make remote CMD="alembic current"' >&2; \
		exit 1; \
	fi
	ssh $(SSH_OPTS) $(REMOTE) "cd $(REMOTE_DIR) && IMAGE_NAME=$(IMAGE_NAME) TAG=$(TAG) $(COMPOSE_PROD) run --rm --no-deps $(SERVICE) $(CMD)"

progress: ## Regenerate docs/PROGRESS.md + docs/progress/status.json (scope.yaml vs the code)
	@mkdir -p .progress
	./scripts/task-compose run --rm -T app python -m tests.progress_collect < docs/progress/scope.yaml > .progress/backend.json
	./scripts/task-compose run --rm --no-deps -T web-offline sh -c '$(WEB_DEPS) && node scripts/ui-markers.mjs' > .progress/ui.json
	./scripts/task-compose run --rm --no-deps -T web-offline sh -c '$(WEB_DEPS) && npm run types >&2 && UI_TESTED_OUT=/tmp/ui-tested.json npx vitest run >&2 && cat /tmp/ui-tested.json' > .progress/ui-tested.json
	python3 scripts/progress/generate.py

docs: ## Regenerate docs/api/ and docs/db/ (app.openapi() vs spec/, pg_catalog at migration head)
	@mkdir -p .docs
	./scripts/task-compose run --rm -T app python -m tests.docs_collect > .docs/collected.json
	python3 scripts/docs/generate.py

docs-check: docs ## Fail if the committed docs/api/ or docs/db/ differ from a fresh `make docs`
	@if ! git diff --quiet -- docs/api docs/db || [ -n "$$(git ls-files --others --exclude-standard -- docs/api docs/db)" ]; then \
		git status --short -- docs/api docs/db; \
		echo "docs-check: docs/api or docs/db is out of date - commit the output of make docs" >&2; \
		exit 1; \
	fi
	@echo "docs-check: docs/api and docs/db match the code"

docs-links: ## Check Markdown links, anchors and the make targets and scripts the agent docs name (no Docker)
	python3 -B scripts/docs_links.py

runtime-check: ## The harness script tests (scripts/test_*.py, run in parallel, ~1.5 min), no Docker or secrets; the commit hook runs them when scripts/, Makefile or .claude/ change
	python3 -B scripts/run_suites.py \
		scripts/test_workflow_lifecycle.py \
		scripts/test_task_finish.py \
		scripts/test_task_pr.py \
		scripts/test_task_git.py \
		scripts/test_task_quality.py \
		scripts/test_task_runtime.py \
		scripts/test_diagnostics.py \
		scripts/test_task_cleanup.py \
		scripts/test_start_task.py \
		scripts/test_task_telemetry.py \
		scripts/test_task_usage.py \
		scripts/test_task_collect.py \
		scripts/test_agent_telemetry_hook.py \
		scripts/test_task_facts.py \
		scripts/test_task_summary.py \
		scripts/test_script_quality.py \
		scripts/test_docs_links.py \
		scripts/test_compose_prod.py \
		scripts/test_deploy_rollback.py \
		scripts/test_mcp_oauth_smoke.py \
		scripts/test_audit_gate.py

.PHONY: telemetry-collect telemetry-probe telemetry-watch
telemetry-watch: export BSS_COLLECT_TASK = $(TASK)
telemetry-watch: ## Retry final usage after an ended session (optional TASK=; bounded to two minutes)
	python3 -B scripts/task_collect.py --watch

telemetry-probe: export BSS_COLLECT_CLIENT = $(CLIENT)
telemetry-probe: export BSS_COLLECT_SESSION = $(SESSION_ID)
telemetry-probe: ## Verify a real client source without attributing historical usage to an issue
	python3 -B scripts/task_collect.py --probe

telemetry-collect: export BSS_COLLECT_TASK = $(TASK)
telemetry-collect: export BSS_COLLECT_CLIENT = $(CLIENT)
telemetry-collect: export BSS_COLLECT_SESSION = $(SESSION_ID)
telemetry-collect: export BSS_COLLECT_CLOSE = $(CLOSE)
telemetry-collect: export BSS_COLLECT_RESET = $(RESET)
telemetry-collect: ## Collect bound machine usage (optional TASK= CLIENT= SESSION_ID= CLOSE=1; RESET=1 re-attributes TASK from sources)
	python3 -B scripts/task_collect.py

.PHONY: telemetry-report telemetry-summary telemetry-facts
telemetry-report: ## Report task usage, priced subtotals and observed lifecycle metrics as JSON
	python3 -B scripts/task_usage.py report

telemetry-summary: ## One row per issue (tokens, cost, active/wall time, prompts, friction, PR size) and rollups (JSON=1 for JSON)
	python3 -B scripts/task_summary.py $(if $(filter 1,$(JSON)),--json,)

telemetry-facts: export BSS_FACTS_TASK = $(TASK)
telemetry-facts: ## Capture or refresh GitHub facts (PR size, labels, epic, follow-ups) for merged tasks (optional TASK=)
	python3 -B scripts/task_facts.py $(if $(TASK),--task "$$BSS_FACTS_TASK",)

.PHONY: telemetry-up telemetry-down telemetry-migrate telemetry-health telemetry-test telemetry-prices
telemetry-test: ## Test the running telemetry schema (all test data rolled back)
	python3 -B scripts/test_telemetry_database.py

telemetry-up: ## Start shared persistent telemetry storage and apply its migrations
	python3 -B scripts/task_telemetry.py up

telemetry-down: ## Stop shared telemetry storage, preserving its volume
	python3 -B scripts/task_telemetry.py down

telemetry-migrate: ## Apply versioned telemetry schema migrations
	python3 -B scripts/task_telemetry.py migrate

telemetry-prices: ## Load the hand-maintained model list prices (scripts/telemetry/prices.json)
	python3 -B scripts/task_telemetry.py prices

telemetry-health: ## Check shared telemetry database and schema
	python3 -B scripts/task_telemetry.py health

hooks: ## Install the pre-commit (lint + test) and pre-push (secrets) hooks
	python3 scripts/install-hooks.py


clean: ## Remove local containers AND the local database volume
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
commit: ## Commit explicit task files (FILES JSON array or FILES_FILE=/abs/path; MESSAGE or MESSAGE_FILE=/abs/path)
	@python3 -B scripts/task_git.py commit

push: ## Publish only the current validated task branch to origin
	@python3 -B scripts/task_git.py push

.PHONY: sync-task
sync-task: ## Merge current origin/master into a clean task, preserving history and hooks
	@python3 -B scripts/task_git.py sync-task

.PHONY: create-pr check-pr
create-pr: export PR_TITLE := $(PR_TITLE)
create-pr: export PR_BODY_FILE := $(PR_BODY_FILE)
create-pr: ## Publish the task branch and create or confirm its PR
	@python3 -B scripts/task_pr.py create-pr

check-pr: export HUMAN_APPROVAL := $(HUMAN_APPROVAL)
check-pr: ## Report task PR readiness, including human review gates (never merges)
	@python3 -B scripts/task_pr.py check-pr

.PHONY: clean-tasks
clean-tasks: export CONFIRM := $(CONFIRM)
clean-tasks: ## List leftovers (volumes, images, branches, credentials) of finished tasks; CONFIRM=1 removes them. Primary only
	@python3 -B scripts/task_cleanup.py

.PHONY: merge-task finish-task
merge-task: export HUMAN_APPROVAL := $(HUMAN_APPROVAL)
merge-task: ## Merge this task PR only when all repository and human gates permit
	@python3 -B scripts/task_finish.py merge-task

finish-task: export BSS_FINISH_MAKEFILE := $(MAKEFILE_LIST)
finish-task: ## Safely finish a completed slot task, from primary master (SLOT=worker1)
	@python3 -B -c 'import os, pathlib, runpy, sys; script = pathlib.Path(os.environ["BSS_FINISH_MAKEFILE"]).resolve().parent / "scripts/task_finish.py"; sys.path.insert(0, str(script.parent)); sys.argv = [str(script), "finish-task"]; runpy.run_path(str(script), run_name="__main__")'
