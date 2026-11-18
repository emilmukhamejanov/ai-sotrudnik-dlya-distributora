"""HTTP API агента.

    uvicorn dala_agent.api.main:create_app --factory --reload   (из каталога backend, PYTHONPATH=src)

POST /chat                       — сообщение пользователя → ответ (или запрос подтверждения действия)
POST /chat/{thread_id}/confirm   — «да» / «нет» для действия в Битрикс24
GET  /chat/{thread_id}/pending   — действие, ожидающее подтверждения
GET  /health                     — проверка
GET  /evals/cases                — эталонные вопросы
POST /evals/run                  — запустить прогон в фоне (тратит токены!)
GET  /evals/runs/{run_id}        — прогресс и отчёт
GET  /evals/reports              — сохранённые прогоны (var/evals)
GET  /meta                       — модели, цены, режим Битрикс24 — для шапки дашборда
/                                — дашборд (frontend/dist), если собран
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..agent.service import Agent
from ..config import REPO_ROOT, get_settings
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


class EvalRunRequest(BaseModel):
    case_ids: list[str] | None = None
    categories: list[str] | None = None


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

    eval_runs: dict[str, dict] = {}
    eval_lock = threading.Lock()

    @app.get("/evals/cases")
    def eval_cases() -> list[dict]:
        from ..evals import load_cases

        return [
            {
                "id": c["id"],
                "category": c["category"],
                "turns": [t.get("message", "[подтверждение]") for t in c["turns"]],
            }
            for c in load_cases()
        ]

    @app.post("/evals/run")
    def eval_run(req: EvalRunRequest, agent: AgentDep) -> dict:
        from ..evals import load_cases, run_evals, save_report, to_markdown

        with eval_lock:
            if any(r["status"] == "running" for r in eval_runs.values()):
                raise HTTPException(409, "Прогон evals уже идёт")
            cases = load_cases(ids=req.case_ids, categories=req.categories)
            if not cases:
                raise HTTPException(422, "Нет кейсов под такой фильтр")
            run_id = uuid.uuid4().hex[:8]
            state = {"run_id": run_id, "status": "running", "done": 0, "total": len(cases), "results": []}
            eval_runs[run_id] = state

        def progress(i, total, rep):
            state["done"] = i
            state["results"].append(
                {"id": rep.id, "passed": rep.passed, "failures": [f for t in rep.turns for f in t.failures]}
            )

        def work():
            try:
                run = run_evals(
                    agent.deps, agent.deps.sql_tool.executor, cases, pricing=agent.pricing, on_progress=progress
                )
                json_path, md_path = save_report(run)
                state.update(status="done", report=run, markdown=to_markdown(run), saved_to=str(md_path))
            except Exception as e:  # noqa: BLE001
                log.exception("Ошибка прогона evals")
                state.update(status="error", error=f"{type(e).__name__}: {e}")

        threading.Thread(target=work, name=f"evals-{run_id}", daemon=True).start()
        return {"run_id": run_id, "total": len(cases)}

    @app.get("/evals/runs/{run_id}")
    def eval_status(run_id: str) -> dict:
        if run_id not in eval_runs:
            raise HTTPException(404, "Нет такого прогона")
        return eval_runs[run_id]

    @app.get("/evals/reports")
    def eval_reports() -> list[dict]:
        from ..evals import runner

        out = []
        for path in sorted(runner.REPORTS_DIR.glob("*.json"), reverse=True):
            try:
                run = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append(
                {"name": path.stem, "started_at": run.get("started_at"), "model": run.get("model"),
                 "router_model": run.get("router_model"), "duration_s": run.get("duration_s"),
                 "summary": run.get("summary")}
            )  # fmt: skip
        return out

    @app.get("/evals/reports/{name}")
    def eval_report(name: str) -> dict:
        from ..evals import runner

        if not re.fullmatch(r"[0-9-]{8,20}", name):
            raise HTTPException(404, "Нет такого отчёта")
        path = runner.REPORTS_DIR / f"{name}.json"
        if not path.exists():
            raise HTTPException(404, "Нет такого отчёта")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get("/meta")
    def meta(agent: AgentDep) -> dict:
        deps = agent.deps
        retriever = getattr(deps.docs_tool, "retriever", None)
        schema = getattr(deps.sql_tool, "schema", None)
        return {
            "demo": True,
            "model": getattr(deps.llm, "model", None),
            "router_model": getattr(deps.router_llm, "model", None),
            "embeddings": getattr(getattr(retriever, "embedder", None), "name", None),
            "bitrix_mode": getattr(deps.bitrix, "mode", None),
            "export_date": getattr(schema, "export_date", None),
            "pricing": agent.pricing.as_dict(),
        }

    @app.get("/chat/{thread_id}/pending")
    def pending(thread_id: str, agent: AgentDep) -> dict:
        return {"thread_id": thread_id, "pending": agent.pending(thread_id)}

    # Дашборд: собранный фронтенд отдаётся тем же сервером — Node.js для запуска не нужен.
    # Монтируется последним, чтобы маршруты API имели приоритет.
    dist = REPO_ROOT / "frontend" / "dist"
    if (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
    return app
