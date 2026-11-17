-- Роль для инструмента text-to-SQL: только чтение и только схема analytics.
-- Это второй рубеж после проверки SQL в коде (только SELECT, белый список представлений).
-- Пароль передаётся переменной psql: -v agent_password=... (из .env, в репозиторий не попадает).

\if :{?agent_password}
\else
  \echo 'Не задана переменная agent_password (AGENT_DB_PASSWORD в .env)'
  \quit 1
\endif

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_reader') THEN
        CREATE ROLE agent_reader LOGIN;
    END IF;
END
$$;

ALTER ROLE agent_reader WITH LOGIN PASSWORD :'agent_password';
ALTER ROLE agent_reader SET default_transaction_read_only = on;
ALTER ROLE agent_reader SET statement_timeout = '5s';
ALTER ROLE agent_reader SET search_path = analytics;

REVOKE ALL ON SCHEMA public FROM agent_reader;
GRANT USAGE ON SCHEMA analytics TO agent_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO agent_reader;
-- erp (ПДн) и kb агенту через SQL недоступны: USAGE на эти схемы не выдаётся.
