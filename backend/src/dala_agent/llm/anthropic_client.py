"""Claude через официальный SDK. Структурированный ответ — принудительный вызов инструмента."""

from __future__ import annotations

import anthropic
from pydantic import BaseModel, ValidationError

from .base import LLMError, Message, StructuredResult, T, TextResult, Usage, tool_schema


class AnthropicLLM:
    def __init__(self, *, api_key: str, model: str, timeout_s: float = 60.0, max_retries: int = 2, http_client=None):
        self.model = model
        self._client = anthropic.Anthropic(
            api_key=api_key, timeout=timeout_s, max_retries=max_retries, http_client=http_client
        )

    def _usage(self, resp) -> Usage:
        u = getattr(resp, "usage", None)
        return Usage(
            model=self.model,
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
        )

    def text(self, *, system: str, messages: list[Message], purpose: str, max_tokens: int = 1024) -> TextResult:
        try:
            resp = self._client.messages.create(
                model=self.model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                metadata={"user_id": f"dala-agent:{purpose}"},
            )
        except anthropic.APIError as e:
            raise LLMError(f"Claude API ({purpose}): {e}") from e
        text = "".join(block.text for block in resp.content if block.type == "text")
        return TextResult(text=text.strip(), usage=self._usage(resp))

    def structured(
        self, *, system: str, messages: list[Message], schema: type[T], purpose: str, max_tokens: int = 1024
    ) -> StructuredResult[T]:
        tool_name = _tool_name(schema)
        try:
            resp = self._client.messages.create(
                model=self.model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                tools=[
                    {
                        "name": tool_name,
                        "description": (schema.__doc__ or "Ответ в структурированном виде").strip(),
                        "input_schema": tool_schema(schema),
                    }
                ],
                tool_choice={"type": "tool", "name": tool_name},
                metadata={"user_id": f"dala-agent:{purpose}"},
            )
        except anthropic.APIError as e:
            raise LLMError(f"Claude API ({purpose}): {e}") from e
        for block in resp.content:
            if block.type == "tool_use" and block.name == tool_name:
                try:
                    return StructuredResult(value=schema.model_validate(block.input), usage=self._usage(resp))
                except ValidationError as e:
                    raise LLMError(f"Claude вернул ответ не по схеме ({purpose}): {e}") from e
        raise LLMError(f"Claude не вернул структурированный ответ ({purpose})")


def _tool_name(schema: type[BaseModel]) -> str:
    return "submit_" + "".join("_" + c.lower() if c.isupper() else c for c in schema.__name__).lstrip("_")
