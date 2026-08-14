# Stept development commands. `make help` lists targets.
SHELL := /bin/bash
COMPOSE := $(shell command -v docker-compose >/dev/null 2>&1 && echo docker-compose || echo docker compose)

.PHONY: help setup dev dev-backend dev-frontend widget-dist services services-down seed types \
        verify test test-backend test-frontend lint format i18n-check e2e e2e-ui build clean \
        db-upgrade db-revision

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: ## Install all dependencies (backend uv env + pnpm workspaces)
	cd backend && uv sync --all-extras
	pnpm install

services: ## Start Postgres/Redis/Mailpit (docker)
	$(COMPOSE) up -d

services-down: ## Stop docker services
	$(COMPOSE) down

# widget/dist is gitignored and the backend only mounts /widget-assets when it
# exists (backend/app/main.py), so on a fresh clone `make dev` would serve an
# embed snippet whose loader 404s. Order-only prereq: build once when missing,
# never force a rebuild when present (`make build` owns freshness).
widget-dist: ## Build the embeddable widget if widget/dist is missing
	@test -f widget/dist/loader.js || pnpm --filter @stept/widget build

dev-backend: | widget-dist ## Run API on :8600 (reload)
	cd backend && uv run uvicorn app.main:app --port 8600 --reload

dev-frontend: ## Run dashboard on :5273
	pnpm --filter @stept/frontend dev

dev: | widget-dist ## Run backend + frontend together
	@$(MAKE) -j2 dev-backend dev-frontend

seed: ## Seed demo workspace/data (idempotent)
	cd backend && uv run python -m app.seed

types: ## Export OpenAPI schema and regenerate frontend API types
	cd backend && uv run python -m app.export_openapi ../openapi.json
	pnpm --filter @stept/frontend exec openapi-typescript ../openapi.json -o src/api/schema.d.ts
	pnpm --filter @stept/frontend exec prettier --write src/api/schema.d.ts

# ---------- Verification ----------

lint: i18n-check ## Static checks only
	cd backend && uv run ruff check app tests && uv run ruff format --check app tests && uv run mypy app
	pnpm -r --no-bail exec tsc --noEmit

i18n-check: ## Catalog integrity: missing keys, plural forms, stray placeholders
	node scripts/check-i18n.mjs

test-backend: ## Backend tests (SQLite by default; pg tests auto-skip if unreachable)
	cd backend && uv run pytest -q

test-frontend: ## Frontend + widget + extension + dom-capture + SDK unit tests
	pnpm --filter @stept/frontend test -- --run
	pnpm --filter @stept/widget test -- --run
	pnpm --filter @stept/extension test -- --run
	pnpm --filter @stept/dom-capture test -- --run
	pnpm --filter @stept/js test -- --run
	pnpm --filter @stept/react test -- --run

build: ## Production builds (frontend, widget, extension + its downloadable zip)
	pnpm -r build
	pnpm --filter @stept/extension zip

e2e: ## Playwright end-to-end suite (starts its own servers)
	pnpm --filter @stept/e2e test

e2e-ui: ## Playwright UI mode
	pnpm --filter @stept/e2e test:ui

verify: lint test-backend test-frontend build ## Everything except e2e
	@echo "✅ verify passed"

# ---------- Database (Postgres mode) ----------

db-upgrade: ## Apply alembic migrations
	cd backend && uv run alembic upgrade head

db-revision: ## Autogenerate a migration: make db-revision m="message"
	cd backend && uv run alembic revision --autogenerate -m "$(m)"

clean: ## Remove caches/builds
	rm -rf backend/.venv backend/.pytest_cache backend/.ruff_cache backend/.mypy_cache
	rm -rf node_modules frontend/node_modules widget/node_modules extension/node_modules e2e/node_modules
	rm -rf frontend/dist widget/dist extension/dist
