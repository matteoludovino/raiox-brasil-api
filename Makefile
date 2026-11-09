.PHONY: install dev lint fmt typecheck test check up down logs

install:     ## Install dependencies (including dev)
	uv sync

dev:         ## Run the API locally with reload (needs Redis on localhost:6379)
	uv run uvicorn raiox.main:create_app --factory --reload

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

fmt:
	uv run ruff format src tests
	uv run ruff check --fix src tests

typecheck:
	uv run mypy src

test:
	uv run pytest --cov --cov-report=term

check: lint typecheck test  ## Everything CI runs

up:          ## Start API + Redis with Docker
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api
