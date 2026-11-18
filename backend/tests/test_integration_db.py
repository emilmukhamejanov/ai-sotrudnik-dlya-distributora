"""Интеграционные тесты на живом Postgres 16 + pgvector с загруженными демо-данными.

    TEST_DATABASE_URL=postgresql://dala:…@127.0.0.1:5432/dala \
    TEST_AGENT_DATABASE_URL=postgresql://agent_reader:…@127.0.0.1:5432/dala  make test-db
"""

from __future__ import annotations

import psycopg
import pytest

from dala_agent.actions.bitrix import BitrixStub
from dala_agent.actions.directory import EmployeeDirectory
from dala_agent.agent.graph import AgentDeps
from dala_agent.agent.service import Agent
from dala_agent.db import AgentSQLExecutor
from dala_agent.embeddings import HashingEmbedder
from dala_agent.llm import FakeLLM
from dala_agent.rag.answer import DocsTool
from dala_agent.rag.indexer import index_documents
from dala_agent.rag.retriever import Retriever
from dala_agent.tools.schema_context import load_schema_context
from dala_agent.tools.sql_guard import check_sql
from dala_agent.tools.text_to_sql import TextToSQLTool

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def executor(db_urls):
    return AgentSQLExecutor(db_urls[1])


@pytest.fixture(scope="module")
def indexed(db_urls):
    index_documents(db_urls[0], HashingEmbedder(), force=True)
    return Retriever(db_urls[0], HashingEmbedder())


# --- второй рубеж: права роли agent_reader в самой базе ---


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT iin FROM erp.clients LIMIT 1",
        "SELECT content FROM kb.chunks LIMIT 1",
        "CREATE TEMP TABLE x (a int)",
        "UPDATE analytics.clients SET name = 'x'",
        "SELECT pg_sleep(6)",
    ],
)
def test_agent_role_is_restricted_even_without_guard(executor, sql):
    with pytest.raises(psycopg.Error):
        executor.run(sql, max_rows=10)


def test_schema_context(executor):
    ctx = load_schema_context(executor)
    assert ctx.export_date == "2026-11-15"
    assert set(ctx.views) == {
        "export_info", "warehouses", "products", "managers", "clients", "sales", "sales_documents", "stock",
        "receivables", "plan_fact",
    }  # fmt: skip
    rendered = ctx.render()
    assert "Отсрочка, дней" in rendered and "Напитки" in rendered
    assert "iin" not in rendered and "phone" not in rendered


@pytest.mark.parametrize(
    "sql",
    [
        # Типичные запросы проходят проверку и после пересборки из дерева остаются рабочими.
        "SELECT doc_month, ROUND(SUM(amount_kzt), 0) FROM sales WHERE doc_month >= '2026-08-01' GROUP BY 1 ORDER BY 1",
        "SELECT EXTRACT(YEAR FROM doc_month)::int, SUM(amount_kzt) FROM sales WHERE category = 'Напитки' "
        "AND client_city = 'Алматы' AND doc_month IN ('2026-10-01', '2025-10-01') GROUP BY 1",
        "WITH d AS (SELECT client_id, MAX(days_overdue) mx FROM receivables GROUP BY 1) "
        "SELECT c.name, d.mx, c.shipment_blocked FROM d JOIN clients c ON c.id = d.client_id WHERE d.mx > 15",
        "SELECT manager_name, completion_pct FROM plan_fact WHERE month = DATE_TRUNC('month', DATE '2026-10-15')",
        "SELECT product_name, lot_number, days_to_expiry FROM stock WHERE is_expired ORDER BY days_to_expiry",
        "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY amount_kzt) FROM sales_documents "
        "WHERE doc_type = 'Реализация'",
    ],
)
def test_guarded_queries_execute(executor, sql):
    res = executor.run(check_sql(sql).sql, max_rows=200)
    assert res.rows


