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

.PHONY: help up down restart logs ps shell test lint fmt smoke deploy check verify tunnel ssh clean migrate worker-once remote hooks spec-verify-upstream seed web-install web-dev web-build web-lint web-test web-types web-fmt web-e2e docs docs-check

help: ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

up: ## Start the local stack (app + postgres, hot reload)
	docker compose up -d --build
	@$(MAKE) --no-print-directory wait-local

down: ## Stop the local stack (volumes are kept)
	docker compose down

restart: down up ## Restart the local stack

logs: ## Last 5 minutes of app logs
	docker compose logs --since=5m --tail=200 app

ps: ## Container status, including health
	docker compose ps

shell: ## Shell inside the running app container
	docker compose exec app /bin/bash

psql: ## psql in the local database, through compose exec (the db port is not published)
	docker compose exec db sh -c 'exec psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

test: ## Run the test suite in the container (parallel; HYPOTHESIS_PROFILE=dev|ci), plus the scripts' own cases
	./scripts/test-billing-status-check.sh
	docker compose run --rm -e HYPOTHESIS_PROFILE=$(HYPOTHESIS_PROFILE) app pytest -n auto

seed: ## LOCAL demo data through the public TMF API (tests/seed.py); refuses APP_ENV=prod; a second run is a no-op
	docker compose run --rm app python -m tests.seed

worker-once: ## One wake-up of the billing worker against the local stack (due dates, queue, flips), then exit
	docker compose run --rm app python -m app.worker --once

migrate: ## Apply Alembic migrations against the local stack
	docker compose run --rm app alembic upgrade head

lint: ## Lint and format check
	docker compose run --rm --no-deps app ruff check .
	docker compose run --rm --no-deps app ruff format --check .

fmt: ## Auto-format the code (writable `tools` service; `app` mounts sources read-only)
	docker compose run --rm tools sh -c 'ruff format . && ruff check --fix .'

# --- admin UI (frontend/): Node runs only in the `web` container, never on the host ---
# Only web-install downloads (npm ci, `web` service). Every other target runs in the
# offline `web-offline` service and first only CHECKS that node_modules matches
# package-lock.json (frontend/scripts/check-deps.sh), failing with "run make web-install"
# - never an implicit install. Lint, test and build regenerate the types from spec/.
WEB := docker compose run --rm --no-deps web-offline
WEB_DEPS := sh scripts/check-deps.sh

web-install: ## Install the UI's npm dependencies exactly as locked (npm ci; the only step with network)
	docker compose run --rm --no-deps web sh scripts/install-deps.sh

web-dev: ## UI dev server on http://localhost:5173 (starts the local stack; proxies /tmf-api and /internal to it)
	docker compose run --rm --service-ports web sh -c '$(WEB_DEPS) && npm run dev'

# The bss-build marker in index.html: deploy.sh passes the commit (WEB_BUILD=<tag>), the web
# smoke test reads it back through Caddy.
WEB_BUILD ?= dev

web-build: ## Build the UI to frontend/dist (WEB_BUILD=<tag> sets its bss-build marker)
	docker compose run --rm --no-deps -e VITE_BSS_BUILD=$(WEB_BUILD) web-offline sh -c '$(WEB_DEPS) && npm run build'

web-lint: ## UI type check, eslint and prettier --check
	$(WEB) sh -c '$(WEB_DEPS) && npm run lint'

web-test: ## UI unit and component tests (vitest)
	$(WEB) sh -c '$(WEB_DEPS) && npm test'

web-types: ## Regenerate the UI's TypeScript types from spec/
	$(WEB) sh -c '$(WEB_DEPS) && npm run types'

web-fmt: ## Format the UI sources (prettier --write)
	$(WEB) sh -c '$(WEB_DEPS) && npm run format'

web-e2e: ## e2e smoke (Playwright) on the built UI and the seeded local stack; read-only
	@$(MAKE) --no-print-directory seed
	@$(MAKE) --no-print-directory web-build
	docker compose run --rm e2e; rc=$$?; docker compose rm -sf web-preview >/dev/null 2>&1; exit $$rc

