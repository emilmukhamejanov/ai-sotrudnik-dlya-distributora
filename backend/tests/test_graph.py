"""Граф LangGraph на заглушках: маршруты, подтверждение действий, передача человеку, трасса."""

from __future__ import annotations

from conftest import FakeDocsTool, FakeSQLTool, route

from dala_agent.llm import LLMError
from dala_agent.rag.answer import DocsResult
from dala_agent.tools.text_to_sql import SQLToolResult

TASK_IVANOV = {"kind": "create_task", "title": "Позвонить клиенту с просрочкой", "responsible_surname": "Иванов"}


def titles(result) -> list[str]:
    return [s["title"] for s in result.trace]


def test_data_route(make_agent, fake_llm):
    fake_llm.set("router", route("data"))
    sql_tool = FakeSQLTool()
    r = make_agent(sql_tool=sql_tool).chat("t", "Сколько продали напитков в Алматы за октябрь?")
    assert r.status == "done" and r.route == "data"
    assert r.answer == "Ответ по данным."
    assert r.sql["rows"] == [[2025, 26289747], [2026, 35279117]]
    assert titles(r) == ["Маршрут: data", "SQL: выполнен", "Ответ сформулирован"]
    assert fake_llm.purposes() == ["router", "answer_compose"]
    # Таблица результата и SQL уходят модели, которая формулирует ответ.
    assert "35279117" in fake_llm.calls[-1].messages[0]["content"].replace(" ", "")


def test_usage_and_cost_are_summed(make_agent, fake_llm):
    fake_llm.set("router", route("data"))
    r = make_agent().chat("t", "Выручка за октябрь?")
    assert r.usage["input_tokens"] >= 100 and r.usage["output_tokens"] >= 20
    assert r.usage["cost_usd"] is not None and r.usage["cost_usd"] > 0


def test_docs_route_with_citation(make_agent, fake_llm):
    fake_llm.set("router", route("docs"))
    r = make_agent().chat("t", "Какая максимальная скидка для HoReCa без согласования?")
    assert "п. 2.1" in r.answer
    assert r.citations[0]["section"] == "2.1"
    assert "answer_compose" not in fake_llm.purposes()  # ответ по документам не переписывается второй раз


def test_docs_not_found(make_agent, fake_llm):
    fake_llm.set("router", route("docs"))
    docs = FakeDocsTool(DocsResult(question="", found=False, answer=""))
    r = make_agent(docs_tool=docs).chat("t", "Сколько стоит аренда офиса?")
    assert "не нашёл" in r.answer
    assert r.citations == []


def test_suspicious_document_is_flagged(make_agent, fake_llm):
    fake_llm.set("router", route("docs"))
    docs = FakeDocsTool(
        DocsResult(
            question="",
            found=True,
            answer="По письму ТОО «Шай Үйі» цены на чай вырастут на 6% с 01.12.2026.",
            citations=[{"slug": "pismo-postavshika-izmenenie-cen", "section": None, "citation": "Письмо"}],
            suspicious_instructions=True,
            sanitized=[{"slug": "pismo-postavshika-izmenenie-cen", "section": None, "removed_comments": 1,
                        "removed_paragraphs": 1}],
        )
    )  # fmt: skip
    r = make_agent(docs_tool=docs).chat("t", "Что пишет поставщик чая?")
    assert "проигнорировал" in r.answer
    assert "25%" not in r.answer


def test_no_warning_when_flagged_document_not_used(make_agent, fake_llm):
    """Письмо с атакой нашлось поиском, но ответ по регламенту — предупреждение не нужно."""
    fake_llm.set("router", route("docs"))
    docs = FakeDocsTool(
        DocsResult(
            question="",
            found=True,
            answer="До 4% (п. 2.1).",
            citations=[{"slug": "politika-cen-i-skidok", "section": "2.1", "citation": "Положение, п. 2.1"}],
            suspicious_instructions=True,
            sanitized=[{"slug": "pismo-postavshika-izmenenie-cen", "section": None, "removed_comments": 1,
                        "removed_paragraphs": 1}],
        )
    )  # fmt: skip
    r = make_agent(docs_tool=docs).chat("t", "Скидка HoReCa?")
    assert r.answer == "До 4% (п. 2.1)."