def test_known_facts(executor):
    """Факты из data_model.md, на которые будут опираться evals."""
    blocked = executor.run(check_sql("SELECT count(*) FROM clients WHERE shipment_blocked").sql, max_rows=1)
    assert blocked.rows[0][0] == 6
    expired = executor.run(check_sql("SELECT count(*) FROM stock WHERE is_expired").sql, max_rows=1)
    assert expired.rows[0][0] == 7


def test_indexing_is_idempotent(db_urls, indexed):
    report = index_documents(db_urls[0], HashingEmbedder())
    assert report.indexed == [] and len(report.skipped) == 9


@pytest.mark.parametrize(
    "question,slug,section",
    [
        ("Какая максимальная скидка для HoReCa без согласования?", "politika-cen-i-skidok", "2.1"),
        ("Когда блокируются отгрузки должнику?", "kreditnaya-politika", "4.1"),
        ("Тапсырыстың ең аз сомасы қанша?", "zhadynama-sauda-okilderine", "1.2"),
        ("Какой срок претензии по скрытым недостаткам?", "vozvraty-i-pretenzii", "2.2"),
        ("На сколько поднимутся цены на чай?", "pismo-postavshika-izmenenie-cen", None),
    ],
)
def test_retrieval_finds_the_clause(indexed, question, slug, section):
    hits = indexed.search(question, top_k=4)
    assert (slug, section) in [(h.slug, h.section) for h in hits]


def test_external_letter_is_marked(indexed):
    hits = indexed.search("письмо поставщика цены на чай", top_k=6)
    letter = next(h for h in hits if h.slug == "pismo-postavshika-izmenenie-cen")
    assert letter.trust_level == "external"


def test_agent_end_to_end_with_real_tools(db_urls, executor, indexed):
    """Граф с настоящими инструментами и базой; модель заменена сценарием."""
    llm = FakeLLM(
        {
            "router": [
                {"route": "data", "language": "ru", "reason": "цифры"},
                {"route": "docs", "language": "ru", "reason": "правила"},
            ],
            "sql_generate": [
                {"sql": "SELECT iin FROM erp.clients"},  # первая попытка отклонена проверкой
                {"sql": 'SELECT COUNT(*) AS "заблокировано" FROM clients WHERE shipment_blocked'},
            ],
            "answer_compose": "Отгрузки заблокированы у 6 клиентов.",
            "rag_answer": {
                "found": True,
                "answer": "Без согласования — до 4% (Положение о ценообразовании и скидках, п. 2.1).",
                "citations": [{"slug": "politika-cen-i-skidok", "section": "2.1"}],
            },
        }
    )
    deps = AgentDeps(
        llm=llm,
        router_llm=llm,
        sql_tool=TextToSQLTool(llm=llm, executor=executor, schema=load_schema_context(executor)),
        docs_tool=DocsTool(llm=llm, retriever=indexed),
        bitrix=BitrixStub(),
        directory=EmployeeDirectory.from_db(executor),
    )
    agent = Agent(deps)
    r = agent.chat("e2e", "Скольким клиентам заблокированы отгрузки?")
    assert r.sql["rows"] == [[6]]
    sql_step = next(s for s in r.trace if s["node"] == "sql")
    assert "Схема erp" in sql_step["detail"]["attempts"][0]["error"]

    r2 = agent.chat("e2e", "Какая максимальная скидка для HoReCa без согласования?")
    assert r2.citations[0]["citation"] == "Положение о ценообразовании и скидках, п. 2.1"
    rag_prompt = next(c for c in llm.calls if c.purpose == "rag_answer").messages[-1]["content"]
    assert 'section="2.1"' in rag_prompt
    assert [e.id for e in deps.directory.find("Иванов")] == [11]


def test_eval_reference_sql_is_valid(executor):
    """Эталонные запросы evals выполняются под ролью агента и возвращают данные."""
    from dala_agent.evals import load_cases

    for case in load_cases():
        for turn in case["turns"]:
            refs = turn.get("expect", {}).get("reference_sql") or []
            for sql in [refs] if isinstance(refs, str) else refs:
                rows = executor.fetch(sql)
                assert rows and rows[0][0] is not None, case["id"]
