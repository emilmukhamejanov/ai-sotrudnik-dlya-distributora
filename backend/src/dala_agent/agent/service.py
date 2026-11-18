"""Фасад над графом: один ход диалога → ответ, статус, трасса шагов, токены и стоимость."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from langgraph.types import Command

from ..config import Settings
from ..pii import PiiVault
from .graph import AgentDeps, build_graph


@dataclass
class TurnResult:
    thread_id: str
    status: str  # done | needs_confirmation | needs_clarification
    answer: str
    language: str = "ru"
    route: str | None = None
    citations: list[dict] = field(default_factory=list)
    pending_action: dict | None = None
    sql: dict | None = None
    handoff: dict | None = None
    action_result: dict | None = None
    trace: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    duration_ms: int = 0


class Agent:
    def __init__(self, deps: AgentDeps, *, price_per_mtok: tuple[float, float] | None = None, checkpointer=None):
        self.deps = deps
        self.graph = build_graph(deps, checkpointer=checkpointer)
        self._price = price_per_mtok

    @classmethod
    def from_settings(cls, settings: Settings, **overrides: Any) -> Agent:
        from .factory import build_deps

        return cls(build_deps(settings, **overrides), price_per_mtok=settings.price())

    def _config(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def _trace_len(self, thread_id: str) -> int:
        snap = self.graph.get_state(self._config(thread_id))
        return len(snap.values.get("trace", [])) if snap and snap.values else 0

    def chat(self, thread_id: str, message: str) -> TurnResult:
        before = self._trace_len(thread_id)
        started = time.monotonic()
        # ПДн заменяются метками ДО графа: в модель, чекпойнт истории и трассу попадает только текст с метками.
        vault = self._vault(thread_id)
        masked, found = vault.mask(message)
        # Новое сообщение во время ожидания подтверждения начинает новый ход: старое действие
        # не выполняется (для выполнения нужен явный confirm).
        self.graph.invoke(
            {
                "question": masked,
                "messages": [{"role": "user", "content": masked}],
                "pii_vault": vault.to_dict(),
                "pii_found": found,
            },
            self._config(thread_id),
        )
        return self._result(thread_id, before, started)

    def _vault(self, thread_id: str) -> PiiVault:
        snap = self.graph.get_state(self._config(thread_id))
        return PiiVault.from_dict(snap.values.get("pii_vault") if snap and snap.values else None)

    def confirm(self, thread_id: str, *, approved: bool, action_id: str | None = None) -> TurnResult:
        snap = self.graph.get_state(self._config(thread_id))
        if not snap.interrupts:
            raise LookupError("Нет действия, ожидающего подтверждения")
        if approved and not action_id:
            raise ValueError("Для подтверждения передайте action_id из pending_action")
        before = self._trace_len(thread_id)
        started = time.monotonic()
        self.graph.invoke(Command(resume={"approved": approved, "action_id": action_id}), self._config(thread_id))
        return self._result(thread_id, before, started)

    def pending(self, thread_id: str) -> dict | None:
        snap = self.graph.get_state(self._config(thread_id))
        return snap.interrupts[0].value if snap.interrupts else None

    def _result(self, thread_id: str, trace_before: int, started: float) -> TurnResult:
        snap = self.graph.get_state(self._config(thread_id))
        v = snap.values
        trace = v.get("trace", [])[trace_before:]
        pending = snap.interrupts[0].value if snap.interrupts else None
        status = "needs_confirmation" if pending else v.get("status") or "done"
        answer = pending["preview"] if pending else (v.get("answer") or "")
        # Пользователю показываем его же данные вместо меток.
        vault = PiiVault.from_dict(v.get("pii_vault"))
        answer = vault.unmask(answer)
        if pending:
            pending = {**pending, "action": {k: vault.unmask(x) if isinstance(x, str) else x
                                             for k, x in pending["action"].items()}}  # fmt: skip
        return TurnResult(
            thread_id=thread_id,
            status=status,
            answer=answer,
            language=v.get("language", "ru"),
            route=v.get("route"),
            citations=v.get("citations") or [],
            pending_action=pending["action"] if pending else None,
            sql=_public_sql(v.get("sql_result")),
            handoff=v.get("handoff"),
            action_result=v.get("action_result"),
            trace=trace,
            usage=self._usage(trace),
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def _usage(self, trace: list[dict]) -> dict:
        total_in = sum((s.get("usage") or {}).get("input_tokens", 0) for s in trace)
        total_out = sum((s.get("usage") or {}).get("output_tokens", 0) for s in trace)
        cost = None
        if self._price:
            cost = round(total_in / 1e6 * self._price[0] + total_out / 1e6 * self._price[1], 6)
        return {"input_tokens": total_in, "output_tokens": total_out, "cost_usd": cost}


def _public_sql(sql_result: dict | None) -> dict | None:
    if not sql_result:
        return None
    keys = ("ok", "sql", "columns", "rows", "row_count", "truncated", "assumptions")
    return {k: sql_result.get(k) for k in keys}
