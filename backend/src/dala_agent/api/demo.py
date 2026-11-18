"""Режим публичного демо: чтобы ссылку можно было дать работодателю, не рискуя ключом.

  * код доступа (заголовок X-Access-Code) — без него чат не работает;
  * лимит запросов с одного IP в час;
  * дневной бюджет на модель в долларах — по фактической стоимости ответов; после него чат отвечает 429;
  * запуск evals через API выключен (прогон стоит ≈ $0,6).

Счётчики в памяти процесса: для одного инстанса демо этого достаточно, при нескольких — Redis.
"""

from __future__ import annotations

import hmac
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import HTTPException, Request


@dataclass
class DemoGuard:
    enabled: bool = False
    access_code: str | None = None
    daily_budget_usd: float = 2.0
    requests_per_hour: int = 30
    _spent: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    _hits: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).date().isoformat()

    @staticmethod
    def client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def check(self, request: Request) -> None:
        """Вызывается перед каждым обращением к модели."""
        if not self.enabled:
            return
        if self.access_code:
            given = request.headers.get("x-access-code", "")
            if not hmac.compare_digest(given.encode(), self.access_code.encode()):
                raise HTTPException(401, "Нужен код доступа к демо")
        with self._lock:
            if self._spent[self._today()] >= self.daily_budget_usd:
                raise HTTPException(429, "Дневной лимит демо исчерпан — попробуйте завтра")
            now = time.monotonic()
            hits = self._hits[self.client_ip(request)]
            while hits and now - hits[0] > 3600:
                hits.popleft()
            if len(hits) >= self.requests_per_hour:
                raise HTTPException(429, "Слишком много вопросов за час — попробуйте позже")
            hits.append(now)

    def record(self, cost_usd: float | None, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """Учитывает стоимость ответа. Нет цены у модели — считаем по тарифу Sonnet, чтобы лимит не обходился."""
        if not self.enabled:
            return
        if cost_usd is None:
            cost_usd = input_tokens / 1e6 * 2.0 + output_tokens / 1e6 * 10.0
        with self._lock:
            self._spent[self._today()] += cost_usd

    def status(self) -> dict:
        if not self.enabled:
            return {"enabled": False}
        spent = self._spent[self._today()]
        return {
            "enabled": True,
            "access_code_required": bool(self.access_code),
            "daily_budget_usd": self.daily_budget_usd,
            "spent_today_usd": round(spent, 4),
            "remaining_today_usd": round(max(0.0, self.daily_budget_usd - spent), 4),
            "requests_per_hour": self.requests_per_hour,
        }
