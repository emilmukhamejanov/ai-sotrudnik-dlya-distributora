#!/usr/bin/env bash
# Загрузка схем и демо-данных во внешнюю базу (Neon, Supabase) — один раз перед деплоем демо.
#   DATABASE_URL=postgresql://…?sslmode=require AGENT_DB_PASSWORD=… ./db/remote-apply.sh
#   RESET=1 — удалить схемы erp, analytics, kb и загрузить заново.
set -euo pipefail
: "${DATABASE_URL:?Задайте DATABASE_URL внешней базы}"
: "${AGENT_DB_PASSWORD:?Задайте AGENT_DB_PASSWORD}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Проверяю подключение…"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -Atc "select 'Postgres ' || current_setting('server_version')"

exists="$(psql "$DATABASE_URL" -Atc "select to_regclass('erp.export_info') is not null")"
if [[ "$exists" == "t" ]]; then
  if [[ "${RESET:-0}" != "1" ]]; then
    echo "Схемы уже загружены — ничего не делаю. Пересоздать: RESET=1 (windows\\remote-db-reset.bat)."
    exit 0
  fi
  echo "Удаляю схемы kb, analytics, erp…"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -c "DROP SCHEMA IF EXISTS kb, analytics, erp CASCADE"
fi

bash "$DIR/apply.sh"
echo "Готово. Документы проиндексирует сам агент при первом запуске."
