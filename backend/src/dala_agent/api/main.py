"""HTTP API агента.

    uvicorn dala_agent.api.main:create_app --factory --reload   (из каталога backend, PYTHONPATH=src)

POST /chat                       — сообщение пользователя → ответ (или запрос подтверждения действия)
POST /chat/{thread_id}/confirm   — «да» / «нет» для действия в Битрикс24
GET  /chat/{thread_id}/pending   — действие, ожидающее подтверждения
GET  /health                     — проверка
"""

from __future__ import annotations

import logging
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ..agent.service import Agent
from ..config import get_settings
from ..llm import LLMError

log = logging.getLogger("dala_agent.api")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(None, max_length=100, description="Не задан — начнётся новый диалог")


class ConfirmRequest(BaseModel):
    approved: bool
    action_id: str | None = Field(
        None, description="id действия из pending_action; обязателен при approved=true — защита от устаревших «да»"
    )


class TurnResponse(BaseModel):
    thread_id: str
    status: Literal["done", "needs_confirmation", "needs_clarification"]
    answer: str
    language: str
    route: str | None
    citations: list[dict]
    pending_action: dict | None
    sql: dict | None
    handoff: dict | None
    action_result: dict | None
    trace: list[dict]
    usage: dict
    duration_ms: int


def get_agent(request: Request) -> Agent:
    return request.app.state.agent


AgentDep = Annotated[Agent, Depends(get_agent)]


def create_app(agent: Agent | None = None) -> FastAPI:
    """agent можно передать готовым (тесты); иначе он собирается из настроек при старте."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if agent is None:
            app.state.agent = Agent.from_settings(get_settings())
        else:
            app.state.agent = agent
        yield

    app = FastAPI(title="ИИ-сотрудник для дистрибьютора (демо)", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins if agent is None else ["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    # Ходы одного диалога выполняются по очереди: иначе два параллельных запроса перепишут состояние.
    locks: dict[str, threading.Lock] = {}
    locks_guard = threading.Lock()

    def thread_lock(thread_id: str) -> threading.Lock:
        with locks_guard:
            return locks.setdefault(thread_id, threading.Lock())

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/chat", response_model=TurnResponse)
    def chat(req: ChatRequest, agent: AgentDep) -> dict:
        thread_id = req.thread_id or uuid.uuid4().hex
        with thread_lock(thread_id):
            try:
                result = agent.chat(thread_id, req.message.strip())
            except LLMError as e:
                log.exception("Ошибка модели")
                raise HTTPException(502, f"Модель недоступна: {e}") from e
        return asdict(result)

    @app.post("/chat/{thread_id}/confirm", response_model=TurnResponse)
    def confirm(thread_id: str, req: ConfirmRequest, agent: AgentDep) -> dict:
        with thread_lock(thread_id):
            try:
                result = agent.confirm(thread_id, approved=req.approved, action_id=req.action_id)
            except LookupError as e:
                raise HTTPException(409, str(e)) from e
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
        return asdict(result)

    @app.get("/chat/{thread_id}/pending")
    def pending(thread_id: str, agent: AgentDep) -> dict:
        return {"thread_id": thread_id, "pending": agent.pending(thread_id)}

    return app
