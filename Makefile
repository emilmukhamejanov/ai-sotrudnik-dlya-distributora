.PHONY: help install data test test-db lint db-up db-down db-reset db-apply psql-agent index api evals ui ui-dev

PY ?= python3

help:
	@echo "make install    — установить backend с зависимостями (pip install -e)"
	@echo "make data       — перегенерировать демо-выгрузку «из 1С» (детерминированно)"
	@echo "make test       — тесты (интеграционные с базой — только если задан TEST_DATABASE_URL)"
	@echo "make lint       — ruff"
	@echo "make db-up      — поднять Postgres + pgvector и загрузить данные (docker compose)"
	@echo "make db-reset   — пересоздать базу с нуля"
	@echo "make db-apply   — загрузить схемы и данные во внешнюю базу (DATABASE_URL, AGENT_DB_PASSWORD)"
	@echo "make psql-agent — консоль под ролью агента (только analytics, только чтение)"
	@echo "make index      — проиндексировать документы в kb (эмбеддинги по EMBEDDINGS_PROVIDER)"
	@echo "make api        — запустить API на http://127.0.0.1:8000 (документация — /docs)"
	@echo "make evals      — эталонные вопросы на настоящей модели (тратит токены), отчёт в var/evals"
	@echo "make ui         — собрать дашборд (frontend/dist); его отдаёт make api на http://127.0.0.1:8000/"
	@echo "make ui-dev     — дашборд в режиме разработки на http://localhost:5173 (API — make api)"
	@echo "make test-db    — интеграционные тесты на живой базе (TEST_DATABASE_URL, TEST_AGENT_DATABASE_URL)"

data:
	cd backend && PYTHONPATH=src $(PY) -m dala_agent.demo_data.generator --out ../data/demo_1c

install:
	cd backend && $(PY) -m pip install -e ".[dev]"

test:
	cd backend && $(PY) -m pytest -q

test-db:
	cd backend && $(PY) -m pytest -q -m db

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

index:
	cd backend && PYTHONPATH=src $(PY) -m dala_agent.rag.indexer

api:
	cd backend && PYTHONPATH=src $(PY) -m uvicorn dala_agent.api.main:create_app --factory --reload --port 8000

evals:
	cd backend && PYTHONPATH=src $(PY) -m dala_agent.evals

ui:
	cd frontend && npm ci && npm run build

ui-dev:
	cd frontend && npm install && npm run dev
