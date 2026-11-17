"""Подключения к Postgres. Text-to-SQL ходит только под ролью агента и только в read-only транзакции."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool


class AgentSQLExecutor:
    """Выполняет уже проверенный SELECT под ролью agent_reader.

    Даже если проверка в коде пропустит что-то лишнее, база не даст писать (READ ONLY),
    читать erp/kb (нет USAGE) и держать соединение дольше statement_timeout.
    """

    def __init__(self, dsn: str, *, statement_timeout_ms: int = 5000, connect_timeout_s: int = 5):
        self._dsn = dsn
        self._timeout_ms = statement_timeout_ms
        self._connect_timeout = connect_timeout_s

    def connect(self) -> psycopg.Connection:
        conn = psycopg.connect(self._dsn, connect_timeout=self._connect_timeout, application_name="dala-agent-sql")
        conn.read_only = True
        return conn

    def run(self, sql: str, *, max_rows: int) -> QueryResult:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = {int(self._timeout_ms)}")
            cur.execute(sql)
            columns = [d.name for d in cur.description or []]
            rows = cur.fetchmany(max_rows + 1)
            conn.rollback()
        return QueryResult(columns=columns, rows=rows[:max_rows], truncated=len(rows) > max_rows)

    def fetch(self, sql: str, params: tuple | dict | None = None) -> list[tuple]:
        """Фиксированные запросы из кода (описание схемы, справочники)."""
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            conn.rollback()
        return rows
