"""Инструмент «вопрос словами → SQL → таблица».

Цикл: модель пишет SELECT → проверка sql_guard → выполнение под agent_reader. Если проверка или база
вернули ошибку, текст ошибки уходит модели на исправление (до sql_max_attempts попыток).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from pydantic import BaseModel, Field

from ..llm.base import LLMClient, LLMError, Message, Usage
from .schema_context import SchemaContext
from .sql_guard import SQLRejected, check_sql


class SQLDraft(BaseModel):
    """SQL-запрос к схеме analytics, отвечающий на вопрос пользователя."""

    sql: str | None = Field(None, description="Один SELECT для PostgreSQL 16 или null, если данных для ответа нет")
    assumptions: list[str] = Field(
        default_factory=list, description="Допущения, которые нужно сообщить пользователю (период, что считали)"
    )
    cannot_answer: str | None = Field(None, description="Если ответить по данным нельзя — почему (кратко)")


SQL_SYSTEM = """Ты аналитик дистрибьютора продуктов питания ТОО «Дала Дистрибьюшн» (ДЕМО-данные).
Пишешь один SELECT для PostgreSQL 16 по схеме analytics, чтобы ответить на вопрос пользователя.

Правила данных:
- «Сегодня» — дата выгрузки {export_date}, а не CURRENT_DATE. Данные за период {period}.
  «За октябрь» без года — последний завершённый октябрь в данных; «прошлый год» — тот же период годом раньше.
- Ноябрь 2026 — неполный месяц (по 14.11). Если сравниваешь его с полными месяцами — добавь это в assumptions.
- Суммы в тенге (₸) с НДС. Выручка = SUM(amount_kzt) из analytics.sales или analytics.sales_documents;
  возвраты там уже со знаком минус, отдельно вычитать их не нужно. Без НДС — amount_net_kzt.
- Для месяцев используй колонку doc_month. «В городе» по умолчанию — город клиента (client_city / clients.city).
- С 2026 года НДС 16% (в 2025 — 12%), цены выше в среднем на 12%: при сравнении год к году это стоит упомянуть.
- Только представления из описания ниже; ПДн (ИИН, телефоны, адреса) в них нет и запрашивать их нельзя.
- Пиши понятные псевдонимы колонок по-русски в двойных кавычках, округляй деньги (ROUND(…, 0)).
- Не больше {max_rows} строк результата; для рейтингов используй ORDER BY … LIMIT.
- Если вопрос не про данные из схемы — верни sql = null и cannot_answer.

{schema}

Пример. Вопрос: «Сколько продали напитков в Алматы за октябрь и как это к прошлому году?»
SQL:
SELECT EXTRACT(YEAR FROM doc_month)::int AS "год", ROUND(SUM(amount_kzt), 0) AS "выручка, ₸"
FROM analytics.sales
WHERE category = 'Напитки' AND client_city = 'Алматы' AND doc_month IN ('2026-10-01', '2025-10-01')
GROUP BY 1 ORDER BY 1
"""


@dataclass
class SQLAttempt:
    sql: str | None
    error: str | None = None
    checked_sql: str | None = None


@dataclass
class SQLToolResult:
    question: str
    ok: bool
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False
    assumptions: list[str] = field(default_factory=list)
    cannot_answer: str | None = None
    error: str | None = None
    attempts: list[SQLAttempt] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    duration_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "sql": self.sql,
            "columns": self.columns,
            "rows": self.rows,
            "row_count": len(self.rows),
            "truncated": self.truncated,
            "assumptions": self.assumptions,
            "cannot_answer": self.cannot_answer,
            "error": self.error,
            "attempts": [a.__dict__ for a in self.attempts],
        }

    def to_markdown(self, limit: int = 50) -> str:
        if not self.columns:
            return "(нет колонок)"
        head = "| " + " | ".join(self.columns) + " |\n|" + "---|" * len(self.columns)
        body = ["| " + " | ".join(_fmt(v) for v in row) + " |" for row in self.rows[:limit]]
        more = f"\n… ещё {len(self.rows) - limit} строк" if len(self.rows) > limit else ""
        cut = "\n(результат обрезан по лимиту строк)" if self.truncated else ""
        return "\n".join([head, *body]) + more + cut if body else head + "\n(пустой результат)"


class TextToSQLTool:
    def __init__(self, *, llm: LLMClient, executor, schema: SchemaContext, max_rows: int = 200, max_attempts: int = 2):
        self.llm = llm
        self.executor = executor
        self.schema = schema
        self.max_rows = max_rows
        self.max_attempts = max_attempts

    def system_prompt(self) -> str:
        return SQL_SYSTEM.format(
            export_date=self.schema.export_date or "дата из analytics.export_info",
            period=self.schema.period or "см. analytics.export_info",
            max_rows=self.max_rows,
            schema=self.schema.render(),
        )

    def run(self, question: str, *, history: list[Message] | None = None, rules_context: str = "") -> SQLToolResult:
        started = time.monotonic()
        result = SQLToolResult(question=question, ok=False)
        user = question if not rules_context else f"{question}\n\nПравила компании из документов:\n{rules_context}"
        messages: list[Message] = [*(history or []), {"role": "user", "content": user}]

        for _ in range(self.max_attempts):
            try:
                draft = self.llm.structured(
                    system=self.system_prompt(), messages=messages, schema=SQLDraft, purpose="sql_generate"
                )
            except LLMError as e:
                result.error = str(e)
                break
            result.usage = result.usage + draft.usage
            d = draft.value
            result.assumptions = d.assumptions
            if not d.sql:
                result.cannot_answer = d.cannot_answer or "По данным выгрузки на этот вопрос не ответить"
                result.attempts.append(SQLAttempt(sql=None, error=result.cannot_answer))
                break
            attempt = SQLAttempt(sql=d.sql)
            result.attempts.append(attempt)
            try:
                checked = check_sql(d.sql, max_rows=self.max_rows)
                attempt.checked_sql = checked.sql
                qr = self.executor.run(checked.sql, max_rows=self.max_rows)
            except SQLRejected as e:
                attempt.error = f"Проверка безопасности: {e}"
            except psycopg.Error as e:
                attempt.error = f"Ошибка PostgreSQL: {_pg_error(e)}"
            else:
                result.ok = True
                result.sql = checked.sql
                result.columns = qr.columns
                result.rows = [[_jsonable(v) for v in row] for row in qr.rows]
                result.truncated = qr.truncated
                result.error = None
                break
            result.error = attempt.error
            messages = [
                *messages,
                {"role": "assistant", "content": f"SQL:\n{d.sql}"},
                {"role": "user", "content": f"{attempt.error}\nИсправь запрос. Верни один корректный SELECT."},
            ]
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result


def _pg_error(e: psycopg.Error) -> str:
    diag = getattr(e, "diag", None)
    msg = (diag.message_primary if diag and diag.message_primary else str(e)).strip()
    hint = diag.message_hint if diag and diag.message_hint else None
    return msg + (f" (подсказка: {hint})" if hint else "")


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    return v


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}".replace(",", " ")
    if isinstance(v, int) and not isinstance(v, bool) and abs(v) >= 10_000:  # годы и коды не трогаем
        return f"{v:,}".replace(",", " ")
    return "" if v is None else str(v)
