"""OpenAI-совместимый API: облачные модели или self-hosted (vLLM, Ollama, LM Studio).

Структурированный ответ — принудительный function calling; если сервер его не поддерживает,
повторяем запрос в режиме JSON со схемой в системном промпте.
"""

from __future__ import annotations

import json
import re

import openai
from pydantic import ValidationError

from .base import LLMError, Message, StructuredResult, T, TextResult, Usage, tool_schema


class OpenAICompatibleLLM:
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None,
        base_url: str | None,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        http_client=None,
    ):
        self.model = model
        self._client = openai.OpenAI(
            api_key=api_key or "not-needed",
            base_url=base_url,
            timeout=timeout_s,
            max_retries=max_retries,
            http_client=http_client,
        )

    def _usage(self, resp) -> Usage:
        u = getattr(resp, "usage", None)
        return Usage(
            model=self.model,
            input_tokens=getattr(u, "prompt_tokens", 0) or 0,
            output_tokens=getattr(u, "completion_tokens", 0) or 0,
        )

    def text(self, *, system: str, messages: list[Message], purpose: str, max_tokens: int = 1024) -> TextResult:
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, *messages],
                max_tokens=max_tokens,
                temperature=0,
            )
        except openai.APIError as e:
            raise LLMError(f"OpenAI-совместимый API ({purpose}): {e}") from e
        return TextResult(text=(resp.choices[0].message.content or "").strip(), usage=self._usage(resp))

    def structured(
        self, *, system: str, messages: list[Message], schema: type[T], purpose: str, max_tokens: int = 1024
    ) -> StructuredResult[T]:
        name = schema.__name__
        params = tool_schema(schema)
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, *messages],
                max_tokens=max_tokens,
                temperature=0,
                tools=[{"type": "function", "function": {"name": name, "parameters": params}}],
                tool_choice={"type": "function", "function": {"name": name}},
            )
            calls = resp.choices[0].message.tool_calls or []
            if calls:
                return StructuredResult(
                    value=schema.model_validate_json(calls[0].function.arguments), usage=self._usage(resp)
                )
        except (openai.BadRequestError, ValidationError):
            pass  # сервер без function calling или кривые аргументы — пробуем JSON-режим
        except openai.APIError as e:
            raise LLMError(f"OpenAI-совместимый API ({purpose}): {e}") from e

        json_system = (
            f"{system}\n\nОтветь ОДНИМ JSON-объектом без пояснений, строго по JSON Schema:\n"
            f"{json.dumps(params, ensure_ascii=False)}"
        )
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": json_system}, *messages],
                max_tokens=max_tokens,
                temperature=0,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or ""
            return StructuredResult(value=schema.model_validate_json(_extract_json(raw)), usage=self._usage(resp))
        except ValidationError as e:
            raise LLMError(f"Модель вернула JSON не по схеме ({purpose}): {e}") from e
        except openai.APIError as e:
            raise LLMError(f"OpenAI-совместимый API ({purpose}): {e}") from e


def _extract_json(raw: str) -> str:
    """Self-hosted модели иногда оборачивают JSON в ```json … ``` или добавляют <think>…</think>."""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    return m.group(0) if m else raw
