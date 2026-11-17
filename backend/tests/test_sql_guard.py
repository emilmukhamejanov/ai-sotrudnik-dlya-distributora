"""Первый рубеж text-to-SQL: только SELECT, только белый список analytics, без служебных функций."""

from __future__ import annotations

import pytest

from dala_agent.tools.sql_guard import SQLRejected, check_sql

ALLOWED = [
    "SELECT doc_month, SUM(amount_kzt) FROM sales GROUP BY 1 ORDER BY 1",
    "select * from analytics.clients where segment = 'HoReCa'",
    "WITH m AS (SELECT manager_id, SUM(amount_kzt) s FROM sales GROUP BY 1) "
    "SELECT * FROM m JOIN managers ON managers.id = m.manager_id",
    "SELECT 1 AS a UNION ALL SELECT 2",
    "SELECT client_name, balance_kzt FROM receivables WHERE days_overdue > 15 ORDER BY balance_kzt DESC LIMIT 10",
    "SELECT date_trunc('month', doc_date)::date, to_char(doc_date, 'YYYY') FROM sales",
    "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY amount_kzt) FROM sales_documents",
    "SELECT COALESCE(SUM(amount_kzt) FILTER (WHERE doc_type = 'Возврат'), 0) FROM sales",
    "SELECT * FROM generate_series(1, 3) AS g",
    "SELECT s.* FROM sales s, LATERAL (SELECT 1) x",
    "SELECT * FROM sales;",  # завершающая точка с запятой — всё ещё один оператор
]

REJECTED = [
    ("DELETE FROM sales", "только SELECT"),
    ("UPDATE clients SET credit_limit_kzt = 0", "только SELECT"),
    ("INSERT INTO sales VALUES (1)", "только SELECT"),
    ("DROP VIEW sales", "только SELECT"),
    ("WITH x AS (DELETE FROM sales RETURNING *) SELECT * FROM x", "DELETE"),
    ("SELECT * INTO tmp FROM sales", "INTO"),
    ("SELECT * FROM sales FOR UPDATE", "LOCK"),
    ("SELECT 1; SELECT 2", "ровно один"),
    ("SELECT 1; DROP TABLE erp.clients", "ровно один"),
    ("SELECT * FROM erp.clients", "Схема erp"),
    ("SELECT iin, phone FROM erp.clients", "Схема erp"),
    ("SELECT * FROM kb.chunks", "Схема kb"),
    ("SELECT * FROM pg_catalog.pg_user", "Схема pg_catalog"),
    ("SELECT * FROM information_schema.tables", "Схема information_schema"),
    ("SELECT * FROM pg_user", "не из белого списка"),
    ("SELECT * FROM other_db.analytics.sales", "другой базе"),
    ("SELECT pg_sleep(10)", "pg_sleep"),
    ("SELECT pg_catalog.pg_sleep(10)", "pg_sleep"),
    ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
    ("SELECT lo_import('/etc/passwd')", "lo_import"),
    ("SELECT set_config('statement_timeout', '0', false)", "set_config"),
    ("SELECT current_setting('server_version')", "current_setting"),
    ("SELECT dblink('host=evil', 'select 1')", "dblink"),
    ("SELECT query_to_xml('select * from erp.clients', true, true, '')", "query_to_xml"),
    ("SELECT current_user", "CURRENT_USER"),
    ("SET statement_timeout = 0", "только SELECT"),
    ("COPY sales TO '/tmp/x'", "только SELECT"),
    ("", "Пустой"),
    ("это не sql", ""),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed(sql):
    checked = check_sql(sql)
    assert checked.sql.upper().startswith(("SELECT", "WITH"))


@pytest.mark.parametrize(("sql", "reason"), REJECTED)
def test_rejected(sql, reason):
    with pytest.raises(SQLRejected) as e:
        check_sql(sql)
    assert reason in str(e.value)


def test_views_are_schema_qualified():
    checked = check_sql("SELECT * FROM sales JOIN clients ON clients.id = sales.client_id")
    assert "analytics.sales" in checked.sql and "analytics.clients" in checked.sql
    assert checked.tables == ("clients", "sales")


def test_cte_names_are_not_qualified():
    checked = check_sql("WITH sales AS (SELECT 1 AS x) SELECT * FROM sales")
    assert "analytics.sales" not in checked.sql
    assert checked.tables == ()


def test_limit_added_and_capped():
    assert check_sql("SELECT * FROM sales", max_rows=50).sql.endswith("LIMIT 50")
    assert check_sql("SELECT * FROM sales LIMIT 10", max_rows=50).limit_applied == 10
    assert check_sql("SELECT * FROM sales LIMIT 100000", max_rows=50).sql.endswith("LIMIT 50")
    assert check_sql("SELECT 1 UNION SELECT 2", max_rows=50).sql.endswith("LIMIT 50")


COMMENT_TRICKS = ["SELECT 1 -- ; DROP TABLE x\n FROM sales", "SELECT 1 -- */ ; DROP TABLE x\n FROM sales"]


@pytest.mark.parametrize("sql", COMMENT_TRICKS)
def test_rebuilt_sql_drops_comments(sql):
    # Выполняется SQL, собранный из дерева, без комментариев: через них ничего не протащить.
    assert check_sql(sql).sql == "SELECT 1 FROM analytics.sales LIMIT 200"
