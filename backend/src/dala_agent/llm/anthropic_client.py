"""Claude через официальный SDK.

Структурированный ответ — Structured Outputs (output_config.format = json_schema): Claude возвращает
JSON строго по схеме Pydantic. Этот способ поддерживают все модели; принудительный вызов инструмента
(tool_choice: tool) новые модели вроде Claude Sonnet 5.5 не принимают.
"""

from __future__ import annotations

import anthropic
from pydantic import BaseModel, ValidationError

from .base import LLMError, Message, StructuredResult, T, TextResult, Usage


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
                model=self.model, system=system, messages=messages, max_tokens=max_tokens
            )
        except anthropic.APIError as e:
            raise LLMError(f"Claude API ({purpose}): {e}") from e
        text = "".join(block.text for block in resp.content if block.type == "text")
        return TextResult(text=text.strip(), usage=self._usage(resp))

    def structured(
        self, *, system: str, messages: list[Message], schema: type[T], purpose: str, max_tokens: int = 1024
    ) -> StructuredResult[T]:
        try:
            resp = self._client.messages.create(
                model=self.model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                output_config={"format": {"type": "json_schema", "schema": json_schema(schema)}},
            )
        except anthropic.APIError as e:
            raise LLMError(f"Claude API ({purpose}): {e}") from e
        if resp.stop_reason == "refusal":
            raise LLMError(f"Claude отказался отвечать ({purpose})")
        if resp.stop_reason == "max_tokens":
            raise LLMError(f"Ответ Claude обрезан по max_tokens ({purpose})")
        text = "".join(block.text for block in resp.content if block.type == "text")
        try:
            return StructuredResult(value=schema.model_validate_json(text), usage=self._usage(resp))
        except ValidationError as e:
            raise LLMError(f"Claude вернул ответ не по схеме ({purpose}): {e}") from e


def json_schema(schema: type[BaseModel]) -> dict:
    """Схема Pydantic в формате, который принимает Structured Outputs.

    SDK-хелпер добавляет additionalProperties: false и переносит неподдерживаемые ограничения в описания.
    """
    return anthropic.transform_schema(schema)
