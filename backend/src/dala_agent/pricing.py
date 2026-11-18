"""Стоимость вызовов по моделям — чтобы трасса и панель расходов показывали деньги без настройки.

Цены — USD за 1 млн токенов, по странице цен Claude Platform на 18.11.2026 (Haiku 5.5 — тариф для
запросов до 100 тыс. токенов; наши запросы — 1–10 тыс.). LLM_PRICE_INPUT_PER_MTOK / OUTPUT в .env
переопределяют цену для всех моделей (например, для self-hosted или после смены тарифов).
"""

from __future__ import annotations

DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}


class Pricing:
    def __init__(
        self, override: tuple[float, float] | None = None, table: dict[str, tuple[float, float]] | None = None
    ):
        self.override = override
        self.table = DEFAULT_PRICES if table is None else table

    def price(self, model: str | None) -> tuple[float, float] | None:
        if self.override:
            return self.override
        return self.table.get(model or "")

    def cost(self, model: str | None, input_tokens: int, output_tokens: int) -> float | None:
        p = self.price(model)
        if p is None:
            return None
        return input_tokens / 1e6 * p[0] + output_tokens / 1e6 * p[1]

    def cost_of_steps(self, steps: list[dict]) -> float | None:
        """Сумма по шагам трассы; None — если хотя бы у одной использованной модели нет цены."""
        total = 0.0
        for s in steps:
            u = s.get("usage") or {}
            if not (u.get("input_tokens") or u.get("output_tokens")):
                continue
            c = self.cost(u.get("model"), u.get("input_tokens", 0), u.get("output_tokens", 0))
            if c is None:
                return None
            total += c
        return round(total, 6)

    def as_dict(self) -> dict:
        return {
            "override": list(self.override) if self.override else None,
            "models": {m: {"input": p[0], "output": p[1]} for m, p in self.table.items()},
        }
