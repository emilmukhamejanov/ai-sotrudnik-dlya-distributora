"""Состояние графа. Поля одного хода сбрасываются роутером; история и трасса накапливаются."""

from __future__ import annotations

import operator
import re
import time
from typing import Annotated, Any, Literal, TypedDict

from ..llm.base import Usage

Route = Literal["data", "docs", "data_and_docs", "action", "human", "smalltalk"]

TURN_FIELDS_RESET: dict[str, Any] = {
    "route": None,
    "route_reason": None,
    "sql_result": None,
    "docs_result": None,
    "pending_action": None,
    "action_result": None,
    "handoff": None,
    "answer": None,
    "citations": [],
    "status": "done",
}


class AgentState(TypedDict, total=False):
    messages: Annotated[list[dict], operator.add]  # история диалога: {"role", "content"}
    trace: Annotated[list[dict], operator.add]  # шаги агента для дашборда (этап 4)
    question: str
    language: Literal["ru", "kk"]
    route: Route | None
    route_reason: str | None
    sql_result: dict | None
    docs_result: dict | None
    pending_action: dict | None
    action_result: dict | None
    handoff: dict | None
    answer: str | None
    citations: list[dict]
    status: Literal["done", "needs_confirmation", "needs_clarification"]


_KK_LETTERS = re.compile(r"[әғқңөұүһі]", re.I)


def detect_language(text: str) -> Literal["ru", "kk"] | None:
    """Казахские буквы однозначно указывают на казахский; иначе решает роутер."""
    return "kk" if _KK_LETTERS.search(text or "") else None


def step(node: str, title: str, started: float, usage: Usage | None = None, **detail: Any) -> dict:
    return {
        "node": node,
        "title": title,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "usage": usage.as_dict() if usage else None,
        "detail": detail,
    }
