.PHONY: help data test lint db-up db-down db-reset db-apply psql-agent

PY ?= python3

help:
	@echo "make data       — перегенерировать демо-выгрузку «из 1С» (детерминированно)"
	@echo "make test       — тесты"
	@echo "make lint       — ruff"
	@echo "make db-up      — поднять Postgres + pgvector и загрузить данные (docker compose)"
	@echo "make db-reset   — пересоздать базу с нуля"
	@echo "make db-apply   — загрузить схемы и данные во внешнюю базу (DATABASE_URL, AGENT_DB_PASSWORD)"
	@echo "make psql-agent — консоль под ролью агента (только analytics, только чтение)"

data:
	cd backend && PYTHONPATH=src $(PY) -m dala_agent.demo_data.generator --out ../data/demo_1c

test:
	cd backend && $(PY) -m pytest -q

lint:
	cd backend && ruff check . && ruff format --check .

db-up:
	docker compose up -d --wait db

db-down:
	docker compose down

db-reset:
	docker compose down -v && docker compose up -d --wait db

db-apply:
	@set -a; . ./.env; set +a; ./db/apply.sh

psql-agent:
	@set -a; . ./.env; set +a; \
	PGPASSWORD=$$AGENT_DB_PASSWORD psql -h 127.0.0.1 -U agent_reader -d $${POSTGRES_DB:-dala}
