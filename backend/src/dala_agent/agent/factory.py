"""Сборка зависимостей агента из настроек (БД, модели, эмбеддинги, Битрикс24)."""

from __future__ import annotations

from ..actions.bitrix import BitrixStub, BitrixWebhookClient
from ..actions.directory import EmployeeDirectory
from ..config import Settings
from ..db import AgentSQLExecutor
from ..embeddings import build_embedder
from ..llm import build_llms
from ..rag.answer import DocsTool
from ..rag.retriever import Retriever
from ..tools.schema_context import load_schema_context
from ..tools.text_to_sql import TextToSQLTool
from .graph import AgentDeps


def build_bitrix(settings: Settings):
    if settings.bitrix_mode == "webhook" and not settings.demo_mode:  # публичное демо — только заглушка
        if not settings.bitrix_webhook_url:
            raise ValueError("Для BITRIX_MODE=webhook задайте BITRIX_WEBHOOK_URL")
        return BitrixWebhookClient(settings.bitrix_webhook_url.get_secret_value())
    return BitrixStub(settings.bitrix_stub_log)


def build_deps(settings: Settings, *, llm=None, router_llm=None, bitrix=None, embedder=None) -> AgentDeps:
    if llm is None or router_llm is None:
        main, router = build_llms(settings)
        llm = llm or main
        router_llm = router_llm or router
    executor = AgentSQLExecutor(settings.agent_dsn())
    schema = load_schema_context(executor)
    retriever = Retriever(settings.service_dsn(), embedder or build_embedder(settings))
    return AgentDeps(
        llm=llm,
        router_llm=router_llm,
        sql_tool=TextToSQLTool(
            llm=llm,
            executor=executor,
            schema=schema,
            max_rows=settings.sql_max_rows,
            max_attempts=settings.sql_max_attempts,
        ),  # fmt: skip
        docs_tool=DocsTool(llm=llm, retriever=retriever, top_k=settings.rag_top_k),
        bitrix=bitrix or build_bitrix(settings),
        directory=EmployeeDirectory.from_db(executor),
    )
