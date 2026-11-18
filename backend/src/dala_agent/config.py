"""Настройки из переменных окружения и файла .env (ключи в код не попадают)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env", ".env"), env_file_encoding="utf-8", extra="ignore")

    # --- База данных ---
    # Сервисное подключение: индексация документов и поиск по kb (запросы фиксированы в коде).
    database_url: str | None = None
    # Подключение для text-to-SQL: роль agent_reader (только чтение analytics). SQL от модели идёт только сюда.
    agent_database_url: str | None = None
    postgres_user: str = "dala"
    postgres_password: SecretStr = SecretStr("")
    postgres_db: str = "dala"
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    agent_db_user: str = "agent_reader"
    agent_db_password: SecretStr = SecretStr("")

    # --- Модели ---
    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    anthropic_router_model: str = "claude-haiku-5-5"
    openai_base_url: str | None = None  # OpenAI-совместимый API, например self-hosted vLLM
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None  # например, Qwen/Qwen3-32B для vLLM — задаётся явно
    openai_router_model: str | None = None
    llm_timeout_s: float = 60.0
    # Цены за 1 млн токенов (USD) — для оценки стоимости в трассе; если не заданы, считаются только токены.
    llm_price_input_per_mtok: float | None = None
    llm_price_output_per_mtok: float | None = None

    # --- Эмбеддинги (размерность фиксирована схемой kb: vector(1024)) ---
    # hashing — офлайн-заглушка по символьным n-граммам, без ключей (качество ниже, но поиск работает);
    # openai — любой OpenAI-совместимый endpoint (bge-m3 через vLLM/TEI, text-embedding-3-small c dimensions=1024);
    # voyage — Voyage AI (voyage-3.5, output_dimension=1024).
    embeddings_provider: Literal["hashing", "openai", "voyage"] = "hashing"
    embeddings_model: str | None = None
    embeddings_base_url: str | None = None
    embeddings_api_key: SecretStr | None = None
    voyage_api_key: SecretStr | None = None

    # --- Инструменты ---
    sql_max_rows: int = Field(200, ge=1, le=5000)
    sql_max_attempts: int = Field(3, ge=1, le=4)
    rag_top_k: int = Field(8, ge=1, le=20)

    # --- Битрикс24 ---
    # stub — заглушка (действия пишутся в журнал), webhook — реальный REST через входящий вебхук.
    bitrix_mode: Literal["stub", "webhook"] = "stub"
    bitrix_webhook_url: SecretStr | None = None
    bitrix_stub_log: Path = REPO_ROOT / "var" / "bitrix_stub.jsonl"

    # --- API ---
    cors_origins: list[str] = ["http://localhost:5173"]

    # --- Публичное демо (см. api/demo.py) ---
    demo_mode: bool = False
    demo_access_code: SecretStr | None = None
    demo_daily_budget_usd: float = Field(2.0, ge=0)
    demo_requests_per_hour: int = Field(30, ge=1)
    evals_api_enabled: bool | None = None  # по умолчанию: выключен в демо-режиме, включён локально

    def evals_enabled(self) -> bool:
        return (not self.demo_mode) if self.evals_api_enabled is None else self.evals_api_enabled

    def service_dsn(self) -> str:
        if self.database_url:
            return self.database_url
        return _dsn(self.postgres_user, self.postgres_password, self.db_host, self.db_port, self.postgres_db)

    def agent_dsn(self) -> str:
        if self.agent_database_url:
            return self.agent_database_url
        if self.database_url:  # та же база, но под ролью агента
            parts = urlsplit(self.database_url)
            host = parts.hostname or self.db_host
            port = parts.port or self.db_port
            db = parts.path.lstrip("/") or self.postgres_db
            return _dsn(self.agent_db_user, self.agent_db_password, host, port, db, query=parts.query)
        return _dsn(self.agent_db_user, self.agent_db_password, self.db_host, self.db_port, self.postgres_db)

    def price(self) -> tuple[float, float] | None:
        if self.llm_price_input_per_mtok is None or self.llm_price_output_per_mtok is None:
            return None
        return self.llm_price_input_per_mtok, self.llm_price_output_per_mtok


def _dsn(user: str, password: SecretStr, host: str, port: int, db: str, query: str = "") -> str:
    secret = password.get_secret_value()
    auth = quote(user, safe="") + (":" + quote(secret, safe="") if secret else "")
    return urlunsplit(("postgresql", f"{auth}@{host}:{port}", f"/{db}", query, ""))


@lru_cache
def get_settings() -> Settings:
    return Settings()
