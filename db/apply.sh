#!/usr/bin/env bash
# Создаёт схемы, роль агента и загружает демо-данные.
#
#   Внутри контейнера Postgres (docker-entrypoint-initdb.d) — подключение через POSTGRES_USER/POSTGRES_DB.
#   Снаружи (локальный Postgres, Supabase):  DATABASE_URL=postgresql://... AGENT_DB_PASSWORD=... ./db/apply.sh
#
# SKIP_KB=1 — не создавать схему kb (если в базе нет pgvector).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_DIR="${SQL_DIR:-$SCRIPT_DIR/sql}"
DATA_DIR="${DATA_DIR:-$SCRIPT_DIR/../data/demo_1c}"
: "${AGENT_DB_PASSWORD:?Задайте AGENT_DB_PASSWORD (см. .env.example)}"

if [[ -n "${DATABASE_URL:-}" ]]; then
  PSQL=(psql "$DATABASE_URL")
else
  PSQL=(psql --username "${POSTGRES_USER:-postgres}" --dbname "${POSTGRES_DB:-postgres}")
fi
PSQL+=(-v ON_ERROR_STOP=1 -q)

run() { echo "→ $1"; "${PSQL[@]}" "${@:2}" -f "$SQL_DIR/$1"; }

run 001_erp.sql
run 002_analytics.sql
if [[ "${SKIP_KB:-0}" != "1" ]]; then run 003_kb.sql; else echo "→ 003_kb.sql пропущен (SKIP_KB=1)"; fi
run 004_agent_role.sql -v agent_password="$AGENT_DB_PASSWORD"
(cd "$DATA_DIR" && run 010_load_demo.sql)
echo "Готово: демо-данные загружены."