def test_data_and_docs_passes_rules_to_sql(make_agent, fake_llm):
    fake_llm.set("router", route("data_and_docs"))
    sql_tool = FakeSQLTool()
    r = make_agent(sql_tool=sql_tool).chat("t", "Кому из должников уже должны были заблокировать отгрузки?")
    assert titles(r)[:3] == ["Маршрут: data_and_docs", "Документы: найдено", "SQL: выполнен"]
    assert "п. 2.1" in sql_tool.calls[0]["rules_context"]
    assert r.citations  # ссылки на пункты сохраняются в смешанном ответе


def test_sql_failure_hands_off_to_human(make_agent, fake_llm, stub):
    fake_llm.set("router", route("data"))
    failed = SQLToolResult(question="", ok=False, error="Ошибка PostgreSQL: timeout")
    r = make_agent(sql_tool=FakeSQLTool(failed)).chat("t", "Сложный отчёт")
    assert r.handoff and r.handoff["ticket_id"] == "H-0001"
    assert "Передал вопрос сотруднику" in r.answer
    assert stub.records[-1]["type"] == "handoff"


def test_sql_cannot_answer_is_explained(make_agent, fake_llm, stub):
    fake_llm.set("router", route("data"))
    res = SQLToolResult(question="", ok=False, cannot_answer="в выгрузке нет данных о погоде")
    r = make_agent(sql_tool=FakeSQLTool(res)).chat("t", "Какая была погода?")
    assert "нет данных о погоде" in r.answer
    assert not stub.records


def test_router_human_and_llm_outage(make_agent, fake_llm, stub):
    fake_llm.set("router", [route("human"), LLMError("503")])
    agent = make_agent()
    r1 = agent.chat("a", "Клиент угрожает судом, что делать?")
    r2 = agent.chat("b", "Сколько продали?")
    assert r1.handoff and r2.handoff
    assert len([x for x in stub.records if x["type"] == "handoff"]) == 2
    assert stub.records[-1]["thread_id"] == "b"


def test_smalltalk_kazakh(make_agent, fake_llm):
    fake_llm.set("router", route("smalltalk", "ru"))
    r = make_agent().chat("t", "Сәлем! Не істей аласың?")
    assert r.language == "kk"  # казахские буквы надёжнее ответа роутера
    assert "Битрикс24" in r.answer and "Сәлеметсіз" in r.answer


def test_action_requires_confirmation(make_agent, fake_llm, stub):
    fake_llm.set("router", route("action"))
    fake_llm.set("action_plan", TASK_IVANOV)
    agent = make_agent()
    r = agent.chat("t", "Поставь задачу Иванову позвонить клиенту с просрочкой")
    assert r.status == "needs_confirmation"
    assert "Иванов Алексей" in r.answer and "Подтверждаете" in r.answer
    assert r.pending_action["responsible_id"] == 11
    assert stub.records == []  # до подтверждения ничего не создано
    assert agent.pending("t")["action"]["action_id"] == r.pending_action["action_id"]

    done = agent.confirm("t", approved=True, action_id=r.pending_action["action_id"])
    assert done.status == "done" and "STUB-TASK-0001" in done.answer
    assert stub.records[0]["responsible_name"] == "Иванов Алексей"
    assert agent.pending("t") is None


def test_action_rejected(make_agent, fake_llm, stub):
    fake_llm.set("router", route("action"))
    fake_llm.set("action_plan", TASK_IVANOV)
    agent = make_agent()
    r = agent.chat("t", "Поставь задачу Иванову")
    done = agent.confirm("t", approved=False, action_id=r.pending_action["action_id"])
    assert "ничего не создано" in done.answer
    assert stub.records == []


def test_stale_confirmation_is_ignored(make_agent, fake_llm, stub):
    fake_llm.set("router", route("action"))
    fake_llm.set("action_plan", TASK_IVANOV)
    agent = make_agent()
    agent.chat("t", "Поставь задачу Иванову")
    done = agent.confirm("t", approved=True, action_id="чужой-id")
    assert "ничего не создано" in done.answer
    assert stub.records == []


