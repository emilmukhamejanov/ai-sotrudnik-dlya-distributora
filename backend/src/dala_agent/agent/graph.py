"""Граф LangGraph: роутер → инструменты → ответ; действия — только после подтверждения человеком.

    router ─┬─ data ──────────────────► sql ─┬─► compose ─► finalize
            ├─ docs ──────► docs ────────────┤
            ├─ data_and_docs ► docs ─► sql ──┘   (sql не удался → handoff)
            ├─ action ─► plan_action ─► confirm (interrupt) ─► execute ─► finalize
            ├─ human ──► handoff ─► finalize
            └─ smalltalk ─► finalize

Документы и результаты SQL никогда не попадают в планировщик действий: инструмент Битрикс24
вызывается только по просьбе пользователя и только после его «да».
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..actions.bitrix import BitrixClient, PlannedAction
from ..actions.directory import EmployeeDirectory
from ..injection import is_prompt_attack
from ..llm.base import LLMClient, LLMError
from ..pii import PiiVault, redact_output
from .prompts import (
    ACTION_SYSTEM,
    COMPOSE_SYSTEM,
    ROUTER_SYSTEM,
    ActionDraft,
    RouteDecision,
    smalltalk_text,
    t,
)
from .state import TURN_FIELDS_RESET, AgentState, detect_language, step

HISTORY_MESSAGES = 6


@dataclass
class AgentDeps:
    llm: LLMClient
    router_llm: LLMClient
    sql_tool: object  # TextToSQLTool: .run(question, history=, rules_context=) -> SQLToolResult
    docs_tool: object  # DocsTool: .run(question, language=, history=) -> DocsResult
    bitrix: BitrixClient
    directory: EmployeeDirectory
    today: Callable[[], date] = field(default=date.today)


def _history(state: AgentState) -> list[dict]:
    """Предыдущие реплики (без текущего вопроса) — для уточнений вроде «а в прошлом году?»."""
    return list(state.get("messages", [])[:-1])[-HISTORY_MESSAGES:]


def build_graph(deps: AgentDeps, checkpointer=None):
    def guard(state: AgentState) -> dict:
        """Первый узел хода: сообщает о скрытых ПДн и отклоняет попытки переписать правила агента."""
        started = time.monotonic()
        update: dict = dict(TURN_FIELDS_RESET)
        trace = []
        found = state.get("pii_found") or {}
        if found:
            kinds = ", ".join(f"{k}: {n}" for k, n in found.items())
            trace.append(step("guard", "ПДн скрыты до отправки в модель", started, found=found, kinds=kinds))
        if is_prompt_attack(state["question"]):
            lang = detect_language(state["question"]) or "ru"
            update.update(route="blocked", language=lang, answer=t(lang, "prompt_attack"))
            trace.append(step("guard", "Сообщение отклонено: попытка изменить правила агента", started))
        update["trace"] = trace
        return update

    def router(state: AgentState) -> dict:
        started = time.monotonic()
        question = state["question"]
        update: dict = {}
        try:
            res = deps.router_llm.structured(
                system=ROUTER_SYSTEM,
                messages=[*_history(state), {"role": "user", "content": question}],
                schema=RouteDecision,
                purpose="router",
                max_tokens=300,
            )
        except LLMError as e:
            update.update(route="human", route_reason=t("ru", "handoff_reason_llm"))
            update["language"] = detect_language(question) or "ru"
            update["trace"] = [step("router", "Роутер: модель недоступна → человеку", started, error=str(e))]
            return update
        decision = res.value
        update.update(
            route=decision.route,
            route_reason=decision.reason,
            search_query=decision.search_query.strip(),
            language=detect_language(question) or decision.language,
        )
        update["trace"] = [
            step("router", f"Маршрут: {decision.route}", started, res.usage, reason=decision.reason,
                 search_query=decision.search_query)
        ]  # fmt: skip
        return update

    def docs(state: AgentState) -> dict:
        started = time.monotonic()
        res = deps.docs_tool.run(
            state["question"],
            language=state.get("language", "ru"),
            history=_history(state),
            search_query=state.get("search_query") or "",
            rules_only=state.get("route") == "data_and_docs",
        )
        title = "Документы: найдено" if res.found else "Документы: ответа нет"
        return {
            "docs_result": res.as_dict(),
            "trace": [step("docs", title, started, res.usage, **res.as_dict())],
        }

    def sql(state: AgentState) -> dict:
        started = time.monotonic()
        rules = ""
        if state.get("route") == "data_and_docs" and state.get("docs_result"):
            rules = state["docs_result"].get("answer", "")
        res = deps.sql_tool.run(state["question"], history=_history(state), rules_context=rules)
        title = "SQL: выполнен" if res.ok else ("SQL: нет данных" if res.cannot_answer else "SQL: ошибка")
        return {"sql_result": res.as_dict(), "trace": [step("sql", title, started, res.usage, **res.as_dict())]}

    def compose(state: AgentState) -> dict:
        started = time.monotonic()
        lang = state.get("language", "ru")
        docs_res = state.get("docs_result")
        sql_res = state.get("sql_result")
        citations = docs_res.get("citations", []) if docs_res else []
        # Предупреждаем, только если в ответе использован документ, из которого защита вырезала инструкции.
        flagged = {x["slug"] for x in (docs_res or {}).get("sanitized", [])}
        used_flagged = bool(flagged & {c["slug"] for c in citations})
        suffix = ("\n\n" + t(lang, "suspicious")) if used_flagged else ""

        if sql_res is None:  # только документы
            if docs_res and docs_res.get("found"):
                answer = docs_res["answer"]
            else:
                answer = t(lang, "docs_not_found")
            return {"answer": answer + suffix, "citations": citations}

        if not sql_res.get("ok"):
            reason = sql_res.get("cannot_answer") or ""
            return {"answer": t(lang, "sql_cannot", reason=reason), "citations": citations}

        from ..tools.text_to_sql import SQLToolResult  # локально: только для форматирования таблицы

        table = SQLToolResult(
            question=state["question"], ok=True, columns=sql_res["columns"], rows=sql_res["rows"],
            truncated=sql_res["truncated"],
        ).to_markdown()  # fmt: skip
        parts = [f"Вопрос: {state['question']}", f"SQL:\n{sql_res['sql']}", f"Результат:\n{table}"]
        if sql_res.get("assumptions"):
            parts.append("Допущения: " + "; ".join(sql_res["assumptions"]))
        with_rules = ""
        if docs_res and docs_res.get("found"):
            parts.append(f"Правила из документов:\n{docs_res['answer']}")
            with_rules = " и правилам компании (ссылки на пункты сохрани)"
        try:
            res = deps.llm.text(
                system=COMPOSE_SYSTEM.format(
                    with_rules=with_rules, language="казахский" if lang == "kk" else "русский"
                ),
                messages=[{"role": "user", "content": "\n\n".join(parts)}],
                purpose="answer_compose",
            )
        except LLMError as e:
            fallback = table if not citations else table + "\n\n" + docs_res["answer"]
            return {
                "answer": fallback + suffix,
                "citations": citations,
                "trace": [step("compose", "Ответ: модель недоступна, показана таблица", started, error=str(e))],
            }
        return {
            "answer": res.text + suffix,
            "citations": citations,
            "trace": [step("compose", "Ответ сформулирован", started, res.usage)],
        }

    def plan_action(state: AgentState) -> dict:
        started = time.monotonic()
        lang = state.get("language", "ru")
        try:
            res = deps.llm.structured(
                system=ACTION_SYSTEM.format(
                    today=deps.today().isoformat(), language="казахский" if lang == "kk" else "русский"
                ),
                messages=[*_history(state), {"role": "user", "content": state["question"]}],
                schema=ActionDraft,
                purpose="action_plan",
            )
        except LLMError as e:
            return {"route": "human", "trace": [step("plan_action", "Планировщик недоступен", started, error=str(e))]}
        d = res.value
        trace = [step("plan_action", f"Действие: {d.kind}", started, res.usage, draft=d.model_dump(mode="json"))]

        if d.kind == "unsupported":
            return {"answer": t(lang, "action_unsupported"), "trace": trace}

        action = PlannedAction(
            kind=d.kind,
            title=d.title,
            description=d.description,
            deadline=d.deadline.isoformat() if d.deadline else None,
            amount_kzt=d.amount_kzt,
            client_name=d.client_name,
            action_id=uuid.uuid4().hex[:12],
        )
        if d.responsible_surname:
            found = deps.directory.find(d.responsible_surname, d.responsible_first_name)
            if not found:
                name = " ".join(x for x in (d.responsible_surname, d.responsible_first_name) if x)
                return {"answer": t(lang, "who_not_found", name=name), "status": "needs_clarification", "trace": trace}
            if len(found) > 1:
                options = ", ".join(f"{e.short} ({e.position}, {e.city})" for e in found)
                return {"answer": t(lang, "who_ambiguous", options=options), "status": "needs_clarification",
                        "trace": trace}  # fmt: skip
            action.responsible_id = found[0].bitrix_user_id
            action.responsible_name = found[0].short
        elif d.kind == "create_task":
            return {"answer": d.missing or "Кому поставить задачу?", "status": "needs_clarification", "trace": trace}

        if d.missing and d.kind == "create_deal" and not d.title:
            return {"answer": d.missing, "status": "needs_clarification", "trace": trace}
        return {"pending_action": asdict(action), "trace": trace}

    def confirm(state: AgentState) -> dict:
        action = PlannedAction(**state["pending_action"])
        lang = state.get("language", "ru")
        decision = interrupt(
            {"type": "confirm_action", "action": state["pending_action"], "preview": action.preview(lang)}
        )
        approved = bool(decision.get("approved")) if isinstance(decision, dict) else False
        if approved and decision.get("action_id") != action.action_id:
            approved = False  # «да» относится к другому (устаревшему) действию или не указывает, к какому
        started = time.monotonic()
        if not approved:
            return {
                "answer": t(lang, "action_cancelled"),
                "pending_action": None,
                "trace": [step("confirm", "Пользователь отклонил действие", started, action_id=action.action_id)],
            }
        return {"trace": [step("confirm", "Пользователь подтвердил действие", started, action_id=action.action_id)]}

    def execute(state: AgentState) -> dict:
        started = time.monotonic()
        lang = state.get("language", "ru")
        action = PlannedAction(**state["pending_action"])
        # Реальные значения ПДн (телефон из просьбы пользователя) попадают только в сам Битрикс24.
        vault = PiiVault.from_dict(state.get("pii_vault"))
        real = PlannedAction(**{k: vault.unmask(v) if isinstance(v, str) else v for k, v in asdict(action).items()})
        res = deps.bitrix.execute(real)
        what = t(lang, "task" if action.kind == "create_task" else "deal")
        answer = (
            t(lang, "action_done", what=what, ext=res.external_id)
            if res.ok
            else t(lang, "action_failed", error=res.error)
        )
        return {
            "answer": answer,
            "action_result": asdict(res),
            "pending_action": None,
            "trace": [step("execute", f"Битрикс24 ({res.mode}): {'ок' if res.ok else 'ошибка'}", started,
                           **asdict(res))],
        }  # fmt: skip

    def handoff(state: AgentState, config) -> dict:
        started = time.monotonic()
        lang = state.get("language", "ru")
        sql_res = state.get("sql_result")
        reason = state.get("route_reason") or ""
        if sql_res and not sql_res.get("ok") and not sql_res.get("cannot_answer"):
            reason = t(lang, "handoff_reason_sql")
        thread_id = config.get("configurable", {}).get("thread_id", "")
        vault = PiiVault.from_dict(state.get("pii_vault"))
        ticket = deps.bitrix.handoff(thread_id=thread_id, reason=reason, question=vault.unmask(state["question"]))
        stored = {**asdict(ticket), "question": state["question"]}  # в состоянии — только с метками
        return {
            "answer": t(lang, "handoff", ticket=ticket.ticket_id),
            "handoff": stored,
            "trace": [step("handoff", "Передано человеку", started, ticket=ticket.ticket_id, reason=reason)],
        }

    def smalltalk(state: AgentState) -> dict:
        return {"answer": smalltalk_text(state.get("language", "ru"))}

    def finalize(state: AgentState) -> dict:
        started = time.monotonic()
        answer, hidden = redact_output(state.get("answer") or "", PiiVault.from_dict(state.get("pii_vault")))
        update: dict = {"answer": answer, "messages": [{"role": "assistant", "content": answer}]}
        if hidden:
            update["trace"] = [step("finalize", f"Из ответа скрыто ПДн: {hidden}", started, hidden=hidden)]
        return update

    g = StateGraph(AgentState)
    for name, fn in [
        ("guard", guard), ("router", router), ("docs", docs), ("sql", sql), ("compose", compose),
        ("plan_action", plan_action), ("confirm", confirm), ("execute", execute), ("handoff", handoff),
        ("smalltalk", smalltalk), ("finalize", finalize),
    ]:  # fmt: skip
        g.add_node(name, fn)

    g.add_edge(START, "guard")
    g.add_conditional_edges("guard", lambda s: "finalize" if s.get("route") == "blocked" else "router",
                            ["router", "finalize"])  # fmt: skip
    g.add_conditional_edges(
        "router",
        lambda s: {
            "data": "sql",
            "docs": "docs",
            "data_and_docs": "docs",
            "action": "plan_action",
            "human": "handoff",
            "smalltalk": "smalltalk",
        }[s["route"]],  # fmt: skip
        ["sql", "docs", "plan_action", "handoff", "smalltalk"],
    )
    g.add_conditional_edges("docs", lambda s: "sql" if s.get("route") == "data_and_docs" else "compose",
                            ["sql", "compose"])  # fmt: skip
    g.add_conditional_edges(
        "sql",
        lambda s: "compose" if s["sql_result"]["ok"] or s["sql_result"].get("cannot_answer") else "handoff",
        ["compose", "handoff"],
    )
    g.add_edge("compose", "finalize")
    g.add_conditional_edges(
        "plan_action",
        lambda s: "confirm" if s.get("pending_action") else ("handoff" if s.get("route") == "human" else "finalize"),
        ["confirm", "handoff", "finalize"],
    )
    g.add_conditional_edges("confirm", lambda s: "execute" if s.get("pending_action") else "finalize",
                            ["execute", "finalize"])  # fmt: skip
    g.add_edge("execute", "finalize")
    g.add_edge("handoff", "finalize")
    g.add_edge("smalltalk", "finalize")
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer or InMemorySaver())
