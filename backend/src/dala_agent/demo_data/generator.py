"""Генератор демо-выгрузки «из 1С» для вымышленного ТОО «Дала Дистрибьюшн».

ВСЕ ДАННЫЕ СИНТЕТИЧЕСКИЕ. Генерация детерминирована (фиксированный seed),
поэтому одинаковый запуск даёт побайтно одинаковые CSV — на этом держатся
эталонные ответы в evals.

Запуск:  python -m dala_agent.demo_data.generator --out data/demo_1c
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from . import kz_ids
from .reference import (
    CHAIN_NAMES,
    CITY_SHARE,
    COMPANY,
    FEMALE_FIRST,
    HORECA_KINDS,
    HORECA_NAMES,
    MALE_FIRST,
    PATRONYMICS,
    PRODUCTS,
    RETURN_REASONS,
    SALES_TEAM,
    SEASONALITY,
    SEGMENTS,
    SHOP_NAMES,
    STREETS,
    SURNAMES,
    WAREHOUSES,
    WHOLESALE_ROOTS,
    WHOLESALE_SUFFIX,
)

SEED = 20261117
PERIOD_START = dt.date(2025, 1, 1)
EXPORT_DATE = dt.date(2026, 11, 15)  # дата выгрузки; продажи — по 14.11.2026 включительно
SALES_END = EXPORT_DATE - dt.timedelta(days=1)
# Цены 2025 года ниже цен 2026-го: инфляция + рост НДС с 12% до 16% с 01.01.2026.
PRICE_FACTOR_2025 = 1 / 1.12
# Недоплата «плохого» плательщика висит не дольше этого срока: более старые остатки к дате выгрузки
# закрыты (взыскание, взаимозачёт). Иначе клиент с долгом с начала 2025 года по кредитной политике
# (п. 4.1) был бы заблокирован весь период — а отгрузки ему идут.
MAX_RESIDUAL_AGE_DAYS = 150

MIN_ORDER_KZT = {"Алматы": 50_000, "Астана": 70_000, "Шымкент": 70_000}

# Нерабочие праздничные дни РК (без переносов), по ним отгрузок нет.
_HOLIDAYS_MD = {
    (1, 1),
    (1, 2),
    (1, 7),
    (3, 8),
    (3, 21),
    (3, 22),
    (3, 23),
    (5, 1),
    (5, 7),
    (5, 9),
    (7, 6),
    (8, 30),
    (10, 25),
    (12, 16),
}

CATEGORY_PREFIX = {
    "Напитки": "НАП",
    "Молочная продукция": "МОЛ",
    "Бакалея": "БАК",
    "Чай и кофе": "ЧАЙ",
    "Кондитерские изделия": "КОН",
    "Консервация": "КНС",
}
# Предпочтения сегментов по категориям (множитель к вероятности попасть в заказ).
SEGMENT_CATEGORY_PREF = {
    "HoReCa": {
        "Бакалея": 1.6,
        "Чай и кофе": 1.6,
        "Молочная продукция": 1.4,
        "Кондитерские изделия": 0.6,
        "Консервация": 0.8,
    },
    "Опт": {"Бакалея": 1.4, "Консервация": 1.3, "Молочная продукция": 0.4},
}
WAREHOUSE_DOC_PREFIX = {"WH-ALA-01": "АЛ1", "WH-ALA-02": "АЛ2", "WH-AST-01": "АСТ", "WH-SHY-01": "ШМК"}
D2 = Decimal("0.01")


def money(x: Decimal | float | int) -> Decimal:
    return Decimal(str(x)).quantize(D2, rounding=ROUND_HALF_UP)


def line_amount(price: Decimal, qty: int, discount: Decimal) -> Decimal:
    return money(Decimal(qty) * price * (Decimal(1) - discount / 100))


def vat_rate(d: dt.date) -> int:
    return 12 if d.year <= 2025 else 16


def is_working_day(d: dt.date) -> bool:
    return d.weekday() != 6 and (d.month, d.day) not in _HOLIDAYS_MD


# ---------------------------------------------------------------- сущности


@dataclass
class Product:
    id: int
    sku: str
    name: str
    category: str
    brand: str
    supplier: str
    unit: str
    box_qty: int
    price_2026: int
    purchase_price_2026: int
    shelf_life_days: int
    season: str
    popularity: float

    def price(self, d: dt.date) -> Decimal:
        if d.year >= 2026:
            return Decimal(self.price_2026)
        return Decimal(round(self.price_2026 * PRICE_FACTOR_2025 / 5) * 5)

    def purchase_price(self, d: dt.date) -> Decimal:
        if d.year >= 2026:
            return Decimal(self.purchase_price_2026)
        return money(self.purchase_price_2026 * PRICE_FACTOR_2025)


@dataclass
class Manager:
    id: int
    code: str
    full_name: str
    position: str
    city: str
    phone: str
    email: str
    hired_at: dt.date


@dataclass
class Client:
    id: int
    code: str
    name: str
    legal_form: str
    bin: str | None
    iin: str | None
    segment: str
    city: str
    address: str
    contact_name: str
    contact_phone: str
    manager_id: int
    warehouse_id: int
    payment_terms_days: int
    credit_limit_kzt: int
    base_discount_pct: float
    max_discount_pct: float
    created_at: dt.date
    churn_date: dt.date | None
    size: float
    behavior: str  # good | late | bad — платёжная дисциплина (в выгрузку не идёт)
    shipment_blocked: bool = False


@dataclass
class Generated:
    warehouses: list[dict] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    managers: list[Manager] = field(default_factory=list)
    clients: list[Client] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)
    lines: list[dict] = field(default_factory=list)
    stock: list[dict] = field(default_factory=list)
    receivables: list[dict] = field(default_factory=list)
    plans: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------- люди и контакты


def _female_surname(s: str) -> str:
    return s + "а" if s.endswith(("ов", "ев", "ин")) else s


def person(rng: random.Random) -> tuple[str, bool]:
    male = rng.random() < 0.55
    first = rng.choice(MALE_FIRST if male else FEMALE_FIRST)
    surname = rng.choice(SURNAMES)
    patronymic_m, patronymic_f = rng.choice(PATRONYMICS)
    if male:
        return f"{surname} {first} {patronymic_m}", True
    return f"{_female_surname(surname)} {first} {patronymic_f}", False


def initials(full_name: str) -> str:
    surname, first, patronymic = full_name.split()
    return f"{surname} {first[0]}.{patronymic[0]}."


def phone(rng: random.Random) -> str:
    op = rng.choice(["701", "702", "705", "707", "708", "747", "771", "775", "776", "777"])
    n = rng.randint(0, 9_999_999)
    s = f"{n:07d}"
    return f"+7 {op} {s[:3]} {s[3:5]} {s[5:]}"


def random_date(rng: random.Random, start: dt.date, end: dt.date) -> dt.date:
    return start + dt.timedelta(days=rng.randint(0, (end - start).days))


# ---------------------------------------------------------------- справочники


def build_reference(rng: random.Random, g: Generated) -> None:
    for i, (code, name, city, address) in enumerate(WAREHOUSES, start=1):
        g.warehouses.append({"id": i, "code": code, "name": name, "city": city, "address": address})

    counters: dict[str, int] = defaultdict(int)
    for i, spec in enumerate(PRODUCTS, start=1):
        prefix = CATEGORY_PREFIX[spec.category]
        counters[prefix] += 1
        g.products.append(
            Product(
                id=i,
                sku=f"{prefix}-{counters[prefix]:04d}",
                name=spec.name,
                category=spec.category,
                brand=spec.brand,
                supplier=spec.supplier,
                unit=spec.unit,
                box_qty=spec.box_qty,
                price_2026=spec.price_2026,
                purchase_price_2026=round(spec.price_2026 * rng.uniform(0.72, 0.84)),
                shelf_life_days=spec.shelf_life_days,
                season=spec.season,
                popularity=spec.popularity,
            )
        )

    used_names: set[str] = set()
    for i, (position, city) in enumerate(SALES_TEAM, start=1):
        while True:
            full_name, _ = person(rng)
            if full_name not in used_names:
                used_names.add(full_name)
                break
        g.managers.append(
            Manager(
                id=i,
                code=f"СОТ-{i:03d}",
                full_name=full_name,
                position=position,
                city=city,
                phone=phone(rng),
                email=f"sales{i:02d}@dala-distribution.example",
                hired_at=random_date(rng, dt.date(2018, 3, 1), dt.date(2024, 9, 1)),
            )
        )


def _pick_city(rng: random.Random) -> str:
    return rng.choices(list(CITY_SHARE), weights=list(CITY_SHARE.values()))[0]


def _manager_for(rng: random.Random, g: Generated, segment: str, city: str) -> Manager:
    in_city = [m for m in g.managers if m.city == city and m.position != "Руководитель отдела продаж"]
    if segment in ("Сеть", "Опт"):
        kam = [m for m in in_city if m.position == "Менеджер по ключевым клиентам"]
        pool = kam or [m for m in in_city if m.position == "Региональный менеджер"]
    else:
        pool = [m for m in in_city if m.position != "Менеджер по ключевым клиентам"]
    return rng.choice(pool)


def _warehouse_for(rng: random.Random, g: Generated, city: str) -> int:
    if city == "Алматы":
        return 1 if rng.random() < 0.65 else 2
    return 3 if city == "Астана" else 4


def build_clients(rng: random.Random, g: Generated) -> None:
    cid = 0
    used: set[str] = set()
    chain_names = iter(CHAIN_NAMES)
    wholesale_roots = list(WHOLESALE_ROOTS)
    rng.shuffle(wholesale_roots)
    wholesale_iter = iter(wholesale_roots)

    for segment, (count, _f, _l, _b, terms, limit_mln, base_disc, max_disc) in SEGMENTS.items():
        for _ in range(count):
            cid += 1
            city = _pick_city(rng) if segment != "Сеть" else rng.choice(["Алматы", "Алматы", "Астана", "Шымкент"])
            owner, male = person(rng)
            registered = random_date(rng, dt.date(2008, 1, 1), dt.date(2023, 12, 1))
            bin_ = iin = None

            if segment == "Сеть":
                legal_form, name = "ТОО", f"ТОО «{next(chain_names)}»"
            elif segment == "Опт":
                legal_form, name = "ТОО", f"ТОО «{next(wholesale_iter)} {rng.choice(WHOLESALE_SUFFIX)}»"
            elif segment == "HoReCa":
                kind, title = rng.choice(HORECA_KINDS), rng.choice(HORECA_NAMES)
                if rng.random() < 0.5:
                    legal_form, name = "ТОО", f"ТОО «{kind} {title}»"
                else:
                    legal_form, name = "ИП", f"ИП {initials(owner)} ({kind.lower()} «{title}»)"
            else:
                legal_form, name = "ИП", f"ИП {initials(owner)} (магазин «{rng.choice(SHOP_NAMES)}»)"

            while name in used:  # редкие совпадения названий
                name = name.replace("»", " 2»", 1) if "»" in name else name + " 2"
            used.add(name)

            if legal_form == "ТОО":
                bin_ = kz_ids.make_bin(rng, registered)
                contact = person(rng)[0]
            else:
                birth = random_date(rng, dt.date(1965, 1, 1), dt.date(2001, 12, 31))
                iin = kz_ids.make_iin(rng, birth, male)
                contact = owner

            # условия по кредитной политике (см. data/documents/02_kreditnaya_politika.md)
            payment_terms = terms
            credit_limit = 0
            if segment == "Розница":
                if rng.random() < 0.30:  # проверенные магазины: отсрочка 7 дней
                    payment_terms = 7
                    credit_limit = rng.choice([500_000, 750_000, 1_000_000, 1_500_000])
            else:
                credit_limit = rng.randint(limit_mln[0], limit_mln[1]) * 1_000_000

            existing = rng.random() < 0.75
            created = (
                random_date(rng, dt.date(2019, 1, 10), dt.date(2024, 12, 20))
                if existing
                else random_date(rng, PERIOD_START, dt.date(2026, 9, 30))
            )
            churn = None
            if rng.random() < 0.08 and created < dt.date(2026, 5, 1):  # ушедшие клиенты
                churn = random_date(rng, max(created, PERIOD_START) + dt.timedelta(days=60), dt.date(2026, 8, 31))

            size_range = {"Сеть": (2.0, 4.0), "Опт": (1.5, 3.0), "HoReCa": (0.7, 1.5), "Розница": (0.6, 1.4)}
            behavior = rng.choices(["good", "late", "bad"], weights=[80, 15, 5])[0]
            manager = _manager_for(rng, g, segment, city)

            g.clients.append(
                Client(
                    id=cid,
                    code=f"КЛ-{cid:05d}",
                    name=name,
                    legal_form=legal_form,
                    bin=bin_,
                    iin=iin,
                    segment=segment,
                    city=city,
                    address=f"{city}, {rng.choice(STREETS[city])}, {rng.randint(1, 240)}",
                    contact_name=contact,
                    contact_phone=phone(rng),
                    manager_id=manager.id,
                    warehouse_id=_warehouse_for(rng, g, city),
                    payment_terms_days=payment_terms,
                    credit_limit_kzt=credit_limit,
                    base_discount_pct=base_disc,
                    max_discount_pct=max_disc,
                    created_at=created,
                    churn_date=churn,
                    size=rng.uniform(*size_range[segment]),
                    behavior=behavior,
                )
            )


# ---------------------------------------------------------------- продажи


def _category_pref(segment: str, category: str) -> float:
    return SEGMENT_CATEGORY_PREF.get(segment, {}).get(category, 1.0)


def _trend(d: dt.date) -> float:
    months = (d.year - PERIOD_START.year) * 12 + d.month - PERIOD_START.month
    return 1.0 + 0.10 * months / 22


def order_total(products: list[Product], boxes: list[int], d: dt.date, discount: Decimal) -> Decimal:
    return sum(
        (line_amount(p.price(d), p.box_qty * b, discount) for p, b in zip(products, boxes, strict=True)),
        Decimal(0),
    )


def build_sales(rng: random.Random, g: Generated) -> None:
    wh_code = {w["id"]: w["code"] for w in g.warehouses}
    seq: dict[tuple[str, int], int] = defaultdict(int)
    doc_id = 0
    line_id = 0
    pending_returns: list[tuple[dt.date, dict, list[dict]]] = []

    def next_number(prefix: str, d: dt.date) -> str:
        seq[(prefix, d.year)] += 1
        return f"{prefix}-{d:%y}-{seq[(prefix, d.year)]:06d}"

    def add_doc(d: dt.date, client: Client, doc_type: str, base_id: int | None, comment: str, posted: bool) -> dict:
        nonlocal doc_id
        doc_id += 1
        prefix = WAREHOUSE_DOC_PREFIX[wh_code[client.warehouse_id]]
        if doc_type == "Возврат":
            prefix = "ВЗ-" + prefix
        doc = {
            "id": doc_id,
            "doc_number": next_number(prefix, d),
            "doc_date": d,
            "doc_type": doc_type,
            "is_posted": posted,
            "client_id": client.id,
            "manager_id": client.manager_id,
            "warehouse_id": client.warehouse_id,
            "base_document_id": base_id,
            "comment": comment,
            "amount_kzt": Decimal(0),
            "vat_kzt": Decimal(0),
        }
        g.documents.append(doc)
        return doc

    def add_line(
        doc: dict,
        line_no: int,
        product: Product,
        qty: int,
        discount: Decimal,
        price: Decimal | None = None,
        rate: int | None = None,
    ) -> None:
        """Строка документа; для возврата цена и ставка НДС берутся из реализации."""
        nonlocal line_id
        line_id += 1
        d = doc["doc_date"]
        price = product.price(d) if price is None else price
        rate = vat_rate(d) if rate is None else rate
        amount = line_amount(price, qty, discount)
        vat = money(amount * rate / (100 + rate))
        g.lines.append(
            {
                "id": line_id,
                "document_id": doc["id"],
                "line_no": line_no,
                "product_id": product.id,
                "quantity": qty,
                "price_kzt": price,
                "discount_pct": discount,
                "amount_kzt": amount,
                "vat_rate": rate,
                "vat_kzt": vat,
                "cost_kzt": money(Decimal(qty) * product.purchase_price(d)),
            }
        )
        doc["amount_kzt"] += amount
        doc["vat_kzt"] += vat

    d = PERIOD_START
    while d <= SALES_END:
        # возвраты, дата которых наступила
        for item in [r for r in pending_returns if r[0] == d]:
            pending_returns.remove(item)
            _, sale_doc, sale_lines = item
            client = g.clients[sale_doc["client_id"] - 1]
            ret = add_doc(d, client, "Возврат", sale_doc["id"], rng.choice(RETURN_REASONS), True)
            for n, sl in enumerate(rng.sample(sale_lines, k=min(len(sale_lines), rng.randint(1, 2))), 1):
                product = g.products[sl["product_id"] - 1]
                qty = min(sl["quantity"], product.box_qty * rng.randint(1, 2))
                add_line(ret, n, product, qty, sl["discount_pct"], sl["price_kzt"], sl["vat_rate"])

        if not is_working_day(d):
            d += dt.timedelta(days=1)
            continue

        day_factor = 0.55 if d.weekday() == 5 else 1.0
        trend = _trend(d)
        for client in g.clients:
            if client.created_at > d or (client.churn_date and d >= client.churn_date):
                continue
            freq, lines_rng, line_value_rng = SEGMENTS[client.segment][1:4]
            if rng.random() >= freq * day_factor:
                continue

            posted = rng.random() > 0.003  # единичные непроведённые черновики, как в живой 1С
            doc = add_doc(d, client, "Реализация", None, "", posted)
            weights = [
                p.popularity * SEASONALITY[p.season][d.month - 1] * _category_pref(client.segment, p.category)
                for p in g.products
            ]
            n_lines = rng.randint(*lines_rng)
            chosen: list[Product] = []
            while len(chosen) < n_lines:
                p = rng.choices(g.products, weights=weights)[0]
                if p not in chosen:
                    chosen.append(p)

            discount = client.base_discount_pct
            if client.max_discount_pct > discount and rng.random() < 0.25:
                discount = round(rng.uniform(discount, client.max_discount_pct) * 2) / 2
            disc = Decimal(str(discount))

            boxes = []
            for p in chosen:
                # объём строки задаётся суммой (тыс. ₸), а не числом коробок: иначе дорогие коробки
                # (чай, сахар) перевешивают дешёвые (вода) и искажают сезонность выручки
                target = (
                    rng.uniform(*line_value_rng)
                    * 1000
                    * client.size
                    * p.popularity**0.5
                    * SEASONALITY[p.season][d.month - 1]
                    * trend
                )
                box_value = float(p.price(d)) * p.box_qty
                boxes.append(max(1, round(target / box_value)))

            # минимальная сумма заказа по регламенту отгрузки (п. 2.3)
            minimum = MIN_ORDER_KZT[client.city]
            while order_total(chosen, boxes, d, disc) < minimum:
                boxes[rng.randrange(len(boxes))] += 1

            first_line = line_id + 1
            for n, (p, b) in enumerate(zip(chosen, boxes, strict=True), 1):
                add_line(doc, n, p, p.box_qty * b, disc)
            lines = g.lines[first_line - 1 :]

            if posted and rng.random() < 0.012:
                ret_date = d + dt.timedelta(days=rng.randint(1, 10))
                while ret_date <= SALES_END and not is_working_day(ret_date):
                    ret_date += dt.timedelta(days=1)
                if ret_date <= SALES_END:
                    pending_returns.append((ret_date, doc, lines))
        d += dt.timedelta(days=1)


# ---------------------------------------------------------------- остатки


def build_stock(rng: random.Random, g: Generated) -> None:
    window_start = EXPORT_DATE - dt.timedelta(days=60)
    docs = {doc["id"]: doc for doc in g.documents}
    sold: dict[tuple[int, int], int] = defaultdict(int)
    for ln in g.lines:
        doc = docs[ln["document_id"]]
        if doc["doc_type"] == "Реализация" and doc["is_posted"] and doc["doc_date"] >= window_start:
            sold[(doc["warehouse_id"], ln["product_id"])] += ln["quantity"]

    for w in g.warehouses:
        for p in g.products:
            avg_daily = sold[(w["id"], p.id)] / 60
            if avg_daily == 0:
                continue
            if rng.random() < 0.05:  # дефицит: позиции нет на складе
                continue
            cover = rng.uniform(3, 12) if p.shelf_life_days <= 21 else rng.uniform(5, 45)
            boxes = max(1, math.ceil(avg_daily * cover / p.box_qty))
            n_lots = 1 if boxes < 4 or p.shelf_life_days <= 21 else rng.randint(1, 3)
            split = sorted(rng.sample(range(1, boxes), k=n_lots - 1)) if n_lots > 1 else []
            parts = [b - a for a, b in zip([0, *split], [*split, boxes], strict=True)]
            for part in parts:
                if p.shelf_life_days <= 21:
                    age = rng.randint(0, max(1, p.shelf_life_days // 2))
                else:
                    age = rng.randint(5, min(int(p.shelf_life_days * 0.6), 240))
                    r = rng.random()
                    if r < 0.05:  # партия с истекающим сроком (5–30 дней)
                        age = p.shelf_life_days - rng.randint(5, 30)
                    elif r < 0.058:  # просроченная партия, не выведенная в карантин
                        age = p.shelf_life_days + rng.randint(1, 20)
                produced = EXPORT_DATE - dt.timedelta(days=age)
                qty = part * p.box_qty
                reserved = 0
                if rng.random() < 0.3:
                    reserved = min(qty, p.box_qty * rng.randint(1, max(1, part // 3)))
                g.stock.append(
                    {
                        "id": len(g.stock) + 1,
                        "as_of_date": EXPORT_DATE,
                        "warehouse_id": w["id"],
                        "product_id": p.id,
                        "lot_number": f"{produced:%y%m%d}-{rng.randint(100, 999)}",
                        "production_date": produced,
                        "expiry_date": produced + dt.timedelta(days=p.shelf_life_days),
                        "quantity": qty,
                        "reserved_qty": reserved,
                    }
                )


# ---------------------------------------------------------------- дебиторка


def build_receivables(rng: random.Random, g: Generated) -> None:
    clients = {c.id: c for c in g.clients}
    max_overdue: dict[int, int] = defaultdict(int)
    for doc in g.documents:
        if doc["doc_type"] != "Реализация" or not doc["is_posted"]:
            continue
        client = clients[doc["client_id"]]
        if client.payment_terms_days == 0:
            continue
        due = doc["doc_date"] + dt.timedelta(days=client.payment_terms_days)
        delay = {"good": rng.randint(-5, 2), "late": rng.randint(2, 25), "bad": rng.randint(10, 90)}[client.behavior]
        pay_date = due + dt.timedelta(days=delay)
        amount = doc["amount_kzt"]
        if pay_date <= EXPORT_DATE:
            if client.behavior == "bad" and rng.random() < 0.15:
                paid = money(amount * Decimal(str(round(rng.uniform(0.3, 0.8), 2))))
                if (EXPORT_DATE - due).days > MAX_RESIDUAL_AGE_DAYS:
                    continue  # старый остаток уже закрыт; случайные числа расходуются как раньше
            else:
                continue  # оплачено полностью — в ведомость открытых позиций не попадает
        else:
            paid = Decimal(0)
            if client.behavior != "good" and rng.random() < 0.3:
                paid = money(amount * Decimal(str(round(rng.uniform(0.2, 0.7), 2))))
        g.receivables.append(
            {
                "id": len(g.receivables) + 1,
                "as_of_date": EXPORT_DATE,
                "client_id": client.id,
                "document_id": doc["id"],
                "doc_date": doc["doc_date"],
                "due_date": due,
                "amount_kzt": amount,
                "paid_kzt": paid,
            }
        )
        overdue = (EXPORT_DATE - due).days
        if overdue > 0:
            max_overdue[client.id] = max(max_overdue[client.id], overdue)

    # кредитная политика п. 4.1: просрочка более 15 дней — блокировка отгрузок
    for cid, days in max_overdue.items():
        if days > 15:
            clients[cid].shipment_blocked = True


# ---------------------------------------------------------------- планы продаж


def build_plans(rng: random.Random, g: Generated) -> None:
    docs = {doc["id"]: doc for doc in g.documents}
    actual: dict[tuple[int, dt.date], Decimal] = defaultdict(Decimal)
    for doc in docs.values():
        if not doc["is_posted"]:
            continue
        month = doc["doc_date"].replace(day=1)
        sign = 1 if doc["doc_type"] == "Реализация" else -1
        actual[(doc["manager_id"], month)] += sign * doc["amount_kzt"]

    managers_with_clients = sorted({c.manager_id for c in g.clients})
    months = []
    m = PERIOD_START
    while m <= dt.date(2026, 12, 1):
        months.append(m)
        m = (m + dt.timedelta(days=32)).replace(day=1)

    for mid in managers_with_clients:
        recent = [actual[(mid, dt.date(2026, k, 1))] for k in (8, 9, 10)]
        recent_avg = sum(recent) / 3 if recent else Decimal(0)
        for month in months:
            if month < dt.date(2026, 11, 1):
                base = actual[(mid, month)] * Decimal(str(rng.uniform(0.92, 1.10)))
            else:
                season = Decimal("1.05") if month.month == 11 else Decimal("1.18")
                base = recent_avg * season * Decimal(str(rng.uniform(1.0, 1.06)))
            plan = int(base / 100_000) * 100_000
            if plan > 0:
                g.plans.append({"id": len(g.plans) + 1, "manager_id": mid, "month": month, "plan_amount_kzt": plan})


# ---------------------------------------------------------------- запись CSV


def _fmt(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, dt.date):
        return v.isoformat()
    return str(v)


def _write(path: Path, header: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        for row in rows:
            w.writerow([_fmt(row[h]) for h in header])


def generate(seed: int = SEED) -> Generated:
    rng = random.Random(seed)
    g = Generated()
    build_reference(rng, g)
    build_clients(rng, g)
    build_sales(rng, g)
    build_stock(rng, g)
    build_receivables(rng, g)
    build_plans(rng, g)
    return g


def write_csv(g: Generated, out: Path) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    _write(out / "warehouses.csv", ["id", "code", "name", "city", "address"], g.warehouses)
    _write(
        out / "products.csv",
        [
            "id",
            "sku",
            "name",
            "category",
            "brand",
            "supplier",
            "unit",
            "box_qty",
            "price_kzt",
            "purchase_price_kzt",
            "shelf_life_days",
            "is_active",
        ],
        [
            {**p.__dict__, "price_kzt": p.price_2026, "purchase_price_kzt": p.purchase_price_2026, "is_active": True}
            for p in g.products
        ],
    )
    _write(
        out / "managers.csv",
        ["id", "code", "full_name", "position", "city", "phone", "email", "hired_at"],
        [m.__dict__ for m in g.managers],
    )
    _write(
        out / "clients.csv",
        [
            "id",
            "code",
            "name",
            "legal_form",
            "bin",
            "iin",
            "segment",
            "city",
            "address",
            "contact_name",
            "contact_phone",
            "manager_id",
            "warehouse_id",
            "payment_terms_days",
            "credit_limit_kzt",
            "base_discount_pct",
            "created_at",
            "is_active",
            "shipment_blocked",
        ],
        [{**c.__dict__, "is_active": c.churn_date is None} for c in g.clients],
    )
    _write(
        out / "sales_documents.csv",
        [
            "id",
            "doc_number",
            "doc_date",
            "doc_type",
            "is_posted",
            "client_id",
            "manager_id",
            "warehouse_id",
            "base_document_id",
            "amount_kzt",
            "vat_kzt",
            "comment",
        ],
        g.documents,
    )
    _write(
        out / "sales_lines.csv",
        [
            "id",
            "document_id",
            "line_no",
            "product_id",
            "quantity",
            "price_kzt",
            "discount_pct",
            "amount_kzt",
            "vat_rate",
            "vat_kzt",
            "cost_kzt",
        ],
        g.lines,
    )
    _write(
        out / "stock_balances.csv",
        [
            "id",
            "as_of_date",
            "warehouse_id",
            "product_id",
            "lot_number",
            "production_date",
            "expiry_date",
            "quantity",
            "reserved_qty",
        ],
        g.stock,
    )
    _write(
        out / "receivables.csv",
        ["id", "as_of_date", "client_id", "document_id", "doc_date", "due_date", "amount_kzt", "paid_kzt"],
        g.receivables,
    )
    _write(out / "sales_plans.csv", ["id", "manager_id", "month", "plan_amount_kzt"], g.plans)
    _write(
        out / "export_info.csv",
        ["export_date", "source", "company", "period_start", "period_end", "note"],
        [
            {
                "export_date": EXPORT_DATE,
                "source": "1С:Управление торговлей — ДЕМО-выгрузка",
                "company": COMPANY,
                "period_start": PERIOD_START,
                "period_end": SALES_END,
                "note": "Синтетические данные для портфолио. Компания, клиенты, люди, ИИН/БИН и телефоны вымышлены.",
            }
        ],
    )
    return {
        "warehouses": len(g.warehouses),
        "products": len(g.products),
        "managers": len(g.managers),
        "clients": len(g.clients),
        "sales_documents": len(g.documents),
        "sales_lines": len(g.lines),
        "stock_balances": len(g.stock),
        "receivables": len(g.receivables),
        "sales_plans": len(g.plans),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Демо-выгрузка «из 1С» (синтетические данные)")
    parser.add_argument("--out", type=Path, default=Path("data/demo_1c"))
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    stats = write_csv(generate(args.seed), args.out)
    for name, n in stats.items():
        print(f"{name:16s} {n:>7d}")


if __name__ == "__main__":
    main()
