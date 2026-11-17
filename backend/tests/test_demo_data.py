"""Инварианты демо-выгрузки: данные должны быть согласованы между собой и с регламентами."""

from __future__ import annotations

import datetime as dt
import filecmp
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

from dala_agent.demo_data import kz_ids
from dala_agent.demo_data.generator import EXPORT_DATE, MIN_ORDER_KZT, generate, write_csv
from dala_agent.demo_data.reference import SEGMENTS

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_CSV = REPO_ROOT / "data" / "demo_1c"


@pytest.fixture(scope="module")
def g():
    return generate()


def test_kz_id_checksum():
    assert kz_ids.is_valid("000000000000") is True  # тривиальный случай: сумма 0
    assert kz_ids.is_valid("12345678901") is False  # 11 цифр
    assert kz_ids.is_valid("12345678901a") is False


def test_generation_is_deterministic(g, tmp_path):
    write_csv(g, tmp_path / "a")
    write_csv(generate(), tmp_path / "b")
    names = sorted(p.name for p in (tmp_path / "a").iterdir())
    match, mismatch, errors = filecmp.cmpfiles(tmp_path / "a", tmp_path / "b", names, shallow=False)
    assert not mismatch and not errors


def test_committed_csv_is_up_to_date(g, tmp_path):
    """CSV в репозитории совпадают с выводом генератора — эталоны evals не «уплывут»."""
    write_csv(g, tmp_path)
    names = sorted(p.name for p in tmp_path.iterdir())
    match, mismatch, errors = filecmp.cmpfiles(tmp_path, COMMITTED_CSV, names, shallow=False)
    assert not mismatch and not errors, f"Перегенерируйте данные: make data ({mismatch or errors})"


def test_clients_ids_and_terms(g):
    for c in g.clients:
        if c.legal_form == "ТОО":
            assert c.bin and kz_ids.is_valid(c.bin) and c.iin is None
        else:
            assert c.iin and kz_ids.is_valid(c.iin) and c.bin is None
        terms = SEGMENTS[c.segment][4]
        if c.segment == "Розница":
            assert c.payment_terms_days in (0, 7)
            assert c.credit_limit_kzt <= 1_500_000
        else:
            assert c.payment_terms_days == terms


def test_document_totals_and_vat(g):
    totals: dict[int, Decimal] = defaultdict(Decimal)
    docs = {d["id"]: d for d in g.documents}
    for ln in g.lines:
        totals[ln["document_id"]] += ln["amount_kzt"]
        doc = docs[ln["document_id"]]
        if doc["doc_type"] == "Реализация":
            assert ln["vat_rate"] == (12 if doc["doc_date"].year == 2025 else 16)
    for doc in g.documents:
        assert totals[doc["id"]] == doc["amount_kzt"]
        assert doc["doc_date"] < EXPORT_DATE


def test_discounts_within_policy(g):
    """Политика скидок п. 2.1: скидка не выше полномочий менеджера по сегменту."""
    clients = {c.id: c for c in g.clients}
    docs = {d["id"]: d for d in g.documents}
    for ln in g.lines:
        client = clients[docs[ln["document_id"]]["client_id"]]
        max_disc = SEGMENTS[client.segment][7]
        assert client.base_discount_pct <= ln["discount_pct"] <= max_disc


def test_min_order_amount(g):
    """Регламент отгрузки п. 2.3: минимальная сумма заказа."""
    clients = {c.id: c for c in g.clients}
    for doc in g.documents:
        if doc["doc_type"] == "Реализация":
            assert doc["amount_kzt"] >= MIN_ORDER_KZT[clients[doc["client_id"]].city]


def test_returns_reference_earlier_sale_of_same_client(g):
    docs = {d["id"]: d for d in g.documents}
    returns = [d for d in g.documents if d["doc_type"] == "Возврат"]
    assert returns
    for r in returns:
        base = docs[r["base_document_id"]]
        assert base["doc_type"] == "Реализация" and base["is_posted"]
        assert base["client_id"] == r["client_id"]
        assert base["doc_date"] < r["doc_date"]
        assert r["comment"]


def test_receivables_and_blocking(g):
    """Кредитная политика п. 4.1: просрочка > 15 дней ⇒ отгрузки заблокированы."""
    overdue: dict[int, int] = defaultdict(int)
    for r in g.receivables:
        assert Decimal(0) <= r["paid_kzt"] < r["amount_kzt"]
        assert r["due_date"] > r["doc_date"]
        overdue[r["client_id"]] = max(overdue[r["client_id"]], (EXPORT_DATE - r["due_date"]).days)
    for c in g.clients:
        assert c.shipment_blocked == (overdue.get(c.id, 0) > 15)


def test_stock(g):
    for s in g.stock:
        assert 0 <= s["reserved_qty"] <= s["quantity"]
        assert s["production_date"] <= EXPORT_DATE
        assert s["expiry_date"] > s["production_date"]
    # в данных есть что искать: истекающие и просроченные партии
    assert any(s["expiry_date"] < EXPORT_DATE for s in g.stock)
    assert any(EXPORT_DATE <= s["expiry_date"] <= EXPORT_DATE + dt.timedelta(days=30) for s in g.stock)


def test_seasonality_drinks_peak_in_summer(g):
    products = {p.id: p for p in g.products}
    docs = {d["id"]: d for d in g.documents}
    by_month: dict[int, Decimal] = defaultdict(Decimal)
    for ln in g.lines:
        doc = docs[ln["document_id"]]
        if products[ln["product_id"]].category == "Напитки" and doc["doc_date"].year == 2025:
            by_month[doc["doc_date"].month] += ln["amount_kzt"]
    assert by_month[7] > Decimal("1.5") * by_month[1]
