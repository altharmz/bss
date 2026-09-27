## The only supported command interface. Claude Code is told to use these targets and
## nothing else, so every action has a stable name and an honest exit code.

SHELL := /bin/bash
COMPOSE_PROD := docker compose -f compose.yaml -f compose.prod.yaml
REMOTE ?= vps-test
REMOTE_DIR ?= lab
IMAGE_NAME ?= vps-lab-app
TAG ?= $(shell git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)

.PHONY: help up down restart logs ps shell test lint fmt smoke deploy verify tunnel ssh clean migrate remote backup restore hooks cron-install backup-list

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

test: ## Run the test suite in the container
	docker compose run --rm app pytest

migrate: ## Apply Alembic migrations against the local stack
	docker compose run --rm app alembic upgrade head

lint: ## Lint and format check
	docker compose run --rm --no-deps app ruff check .
	docker compose run --rm --no-deps app ruff format --check .

fmt: ## Auto-format the code
	docker compose run --rm --no-deps app ruff format .
	docker compose run --rm --no-deps app ruff check --fix .

smoke: ## Smoke-test the local instance
	./scripts/smoke.sh http://localhost:8080

deploy: ## Build locally and ship to the VPS
	./scripts/deploy.sh

verify: lint test deploy ## Full gate: lint, tests, then a real deployment that must go healthy
	@echo "verified: local checks passed and the remote instance is healthy"

tunnel: ## Forward the remote app to localhost:18080 (Ctrl-C to stop)
	ssh -N -L 18080:127.0.0.1:18080 $(REMOTE)

ssh: ## Shell on the VPS
	ssh $(REMOTE)

remote: ## Run a one-off command against the deployed image on the VPS: make remote CMD="alembic current"
	@if [ -z "$(CMD)" ]; then \
		echo 'usage: make remote CMD="alembic current"' >&2; \
		exit 1; \
	fi
	ssh $(REMOTE) "cd $(REMOTE_DIR) && IMAGE_NAME=$(IMAGE_NAME) TAG=$(TAG) $(COMPOSE_PROD) run --rm --no-deps app $(CMD)"

backup: ## Dump the remote database into backups/
	./scripts/backup.sh

backup-list: ## List backup dumps sitting on the VPS, with dates and sizes
	./scripts/backup-list.sh

restore: ## Restore a dump onto the VPS: make restore FILE=backups/vps-lab-<ts>.sql.gz
	FILE="$(FILE)" ./scripts/restore.sh

hooks: ## Install the pre-commit hook (make lint + make test before every commit)
	@mkdir -p .git/hooks
	@ln -sf ../../scripts/pre-commit.sh .git/hooks/pre-commit
	@chmod +x scripts/pre-commit.sh
	@echo "installed pre-commit hook -> scripts/pre-commit.sh (bypass with git commit --no-verify)"

cron-install: ## Install/refresh the daily 02:30 backup cron job on the VPS (idempotent)
	./scripts/cron-install.sh

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
