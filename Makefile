# Agent Factory developer commands. Requires uv (https://docs.astral.sh/uv/) and Node.js 20+ for the web UI.
.DEFAULT_GOAL := help
UV ?= uv
PY := $(UV) run
WEB := web

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install Python (incl. dev) and web dependencies from lockfiles
	$(UV) sync --frozen --all-extras
	cd $(WEB) && npm ci --no-audit --no-fund

init: ## Migrate the database, seed templates and install + evaluate the example agents
	$(PY) agent-factory init

dev: web ## Run the API + dashboard on http://127.0.0.1:8787 (local profile)
	$(PY) agent-factory serve

dev-web: ## Run the Vite dev server (hot reload) on :5173 proxying the API on :8787
	cd $(WEB) && npm run dev

web: ## Build the web dashboard into web/dist
	cd $(WEB) && npm run build

doctor: ## Preflight checks for this machine
	$(PY) agent-factory doctor

test: ## Run backend and frontend tests (deterministic mock model)
	$(PY) pytest -q
	cd $(WEB) && npm test

test-backend: ## Backend tests only
	$(PY) pytest -q

coverage: ## Backend tests with coverage report
	$(PY) pytest -q --cov=agent_factory --cov-report=term-missing

lint: ## Ruff lint + format check, ESLint
	$(PY) ruff check src tests migrations
	$(PY) ruff format --check src
	cd $(WEB) && npm run lint -- --max-warnings 0

format: ## Apply ruff formatting and autofixes
	$(PY) ruff format src
	$(PY) ruff check src tests migrations --fix

typecheck: ## mypy (backend) and tsc (frontend)
	$(PY) mypy
	cd $(WEB) && npm run typecheck

audit: ## Dependency vulnerability scan (Python + npm) and SBOM-style inventory
	$(PY) pip-audit --progress-spinner off
	cd $(WEB) && npm audit --omit=dev
	./scripts/dependency-inventory.sh

build: web ## Build the Python wheel/sdist and the production container image
	$(UV) build
	docker build -f deploy/docker/Dockerfile -t agent-factory:local .

compose-up: ## Start the single-node production stack (needs deploy/compose/production.env)
	docker compose --env-file deploy/compose/production.env up -d --build

compose-down: ## Stop the production stack
	docker compose --env-file deploy/compose/production.env down

backup: ## Back up the local SQLite database and artifacts
	$(PY) agent-factory db backup

clean: ## Remove build outputs and caches (keeps data/)
	rm -rf dist build $(WEB)/dist .pytest_cache .mypy_cache .ruff_cache htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

.PHONY: help install init dev dev-web web doctor test test-backend coverage lint format typecheck audit build compose-up compose-down backup clean
