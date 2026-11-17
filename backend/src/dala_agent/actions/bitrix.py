"""Действия в Битрикс24: заглушка (по умолчанию) и клиент входящего вебхука.

Сюда попадает только действие, которое пользователь подтвердил в интерфейсе: граф ставит
interrupt() перед выполнением, и продолжить его можно лишь вызовом /chat/{thread}/confirm.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

import httpx

ActionKind = Literal["create_task", "create_deal"]


@dataclass
class PlannedAction:
    kind: ActionKind
    title: str
    description: str = ""
    responsible_id: int | None = None  # id пользователя в Битрикс24
    responsible_name: str | None = None
    deadline: str | None = None  # ISO-дата
    amount_kzt: float | None = None
    client_name: str | None = None
    action_id: str = ""

    def preview(self, language: str = "ru") -> str:
        if language == "kk":
            if self.kind == "create_task":
                parts = [f"Битрикс24-те тапсырма құру: «{self.title}»"]
                if self.responsible_name:
                    parts.append(f"жауапты — {self.responsible_name}")
                if self.deadline:
                    parts.append(f"мерзімі — {self.deadline}")
            else:
                parts = [f"Битрикс24-те мәміле құру: «{self.title}»"]
                if self.amount_kzt is not None:
                    parts.append(f"сомасы — {self.amount_kzt:,.0f} ₸".replace(",", " "))
            return ", ".join(parts) + ". Растайсыз ба?"
        if self.kind == "create_task":
            parts = [f"Создать задачу в Битрикс24: «{self.title}»"]
            if self.responsible_name:
                parts.append(f"ответственный — {self.responsible_name}")
            if self.deadline:
                parts.append(f"срок — {self.deadline}")
        else:
            parts = [f"Создать сделку в Битрикс24: «{self.title}»"]
            if self.client_name:
                parts.append(f"клиент — {self.client_name}")
            if self.amount_kzt is not None:
                parts.append(f"сумма — {self.amount_kzt:,.0f} ₸".replace(",", " "))
        return ", ".join(parts) + ". Подтверждаете?"


@dataclass
class ActionResult:
    ok: bool
    external_id: str | None = None
    url: str | None = None
    error: str | None = None
    mode: str = "stub"


@dataclass
class HandoffTicket:
    ticket_id: str
    thread_id: str
    reason: str
    question: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))


class BitrixClient(Protocol):
    mode: str

    def execute(self, action: PlannedAction) -> ActionResult: ...

    def handoff(self, *, thread_id: str, reason: str, question: str) -> HandoffTicket: ...


class BitrixStub:
    """Записывает действия в память и (опционально) в JSONL-журнал — вместо реального портала."""

    mode = "stub"

    def __init__(self, log_path: Path | None = None):
        self.log_path = log_path
        self.records: list[dict] = []
        self._lock = threading.Lock()
        self._seq = {"task": 0, "deal": 0, "handoff": 0}

    def _write(self, record: dict) -> None:
        self.records.append(record)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def execute(self, action: PlannedAction) -> ActionResult:
        with self._lock:
            key = "task" if action.kind == "create_task" else "deal"
            self._seq[key] += 1
            ext_id = f"STUB-{key.upper()}-{self._seq[key]:04d}"
            self._write({"type": action.kind, "id": ext_id, "at": _now(), **asdict(action)})
        return ActionResult(ok=True, external_id=ext_id, mode=self.mode)

    def handoff(self, *, thread_id: str, reason: str, question: str) -> HandoffTicket:
        with self._lock:
            self._seq["handoff"] += 1
            ticket = HandoffTicket(f"H-{self._seq['handoff']:04d}", thread_id, reason, question)
            self._write({"type": "handoff", **asdict(ticket)})
        return ticket


class BitrixWebhookClient:
    """Реальный портал через входящий вебхук (https://<портал>/rest/<user>/<token>/).

    Задачи — tasks.task.add, сделки — crm.deal.add. Передача человеку — задача ответственному за линию.
    """

    mode = "webhook"

    def __init__(self, webhook_url: str, *, handoff_responsible_id: int = 1, timeout_s: float = 15.0):
        self._base = webhook_url.rstrip("/") + "/"
        self._handoff_responsible = handoff_responsible_id
        self._timeout = timeout_s

    def _call(self, method: str, payload: dict) -> dict:
        resp = httpx.post(self._base + method + ".json", json=payload, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"{data.get('error')}: {data.get('error_description', '')}")
        return data

    def execute(self, action: PlannedAction) -> ActionResult:
        try:
            if action.kind == "create_task":
                fields = {"TITLE": action.title, "DESCRIPTION": action.description}
                if action.responsible_id:
                    fields["RESPONSIBLE_ID"] = action.responsible_id
                if action.deadline:
                    fields["DEADLINE"] = action.deadline
                data = self._call("tasks.task.add", {"fields": fields})
                ext = str(data["result"]["task"]["id"])
            else:
                fields = {"TITLE": action.title, "COMMENTS": action.description, "CURRENCY_ID": "KZT"}
                if action.amount_kzt is not None:
                    fields["OPPORTUNITY"] = action.amount_kzt
                if action.responsible_id:
                    fields["ASSIGNED_BY_ID"] = action.responsible_id
                data = self._call("crm.deal.add", {"fields": fields})
                ext = str(data["result"])
        except (httpx.HTTPError, RuntimeError, KeyError) as e:
            return ActionResult(ok=False, error=str(e), mode=self.mode)
        return ActionResult(ok=True, external_id=ext, mode=self.mode)

    def handoff(self, *, thread_id: str, reason: str, question: str) -> HandoffTicket:
        fields = {
            "TITLE": f"Вопрос от ИИ-сотрудника: {question[:80]}",
            "DESCRIPTION": f"Причина передачи: {reason}\nДиалог: {thread_id}\n\n{question}",
            "RESPONSIBLE_ID": self._handoff_responsible,
        }
        data = self._call("tasks.task.add", {"fields": fields})
        return HandoffTicket(f"T-{data['result']['task']['id']}", thread_id, reason, question)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
