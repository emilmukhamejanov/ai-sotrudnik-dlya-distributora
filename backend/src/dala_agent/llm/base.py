"""Общий интерфейс к моделям: Claude или любой OpenAI-совместимый API (в т.ч. self-hosted).

Узлы графа знают только этот интерфейс, поэтому провайдера можно сменить настройкой,
а в тестах подставить FakeLLM без сети.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass
class Usage:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            model=self.model or other.model,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )

    def as_dict(self) -> dict:
        return {"model": self.model, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


@dataclass
class TextResult:
    text: str
    usage: Usage = field(default_factory=Usage)


@dataclass
class StructuredResult[M: BaseModel]:
    value: M
    usage: Usage = field(default_factory=Usage)


Message = dict  # {"role": "user" | "assistant", "content": str}


class LLMError(RuntimeError):
    """Ошибка провайдера или ответ, который не удалось разобрать."""


class LLMClient(Protocol):
    model: str

    def text(self, *, system: str, messages: list[Message], purpose: str, max_tokens: int = 1024) -> TextResult: ...

    def structured(
        self, *, system: str, messages: list[Message], schema: type[T], purpose: str, max_tokens: int = 1024
    ) -> StructuredResult[T]: ...


def tool_schema(schema: type[BaseModel]) -> dict:
    """JSON Schema модели без лишних полей (title), пригодная для tool use / function calling."""
    js = schema.model_json_schema()
    js.pop("title", None)
    return js
