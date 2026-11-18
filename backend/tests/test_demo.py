"""Режим публичного демо: код доступа, лимит запросов, дневной бюджет, запрет evals."""

from __future__ import annotations

from conftest import route
from fastapi.testclient import TestClient

from dala_agent.api.demo import DemoGuard
from dala_agent.api.main import create_app


def client_for(agent, **guard):
    return TestClient(create_app(agent, demo=DemoGuard(enabled=True, **guard), evals_enabled=False))


def test_access_code(make_agent, fake_llm):
    fake_llm.set("router", route("smalltalk"))
    with client_for(make_agent(), access_code="dala-2026") as c:
        assert c.post("/chat", json={"message": "Привет"}).status_code == 401
        bad = c.post("/chat", json={"message": "Привет"}, headers={"X-Access-Code": "wrong"})
        assert bad.status_code == 401 and bad.json()["detail"] == "Нужен код доступа к демо"
        ok = c.post("/chat", json={"message": "Привет"}, headers={"X-Access-Code": "dala-2026"})
        assert ok.status_code == 200
        assert c.get("/meta").json()["demo_mode"]["access_code_required"] is True  # /meta открыт


def test_rate_limit_per_ip(make_agent, fake_llm):
    fake_llm.set("router", route("smalltalk"))
    with client_for(make_agent(), requests_per_hour=2) as c:
        assert c.post("/chat", json={"message": "1"}).status_code == 200
        assert c.post("/chat", json={"message": "2"}).status_code == 200
        r = c.post("/chat", json={"message": "3"})
        assert r.status_code == 429 and "за час" in r.json()["detail"]
        # другой адрес за прокси — свой счётчик
        assert c.post("/chat", json={"message": "4"}, headers={"X-Forwarded-For": "10.0.0.7"}).status_code == 200


def test_daily_budget(make_agent, fake_llm):
    fake_llm.set("router", route("data"))
    with client_for(make_agent(), daily_budget_usd=0.0005) as c:
        first = c.post("/chat", json={"message": "Выручка за октябрь?"})
        assert first.status_code == 200 and first.json()["usage"]["cost_usd"] > 0.0005
        r = c.post("/chat", json={"message": "А в сентябре?"})
        assert r.status_code == 429 and "Дневной лимит" in r.json()["detail"]
        status = c.get("/meta").json()["demo_mode"]
        assert status["remaining_today_usd"] == 0


def test_evals_disabled_in_demo(make_agent):
    with client_for(make_agent()) as c:
        r = c.post("/evals/run", json={})
        assert r.status_code == 403
        assert c.get("/meta").json()["evals_enabled"] is False
        assert c.get("/evals/reports").status_code == 200  # история прогонов открыта


def test_unknown_price_still_counts():
    g = DemoGuard(enabled=True, daily_budget_usd=1.0)
    g.record(None, input_tokens=100_000, output_tokens=10_000)  # по тарифу Sonnet: 0.2 + 0.1
    assert g.status()["spent_today_usd"] == 0.3


def test_disabled_guard_is_transparent(make_agent, fake_llm):
    fake_llm.set("router", route("smalltalk"))
    with TestClient(create_app(make_agent())) as c:
        assert c.post("/chat", json={"message": "Привет"}).status_code == 200
        assert c.get("/meta").json()["demo_mode"] == {"enabled": False}
