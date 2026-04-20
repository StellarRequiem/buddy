# buddy — common development commands
# Usage: make <target>

.PHONY: install dev test coverage lint format clean docker-up docker-down help

# ── Setup ──────────────────────────────────────────────────────────────────────
install:          ## Install all dependencies
	uv sync --extra dev

# ── Run ────────────────────────────────────────────────────────────────────────
dev:              ## Start the server in development mode (auto-reload)
	uv run uvicorn buddy.main:app --reload --host 127.0.0.1 --port 7437 --log-level info

run:              ## Start the server in production mode
	uv run python -m buddy.main

# ── Test ───────────────────────────────────────────────────────────────────────
test:             ## Run the full test suite
	uv run pytest -v --tb=short

test-fast:        ## Run tests without integration tests (no app boot, <1s)
	uv run pytest -v --tb=short --ignore=tests/test_integration.py

coverage:         ## Run tests with coverage report (HTML at htmlcov/index.html)
	uv run pytest --cov=buddy --cov-report=term-missing --cov-report=html -q
	@echo "HTML report: htmlcov/index.html"

# ── Quality ────────────────────────────────────────────────────────────────────
lint:             ## Lint with ruff
	uv run ruff check buddy/ tests/

format:           ## Auto-format with ruff
	uv run ruff format buddy/ tests/

typecheck:        ## Type-check with pyright (if installed)
	uv run pyright buddy/ 2>/dev/null || echo "pyright not installed — skipping"

# ── Docker ─────────────────────────────────────────────────────────────────────
docker-up:        ## Start buddy + Ollama via Docker Compose
	docker compose up -d
	@echo "Waiting for buddy to be healthy..."
	@until curl -sf http://localhost:7437/health > /dev/null; do sleep 2; done
	@echo "buddy is up at http://localhost:7437"

docker-down:      ## Stop Docker Compose stack
	docker compose down

docker-models:    ## Pull required Ollama models into the Docker container
	docker compose exec ollama ollama pull qwen2.5:14b
	docker compose exec ollama ollama pull phi4-mini
	docker compose exec ollama ollama pull nomic-embed-text

docker-logs:      ## Tail buddy container logs
	docker compose logs -f buddy

# ── Demo ───────────────────────────────────────────────────────────────────────
demo:             ## Run the interactive demo script
	scripts/demo.sh

# ── Cleanup ────────────────────────────────────────────────────────────────────
clean:            ## Remove build artifacts, caches, coverage reports
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache htmlcov .coverage dist *.egg-info

# ── Help ───────────────────────────────────────────────────────────────────────
help:             ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
