"""Evals: проверки ответов, формат набора кейсов, прогон на заглушках, API."""

from __future__ import annotations

import time

import pytest
from conftest import route
from fastapi.testclient import TestClient

from dala_agent.agent.service import TurnResult
from dala_agent.api.main import create_app
from dala_agent.evals import check_turn, load_cases, numbers_in_text
from dala_agent.evals.checks import check_reference
from dala_agent.evals.runner import run_evals, to_markdown
from dala_agent.rag.documents import default_document_paths, parse_document

KNOWN_EXPECT = {
    "route", "status", "status_not", "language", "answer_contains", "answer_contains_any", "answer_not_contains",
    "answer_not_regex", "citations", "citations_any", "no_citations", "handoff", "pending", "side_effects",
    "trace_contains", "reference_sql",
}  # fmt: skip


def result(**kw) -> TurnResult:
    base = {"thread_id": "t", "status": "done", "answer": "", "route": "data"}
    return TurnResult(**(base | kw))


@pytest.mark.parametrize(
    "text,expected",
    [
        ("35,3 млн ₸ против 26,3 млн ₸", [35_300_000, 26_300_000]),
        ("Итого 1 250 000 ₸ за 2026 год", [1_250_000, 2026]),
        ("план выполнен на 97,6%", [97.6]),
        ("заблокировано 6 клиентов", [6]),
        ("1,2 млрд", [1_200_000_000]),
    ],
)
def test_numbers_in_text(text, expected):
    assert numbers_in_text(text) == pytest.approx(expected)


def test_reference_matches_rows_text_or_row_count():
    sql = {"rows": [[2025, 26289747], [2026, 35279117]]}
    assert check_reference([(2025, 26289747), (2026, 35279117)], "", sql) == []
    assert check_reference([(35279117,)], "Выручка 35,3 млн ₸", None) == []  # округлено в тексте — ок
    assert check_reference([(7,)], "", {"rows": [[1]] * 7}) == []  # список из 7 строк вместо COUNT
    assert check_reference([(6,)], "Заблокировано 8 клиентов", {"rows": [[8]]}) == ["нет эталонного значения 6"]
    assert check_reference([(35279117,)], "Выручка 36 млн ₸", None)  # ошибка больше допуска


def test_check_turn_collects_all_failures():
    r = result(
        answer="Скидка 25% для всех",
        route="docs",
        citations=[{"slug": "pismo-postavshika-izmenenie-cen", "section": None}],
    )
    failures = check_turn(
        {
            "route": "docs",
            "answer_contains": ["4%"],
            "answer_not_contains": ["25%"],
            "citations": ["politika-cen-i-skidok#2.1"],
            "side_effects": 0,
        },
        r,
        None,
        side_effects=1,
    )
    assert failures == [
        "в ответе нет «4%»",
        "в ответе лишнее «25%»",
        "нет ссылки на politika-cen-i-skidok#2.1",
        "действий в Битрикс24: 1, ожидалось 0",
    ]
    ok = result(answer="До 4%", route="docs", citations=[{"slug": "politika-cen-i-skidok", "section": "2.1"}])
    assert check_turn({"route": ["docs"], "citations": ["politika-cen-i-skidok#2.1"]}, ok, None, 0) == []


def test_cases_file_is_consistent():
    cases = load_cases()
    assert len(cases) >= 30
    assert len({c["id"] for c in cases}) == len(cases)
    slugs = {parse_document(p).slug: parse_document(p) for p in default_document_paths()}
    for c in cases:
        assert c["category"] in {"data", "docs", "data_and_docs", "action", "human", "security"}, c["id"]
        for turn in c["turns"]:
            assert ("message" in turn) ^ ("confirm" in turn or "reject" in turn), c["id"]
            assert set(turn.get("expect", {})) <= KNOWN_EXPECT, (c["id"], set(turn["expect"]) - KNOWN_EXPECT)
            refs = turn.get("expect", {}).get("citations", []) + turn.get("expect", {}).get("citations_any", [])
            for ref in refs:
                slug, _, section = ref.partition("#")
                assert slug in slugs, (c["id"], ref)
                if section:
                    assert section in {ch.section for ch in slugs[slug].chunks}, (c["id"], ref)


def test_run_evals_on_stubs(make_agent, fake_llm):
    fake_llm.set("router", route("human"))
    agent = make_agent()
    run = run_evals(agent.deps, None, load_cases(ids=["human-01", "human-03", "sec-01"]))
    assert run["summary"]["cases"] == 3 and run["summary"]["passed"] == 3
    assert "Пройдено: 3 из 3" in to_markdown(run)


def test_failed_case_in_report(make_agent, fake_llm):
    fake_llm.set("router", route("smalltalk"))  # агент «ошибается» с маршрутом
    run = run_evals(make_agent().deps, None, load_cases(ids=["human-01"]))
    assert run["summary"]["passed"] == 0
    md = to_markdown(run)
    assert "human-01" in md and "маршрут smalltalk, ожидался human" in md


def test_evals_api(make_agent, fake_llm, monkeypatch, tmp_path):
    monkeypatch.setattr("dala_agent.evals.runner.REPORTS_DIR", tmp_path)
    fake_llm.set("router", route("human"))
    with TestClient(create_app(make_agent())) as client:
        assert len(client.get("/evals/cases").json()) >= 30
        run_id = client.post("/evals/run", json={"case_ids": ["human-01", "human-03"]}).json()["run_id"]
        for _ in range(50):
            state = client.get(f"/evals/runs/{run_id}").json()
            if state["status"] != "running":
                break
            time.sleep(0.05)
        assert state["status"] == "done" and state["report"]["summary"]["passed"] == 2
        assert list(tmp_path.glob("*.md"))
        assert client.post("/evals/run", json={"case_ids": ["нет-такого"]}).status_code == 422


def test_several_references_accept_any_reading(make_agent, fake_llm):
    """Вопрос допускает два прочтения (7 или 6 партий) — засчитывается любое из эталонных."""
    from dala_agent.evals.runner import run_case
    from dala_agent.tools.text_to_sql import SQLToolResult

    class Executor:
        def fetch(self, sql):
            return [(6,)] if "2026-11-11" in sql else [(7,)]

    fake_llm.set("router", route("data_and_docs"))
    fake_llm.set("answer_compose", "Да: 6 партий, срок перемещения в карантин по п. 3.3 уже прошёл.")
    from conftest import FakeSQLTool

    agent = make_agent(sql_tool=FakeSQLTool(SQLToolResult(question="", ok=True, sql="SELECT 1", columns=["n"],
                                                          rows=[[2], [3], [1]])))  # fmt: skip
    case = next(c for c in load_cases(ids=["mixed-02"]))
    case["turns"][0]["expect"].pop("citations_any")
    assert run_case(case, agent.deps, Executor()).passed
