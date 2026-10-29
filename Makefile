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

.PHONY: help up down restart logs ps shell test test-smoke test-changed lint fmt smoke deploy check verify tunnel ssh clean migrate worker-once remote hooks spec-verify-upstream seed web-install web-dev web-build web-lint web-test web-types web-fmt web-e2e web-shots docs docs-check mcp-smoke mcp-token-create mcp-tokens mcp-token-revoke

help: ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

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

mcp-smoke: ## Discovery/401/carve-out checks and prepaid operations against this task's MCP
	./scripts/mcp-smoke.sh $$(./scripts/task-compose --url mcp) local
	@# the prepaid part needs a write credential: a throwaway local token, revoked afterwards
	@set -e; out="$$(mktemp)"; err="$$(mktemp)"; trap 'rm -f "$$out" "$$err"' EXIT; \
	./scripts/task-compose run --rm -T --no-deps mcp python -m bss_mcp.tokens create "smoke-$$$$" --scope write >"$$out" 2>"$$err"; \
	id="$$(sed -n 's/^id \([^ ]*\) .*/\1/p' "$$err" | head -1)"; \
	[ -n "$$id" ] || { echo 'mcp-smoke: could not create the local smoke token' >&2; exit 1; }; \
	status=0; MCP_SMOKE_TOKEN="$$(head -1 "$$out")" python3 scripts/mcp-prepaid-smoke.py $$(./scripts/task-compose --url mcp) || status=$$?; \
	./scripts/task-compose run --rm -T --no-deps mcp python -m bss_mcp.tokens revoke "$$id" >/dev/null 2>&1; \
	exit $$status

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

test: runtime-check ## The FULL suite in the container (parallel; HYPOTHESIS_PROFILE=dev|ci), plus the scripts' own cases: nightly in CI, by hand before a PR
	./scripts/test-billing-status-check.sh
	./scripts/test-pre-commit-lock.sh
	@mkdir -p .testmon
	./scripts/task-compose run --rm -e HYPOTHESIS_PROFILE=$(HYPOTHESIS_PROFILE) oneoff pytest -n auto

test-smoke: runtime-check ## The smoke subset (pytest -m smoke) and the scripts' own cases: what CI runs on pull requests and master
	./scripts/test-billing-status-check.sh
	./scripts/test-pre-commit-lock.sh
	@mkdir -p .testmon
	./scripts/task-compose run --rm -e HYPOTHESIS_PROFILE=$(HYPOTHESIS_PROFILE) oneoff pytest -n auto -m smoke

# pytest-testmon runs the tests whose code changed since their last recorded run, plus new and
# previously failed ones (selection is off under -m or -k, so this never filters). The first
# run on a fresh checkout is the whole suite; it records into .testmon/.testmondata.
test-changed: ## Only the tests affected by the change since their last run (pytest-testmon); the pre-commit hook runs it after test-smoke
	@mkdir -p .testmon
	./scripts/task-compose run --rm -e HYPOTHESIS_PROFILE=$(HYPOTHESIS_PROFILE) oneoff pytest -n auto --testmon

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

# --- admin UI (frontend/): Node runs only in the `web` container, never on the host ---
# Only web-install downloads (npm ci, `web` service). Every other target runs in the
# offline `web-offline` service and first only CHECKS that node_modules matches
# package-lock.json (frontend/scripts/check-deps.sh), failing with "run make web-install"
# - never an implicit install. Lint, test and build regenerate the types from spec/.
WEB := ./scripts/task-compose run --rm --no-deps web-offline
WEB_DEPS := sh scripts/check-deps.sh

web-install: ## Install the UI's npm dependencies exactly as locked (npm ci; the only step with network)
	./scripts/task-compose run --rm --no-deps web sh scripts/install-deps.sh

web-dev: ## UI dev server on the task WEB_PORT (starts the local stack; proxies /tmf-api and /internal)
	./scripts/task-compose run --rm --service-ports web sh -c '$(WEB_DEPS) && npm run dev'

# The bss-build marker in index.html: deploy.sh passes the commit (WEB_BUILD=<tag>), the web
# smoke test reads it back through Caddy.
WEB_BUILD ?= dev

web-build: ## Build the UI to frontend/dist (WEB_BUILD=<tag> sets its bss-build marker)
	./scripts/task-compose run --rm --no-deps -e VITE_BSS_BUILD=$(WEB_BUILD) web-offline sh -c '$(WEB_DEPS) && npm run build'

web-lint: ## UI type check, eslint and prettier --check
	$(WEB) sh -c '$(WEB_DEPS) && npm run lint'

web-test: ## UI unit and component tests (vitest)
	$(WEB) sh -c '$(WEB_DEPS) && npm test'

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
	$(E2E_USERS) prepare
	./scripts/task-compose run --rm e2e; rc=$$?; $(E2E_USERS) cleanup || rc=1; ./scripts/task-compose rm -sf web-preview >/dev/null 2>&1; exit $$rc

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

deploy: ## Build locally and ship to the VPS
	./scripts/deploy.sh

# On demand only, when a change needs stronger testing; the pre-commit hook is the
# regular gate. Target-specific: also applies to the `test` prerequisite.
check: HYPOTHESIS_PROFILE = ci
check: lint test docs-check web-lint web-test web-e2e ## Every local check (ci profile, docs, UI, browser), on demand
	@echo "checked: lint, test, docs-check, web-lint, web-test and web-e2e passed"

# The commit was tested by the pre-commit hook: deploying does not test again.
deployed: ## Show the last deploy recorded by scripts/deploy.sh and the commits not deployed yet
	@if [ ! -f .deployed ]; then \
		echo "no deploy recorded on this workstation (.deployed is written by make deploy)"; \
	else \
		cat .deployed; \
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

runtime-check: ## Validate task runtime helpers without Docker or secrets
	python3 -B scripts/test_task_runtime.py

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
