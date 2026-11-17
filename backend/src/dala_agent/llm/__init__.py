"""Клиенты моделей и фабрика по настройкам."""

from __future__ import annotations

from ..config import Settings
from .base import LLMClient, LLMError, StructuredResult, TextResult, Usage
from .fake import FakeLLM

__all__ = ["FakeLLM", "LLMClient", "LLMError", "StructuredResult", "TextResult", "Usage", "build_llms"]


def build_llms(settings: Settings) -> tuple[LLMClient, LLMClient]:
    """Возвращает (основная модель, модель роутера). Роутеру хватает быстрой и дешёвой модели."""
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise LLMError("Не задан ANTHROPIC_API_KEY (заполните .env) или выберите LLM_PROVIDER=openai")
        from .anthropic_client import AnthropicLLM

        key = settings.anthropic_api_key.get_secret_value()
        main = AnthropicLLM(api_key=key, model=settings.anthropic_model, timeout_s=settings.llm_timeout_s)
        router = AnthropicLLM(api_key=key, model=settings.anthropic_router_model, timeout_s=settings.llm_timeout_s)
        return main, router

    if not settings.openai_model:
        raise LLMError("Для LLM_PROVIDER=openai задайте OPENAI_MODEL (и OPENAI_BASE_URL для self-hosted)")
    from .openai_client import OpenAICompatibleLLM

    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
    common = {"api_key": key, "base_url": settings.openai_base_url, "timeout_s": settings.llm_timeout_s}
    main = OpenAICompatibleLLM(model=settings.openai_model, **common)
    router = OpenAICompatibleLLM(model=settings.openai_router_model or settings.openai_model, **common)
    return main, router
