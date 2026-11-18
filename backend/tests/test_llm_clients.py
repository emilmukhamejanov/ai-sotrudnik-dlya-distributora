"""Клиенты моделей без сети: формат запросов и разбор ответов через подменённый HTTP-транспорт."""

from __future__ import annotations

import json

import httpx
import pytest

from dala_agent.agent.prompts import ActionDraft, RouteDecision
from dala_agent.config import Settings
from dala_agent.llm import LLMError, build_llms
from dala_agent.llm.anthropic_client import AnthropicLLM, json_schema
from dala_agent.llm.openai_client import OpenAICompatibleLLM
from dala_agent.rag.answer import DocsAnswer
from dala_agent.tools.text_to_sql import SQLDraft


def transport(handler, lib=httpx):
    """lib — пакет HTTP-клиента SDK: openai использует httpx, свежий anthropic — httpx2."""
    requests: list[dict] = []

    def wrapped(request):
        body = json.loads(request.content)
        requests.append(body)
        resp = handler(body)
        return lib.Response(resp.status_code, content=resp.content, headers={"content-type": "application/json"})

    return lib.Client(transport=lib.MockTransport(wrapped)), requests


def anthropic_http():
    try:
        import httpx2

        return httpx2
    except ImportError:  # anthropic < 2 работает на httpx
        return httpx


def test_anthropic_structured_uses_output_config():
    """Structured Outputs (output_config.format), а не принудительный вызов инструмента:
    tool_choice "tool" Claude Sonnet 5.5 отклоняет с ошибкой 400."""

    def handler(body):
        return httpx.Response(
            200,
            json={
                "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
                "stop_reason": "end_turn", "stop_sequence": None,
                "content": [{"type": "text", "text": '{"route": "docs", "language": "ru", "reason": "правила"}'}],
                "usage": {"input_tokens": 120, "output_tokens": 15},
            },
        )  # fmt: skip

    client, sent = transport(handler, anthropic_http())
    llm = AnthropicLLM(api_key="test", model="claude-sonnet-5-5", http_client=client)
    res = llm.structured(
        system="s", messages=[{"role": "user", "content": "q"}], schema=RouteDecision, purpose="router"
    )
    assert res.value.route == "docs" and res.usage.input_tokens == 120
    req = sent[0]
    assert "tools" not in req and "tool_choice" not in req
    fmt = req["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["properties"]["route"]["enum"][0] == "data"
    assert req["system"] == "s"


@pytest.mark.parametrize("schema", [RouteDecision, ActionDraft, DocsAnswer, SQLDraft])
def test_all_schemas_are_strict(schema):
    """Каждый объект в схеме закрыт (additionalProperties: false) — требование Structured Outputs."""

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, node.get("title")
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(json_schema(schema))


def test_anthropic_refusal_is_error():
    client, _ = transport(
        lambda body: httpx.Response(
            200,
            json={
                "id": "m", "type": "message", "role": "assistant", "model": body["model"], "stop_reason": "refusal",
                "stop_sequence": None, "content": [], "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        ),
        anthropic_http(),
    )  # fmt: skip
    with pytest.raises(LLMError, match="отказался"):
        AnthropicLLM(api_key="k", model="m", http_client=client).structured(
            system="s", messages=[], schema=RouteDecision, purpose="router"
        )


def test_anthropic_text_and_error():
    client, _ = transport(
        lambda body: httpx.Response(
            200,
            json={
                "id": "m", "type": "message", "role": "assistant", "model": body["model"], "stop_reason": "end_turn",
                "stop_sequence": None, "content": [{"type": "text", "text": " Ответ "}],
                "usage": {"input_tokens": 1, "output_tokens": 2},
            },
        ),
        anthropic_http(),
    )  # fmt: skip
    assert AnthropicLLM(api_key="k", model="m", http_client=client).text(system="s", messages=[], purpose="p").text == (
        "Ответ"
    )

    failing, _ = transport(
        lambda body: httpx.Response(401, json={"type": "error", "error": {"type": "auth"}}), anthropic_http()
    )
    with pytest.raises(LLMError):
        AnthropicLLM(api_key="bad", model="m", http_client=failing, max_retries=0).text(
            system="s", messages=[], purpose="p"
        )


def test_openai_falls_back_to_json_mode():
    """Self-hosted сервер без function calling: второй запрос — JSON-режим, ответ с <think> и ```json."""

    def handler(body):
        if "tools" in body:
            return httpx.Response(400, json={"error": {"message": "tools not supported"}})
        content = '<think>…</think>```json\n{"route": "data", "language": "kk", "reason": "сандар"}\n```'
        return httpx.Response(
            200,
            json={
                "id": "c", "object": "chat.completion", "created": 0, "model": body["model"],
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
            },
        )  # fmt: skip

    client, sent = transport(handler)
    llm = OpenAICompatibleLLM(model="qwen", api_key=None, base_url="http://vllm/v1", http_client=client, max_retries=0)
    res = llm.structured(system="s", messages=[{"role": "user", "content": "q"}], schema=RouteDecision, purpose="r")
    assert res.value.language == "kk" and res.usage.output_tokens == 10
    assert sent[1]["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in sent[1]["messages"][0]["content"]


def test_build_llms_requires_keys():
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        build_llms(Settings(_env_file=None))
    with pytest.raises(LLMError, match="OPENAI_MODEL"):
        build_llms(Settings(_env_file=None, llm_provider="openai"))


def test_dsn_building():
    s = Settings(_env_file=None, postgres_password="p@ss", agent_db_password="a/b")
    assert s.service_dsn() == "postgresql://dala:p%40ss@127.0.0.1:5432/dala"
    assert s.agent_dsn() == "postgresql://agent_reader:a%2Fb@127.0.0.1:5432/dala"
    ext = Settings(_env_file=None, database_url="postgresql://u:x@db.example:6543/postgres?sslmode=require",
                   agent_db_password="z")  # fmt: skip
    assert ext.agent_dsn() == "postgresql://agent_reader:z@db.example:6543/postgres?sslmode=require"
