"""Общие заглушки: инструменты без базы и модели без сети."""

from __future__ import annotations

import os

import pytest

from dala_agent.actions.bitrix import BitrixStub
from dala_agent.actions.directory import Employee, EmployeeDirectory
from dala_agent.agent.graph import AgentDeps
from dala_agent.agent.service import Agent
from dala_agent.llm import FakeLLM, Usage
from dala_agent.rag.answer import DocsResult
from dala_agent.tools.text_to_sql import SQLToolResult

EMPLOYEES = [
    Employee(1, "Петренко Ержан Нурланович", "Руководитель отдела продаж", "Алматы"),
    Employee(5, "Петренко Динара Нурлановна", "Торговый представитель", "Алматы"),
    Employee(7, "Калиева Гульнара Жанатовна", "Региональный менеджер", "Астана"),
    Employee(11, "Иванов Алексей Алексеевич", "Региональный менеджер", "Шымкент"),
    Employee(12, "Калиев Ержан Талгатович", "Торговый представитель", "Шымкент"),
]


class FakeSQLTool:
    def __init__(self, result: SQLToolResult | None = None):
        self.result = result or SQLToolResult(
            question="",
            ok=True,
            sql="SELECT 1 FROM analytics.sales LIMIT 200",
            columns=["год", "выручка, ₸"],
            rows=[[2025, 26289747], [2026, 35279117]],
            usage=Usage("fake", 100, 20),
        )
        self.calls: list[dict] = []

    def run(self, question, *, history=None, rules_context=""):
        self.calls.append({"question": question, "history": history, "rules_context": rules_context})
        self.result.question = question
        return self.result


class FakeDocsTool:
    def __init__(self, result: DocsResult | None = None):
        self.result = result or DocsResult(
            question="",
            found=True,
            answer="Без согласования — до 4% (Положение о ценообразовании и скидках, п. 2.1).",
            citations=[{"slug": "politika-cen-i-skidok", "section": "2.1", "citation": "Положение…, п. 2.1"}],
        )
        self.calls: list[dict] = []

    def run(self, question, *, language="ru", history=None):
        self.calls.append({"question": question, "language": language})
        return self.result


def route(name: str, language: str = "ru") -> dict:
    return {"route": name, "language": language, "reason": "тест"}


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM({"answer_compose": "Ответ по данным."})


@pytest.fixture
def stub() -> BitrixStub:
    return BitrixStub()


@pytest.fixture
def make_agent(fake_llm, stub):
    def _make(sql_tool=None, docs_tool=None) -> Agent:
        deps = AgentDeps(
            llm=fake_llm,
            router_llm=fake_llm,
            sql_tool=sql_tool or FakeSQLTool(),
            docs_tool=docs_tool or FakeDocsTool(),
            bitrix=stub,
            directory=EmployeeDirectory(EMPLOYEES),
        )
        return Agent(deps, price_per_mtok=(3.0, 15.0))

    return _make


@pytest.fixture(scope="session")
def db_urls() -> tuple[str, str]:
    service = os.environ.get("TEST_DATABASE_URL")
    agent = os.environ.get("TEST_AGENT_DATABASE_URL")
    if not service or not agent:
        pytest.skip("Интеграционные тесты: задайте TEST_DATABASE_URL и TEST_AGENT_DATABASE_URL")
    return service, agent