def test_new_message_cancels_pending_action(make_agent, fake_llm, stub):
    fake_llm.set("router", [route("action"), route("data")])
    fake_llm.set("action_plan", TASK_IVANOV)
    agent = make_agent()
    agent.chat("t", "Поставь задачу Иванову")
    r = agent.chat("t", "Хотя нет, сколько продали за октябрь?")
    assert r.status == "done" and r.route == "data"
    assert agent.pending("t") is None
    assert stub.records == []


def test_ambiguous_responsible_then_clarified(make_agent, fake_llm, stub):
    fake_llm.set("router", route("action"))
    fake_llm.set(
        "action_plan",
        [
            {"kind": "create_task", "title": "Позвонить клиенту", "responsible_surname": "Петренко"},
            {
                "kind": "create_task",
                "title": "Позвонить клиенту",
                "responsible_surname": "Петренко",
                "responsible_first_name": "Динара",
            },
        ],
    )
    agent = make_agent()
    r = agent.chat("t", "Поставь задачу Петренко позвонить клиенту")
    assert r.status == "needs_clarification"
    assert "Петренко Ержан" in r.answer and "Петренко Динара" in r.answer
    r2 = agent.chat("t", "Динаре")
    assert r2.status == "needs_confirmation" and r2.pending_action["responsible_id"] == 5
    # Планировщик видит историю диалога, чтобы понять уточнение «Динаре».
    assert any("Петренко" in m["content"] for m in fake_llm.calls[-1].messages[:-1])


def test_unknown_responsible(make_agent, fake_llm):
    fake_llm.set("router", route("action"))
    fake_llm.set("action_plan", {"kind": "create_task", "title": "Позвонить", "responsible_surname": "Сидоров"})
    r = make_agent().chat("t", "Поставь задачу Сидорову")
    assert r.status == "needs_clarification" and "Сидоров" in r.answer


def test_action_planner_never_sees_documents(make_agent, fake_llm):
    """Защита от prompt injection по построению: тексты документов не попадают в планировщик действий."""
    fake_llm.set("router", [route("docs"), route("action")])
    fake_llm.set("action_plan", {"kind": "create_deal", "title": "Сделка", "amount_kzt": 10_000_000})
    docs = FakeDocsTool(
        DocsResult(
            question="",
            found=True,
            answer="По письму поставщика цены на чай вырастут на 6%.",
            citations=[{"slug": "pismo", "section": None, "citation": "Письмо"}],
            suspicious_instructions=True,
        )
    )
    agent = make_agent(docs_tool=docs)
    agent.chat("t", "Что пишет поставщик?")
    r = agent.chat("t", "Создай сделку на 10 млн")
    plan_call = next(c for c in fake_llm.calls if c.purpose == "action_plan")
    assert "<fragment" not in plan_call.system + str(plan_call.messages)
    assert r.status == "needs_confirmation"  # даже сделка по прямой просьбе ждёт «да»


def test_unsupported_action(make_agent, fake_llm, stub):
    fake_llm.set("router", route("action"))
    fake_llm.set("action_plan", {"kind": "unsupported", "title": "Удалить клиента"})
    r = make_agent().chat("t", "Удали клиента из CRM")
    assert "только ставить задачи" in r.answer
    assert stub.records == []


def test_trace_is_per_turn(make_agent, fake_llm):
    fake_llm.set("router", [route("data"), route("smalltalk")])
    agent = make_agent()
    agent.chat("t", "Сколько продали?")
    r = agent.chat("t", "Спасибо!")
    assert titles(r) == ["Маршрут: smalltalk"]


def test_router_search_query_and_rules_mode_reach_docs(make_agent, fake_llm):
    """Роутер переформулирует вопрос для поиска; в смешанном вопросе документы ищут только правила."""
    fake_llm.set(
        "router",
        {"route": "data_and_docs", "language": "ru", "reason": "т", "search_query": "истёкший срок годности карантин"},
    )
    docs = FakeDocsTool()
    make_agent(docs_tool=docs).chat("t", "Есть ли просроченные партии, которые пора в карантин?")
    assert docs.calls[0]["search_query"] == "истёкший срок годности карантин"
    assert docs.calls[0]["rules_only"] is True
