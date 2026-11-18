"""HTTP API поверх графа (заглушки вместо базы и модели)."""

from __future__ import annotations

import pytest
from conftest import route
from fastapi.testclient import TestClient

from dala_agent.api.main import create_app


@pytest.fixture
def client(make_agent):
    with TestClient(create_app(make_agent())) as c:
        yield c


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_chat_creates_thread(client, fake_llm):
    fake_llm.set("router", route("data"))
    r = client.post("/chat", json={"message": "Сколько продали напитков за октябрь?"})
    assert r.status_code == 200
    body = r.json()
    assert body["thread_id"] and body["status"] == "done" and body["route"] == "data"
    assert body["sql"]["rows"] == [[2025, 26289747], [2026, 35279117]]
    assert [s["node"] for s in body["trace"]] == ["router", "sql", "compose"]


def test_confirm_flow(client, fake_llm, stub):
    fake_llm.set("router", route("action"))
    fake_llm.set("action_plan", {"kind": "create_task", "title": "Позвонить", "responsible_surname": "Иванов"})
    r = client.post("/chat", json={"message": "Поставь задачу Иванову", "thread_id": "th1"}).json()
    assert r["status"] == "needs_confirmation"
    assert client.get("/chat/th1/pending").json()["pending"]["preview"] == r["answer"]

    no_id = client.post("/chat/th1/confirm", json={"approved": True})
    assert no_id.status_code == 422 and not stub.records  # «да» без action_id не принимается

    done = client.post("/chat/th1/confirm", json={"approved": True, "action_id": r["pending_action"]["action_id"]})
    assert done.status_code == 200 and done.json()["action_result"]["ok"] is True
    assert len(stub.records) == 1

    again = client.post("/chat/th1/confirm", json={"approved": True})
    assert again.status_code == 409  # подтверждать больше нечего
    assert len(stub.records) == 1


def test_validation(client):
    assert client.post("/chat", json={"message": ""}).status_code == 422
    assert client.post("/chat", json={"message": "x" * 5000}).status_code == 422


def test_meta(client):
    m = client.get("/meta").json()
    assert m["model"] == "fake-llm" and m["bitrix_mode"] == "stub" and m["demo"] is True
    assert m["pricing"]["override"] == [3.0, 15.0]


def test_saved_eval_reports(client, monkeypatch, tmp_path):
    import json

    monkeypatch.setattr("dala_agent.evals.runner.REPORTS_DIR", tmp_path)
    (tmp_path / "20261118-170347.json").write_text(
        json.dumps({"started_at": "2026-11-18T17:03:47", "model": "m", "summary": {"passed": 32, "cases": 35}}),
        encoding="utf-8",
    )
    reports = client.get("/evals/reports").json()
    assert reports[0]["name"] == "20261118-170347" and reports[0]["summary"]["passed"] == 32
    assert client.get("/evals/reports/20261118-170347").json()["model"] == "m"
    assert client.get("/evals/reports/..%2F..%2Fsecret").status_code == 404


def test_cost_by_model():
    from dala_agent.pricing import Pricing

    p = Pricing()
    steps = [
        {"usage": {"model": "claude-haiku-5-5", "input_tokens": 1_000_000, "output_tokens": 0}},
        {"usage": {"model": "claude-sonnet-5-5", "input_tokens": 1_000_000, "output_tokens": 100_000}},
        {"usage": None},
    ]
    assert p.cost_of_steps(steps) == pytest.approx(0.10 + 2.0 + 1.0)
    assert p.cost_of_steps([{"usage": {"model": "qwen", "input_tokens": 5, "output_tokens": 1}}]) is None
    assert Pricing(override=(1.0, 1.0)).cost("qwen", 1_000_000, 0) == 1.0
