"""Детерминированная модель для тестов и офлайн-демо: ответы задаются по назначению вызова (purpose)."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from .base import LLMError, Message, StructuredResult, T, TextResult, Usage

Responder = Callable[[str, list[Message]], Any]


@dataclass
class Call:
    purpose: str
    system: str
    messages: list[Message]
    schema: str | None


class FakeLLM:
    """responses: purpose → ответ, список ответов (по очереди) или функция (system, messages) → ответ.

    Ответ для structured — экземпляр схемы или dict; для text — строка.
    """

    def __init__(self, responses: dict[str, Any] | None = None, model: str = "fake-llm"):
        self.model = model
        self.calls: list[Call] = []
        self._fixed: dict[str, Any] = {}
        self._queues: dict[str, deque] = defaultdict(deque)
        for purpose, value in (responses or {}).items():
            self.set(purpose, value)

    def set(self, purpose: str, value: Any) -> None:
        if isinstance(value, list):
            self._queues[purpose] = deque(value)
            self._fixed.pop(purpose, None)
        else:
            self._fixed[purpose] = value

    def _next(self, purpose: str, system: str, messages: list[Message]) -> Any:
        if self._queues.get(purpose):
            value = self._queues[purpose].popleft()
        elif purpose in self._fixed:
            value = self._fixed[purpose]
        else:
            raise LLMError(f"FakeLLM: нет ответа для purpose={purpose!r}")
        if callable(value) and not isinstance(value, BaseModel):
            value = value(system, messages)
        if isinstance(value, Exception):
            raise value
        return value

    def _usage(self, system: str, messages: list[Message], out: str) -> Usage:
        n_in = (len(system) + sum(len(str(m["content"])) for m in messages)) // 4
        return Usage(model=self.model, input_tokens=n_in, output_tokens=len(out) // 4)

    def text(self, *, system: str, messages: list[Message], purpose: str, max_tokens: int = 1024) -> TextResult:
        self.calls.append(Call(purpose, system, messages, None))
        value = str(self._next(purpose, system, messages))
        return TextResult(text=value, usage=self._usage(system, messages, value))

    def structured(
        self, *, system: str, messages: list[Message], schema: type[T], purpose: str, max_tokens: int = 1024
    ) -> StructuredResult[T]:
        self.calls.append(Call(purpose, system, messages, schema.__name__))
        value = self._next(purpose, system, messages)
        obj = value if isinstance(value, schema) else schema.model_validate(value)
        return StructuredResult(value=obj, usage=self._usage(system, messages, obj.model_dump_json()))

    def purposes(self) -> list[str]:
        return [c.purpose for c in self.calls]