spec-verify-upstream: ## Re-fetch the spec blob SHAs from GitHub and compare with spec/README.md (network; not in make test)
	python3 scripts/spec-verify-upstream.py

smoke: ## Smoke-test the local instance
	./scripts/smoke.sh http://localhost:8080

deploy: ## Build locally and ship to the VPS
	./scripts/deploy.sh

# Target-specific: also applies to the `test` prerequisite (through check, for verify).
check: HYPOTHESIS_PROFILE = ci
check: lint test docs-check web-lint web-test web-e2e ## Every local check of make verify, without the deploy
	@echo "checked: lint, test, docs-check, web-lint, web-test and web-e2e passed"

verify: HYPOTHESIS_PROFILE = ci
verify: check deploy ## Full gate: make check, then a real deployment that must go healthy
	@echo "verified: local checks passed and the remote instance is healthy"

tunnel: ## Forward the remote app to localhost:18081 (Ctrl-C to stop)
	ssh $(SSH_OPTS) -N -L 18081:127.0.0.1:18081 $(REMOTE)

ssh: ## Shell on the VPS
	ssh $(SSH_OPTS) $(REMOTE)

remote: ## Run a one-off command against the deployed image on the VPS: make remote CMD="alembic current"
	@if [ -z "$(CMD)" ]; then \
		echo 'usage: make remote CMD="alembic current"' >&2; \
		exit 1; \
	fi
	ssh $(SSH_OPTS) $(REMOTE) "cd $(REMOTE_DIR) && IMAGE_NAME=$(IMAGE_NAME) TAG=$(TAG) $(COMPOSE_PROD) run --rm --no-deps app $(CMD)"

progress: ## Regenerate docs/PROGRESS.md + docs/progress/status.json (scope.yaml vs the code)
	@mkdir -p .progress
	docker compose run --rm -T app python -m tests.progress_collect < docs/progress/scope.yaml > .progress/backend.json
	docker compose run --rm --no-deps -T web-offline sh -c '$(WEB_DEPS) && node scripts/ui-markers.mjs' > .progress/ui.json
	python3 scripts/progress/generate.py

docs: ## Regenerate docs/api/ and docs/db/ (app.openapi() vs spec/, pg_catalog at migration head)
	@mkdir -p .docs
	docker compose run --rm -T app python -m tests.docs_collect > .docs/collected.json
	python3 scripts/docs/generate.py

docs-check: docs ## Fail if the committed docs/api/ or docs/db/ differ from a fresh `make docs`
	@if ! git diff --quiet -- docs/api docs/db || [ -n "$$(git ls-files --others --exclude-standard -- docs/api docs/db)" ]; then \
		git status --short -- docs/api docs/db; \
		echo "docs-check: docs/api or docs/db is out of date - commit the output of make docs" >&2; \
		exit 1; \
	fi
	@echo "docs-check: docs/api and docs/db match the code"

hooks: ## Install the pre-commit (lint + test) and pre-push (secrets) hooks
	@mkdir -p .git/hooks
	@ln -sf ../../scripts/pre-commit.sh .git/hooks/pre-commit
	@ln -sf ../../scripts/pre-push.sh .git/hooks/pre-push
	@chmod +x scripts/pre-commit.sh scripts/pre-push.sh
	@echo "installed pre-commit hook -> scripts/pre-commit.sh (bypass with git commit --no-verify)"
	@echo "installed pre-push hook -> scripts/pre-push.sh (never bypass it)"


clean: ## Remove local containers AND the local database volume
	docker compose down -v

wait-local:
	@echo "waiting for app to become healthy..."; \
	for i in $$(seq 1 30); do \
	  cid=$$(docker compose ps -q app); \
	  st=$$(docker inspect -f '{{.State.Health.Status}}' $$cid 2>/dev/null || echo starting); \
	  if [ "$$st" = "healthy" ]; then echo "app is healthy on http://localhost:8080"; exit 0; fi; \
	  if [ "$$st" = "unhealthy" ]; then docker compose logs --tail=50 app; exit 1; fi; \
	  sleep 2; \
	done; \
	docker compose logs --tail=50 app; echo "app did not become healthy in time"; exit 1
