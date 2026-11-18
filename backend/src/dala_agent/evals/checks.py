"""Детерминированные проверки ответа агента против эталона (без LLM-судьи)."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

_NUM_IN_TEXT = re.compile(r"(?<![\w.,])(\d{1,3}(?:[   ]\d{3})+|\d+)(?:[.,](\d+))?\s*(млрд|млн|тыс)?", re.I)
_MULT = {"млрд": 1e9, "млн": 1e6, "тыс": 1e3}


def numbers_in_text(text: str) -> list[float]:
    """«35,3 млн ₸» → 35 300 000; «1 250 000» → 1 250 000; «97,6%» → 97.6."""
    out = []
    for m in _NUM_IN_TEXT.finditer(text or ""):
        whole = re.sub(r"[   ]", "", m.group(1))
        value = float(f"{whole}.{m.group(2)}" if m.group(2) else whole)
        if m.group(3):
            value *= _MULT[m.group(3).lower()]
        out.append(value)
    return out


def numbers_in_rows(rows: list[list[Any]]) -> list[float]:
    out = []
    for row in rows or []:
        for v in row:
            if isinstance(v, bool):
                continue
            if isinstance(v, int | float | Decimal):
                out.append(float(v))
    return out


def close(a: float, b: float) -> bool:
    """Целые до 10 000 (количества, годы) — точно; деньги и доли — с допуском на округление «35,3 млн»."""
    if abs(b) < 10_000 and float(b).is_integer() and float(a).is_integer():
        return a == b
    return abs(a - b) <= max(abs(b) * 0.005, 0.11)


def check_reference(ref_rows: list[tuple], answer: str, sql: dict | None) -> list[str]:
    agent_numbers = numbers_in_rows((sql or {}).get("rows") or [])
    text_numbers = numbers_in_text(answer)
    row_count = len((sql or {}).get("rows") or [])
    failures = []
    for row in ref_rows:
        for v in row:
            if v is None or isinstance(v, bool):
                continue
            if isinstance(v, int | float | Decimal):
                x = float(v)
                single = len(ref_rows) == 1 and len(row) == 1
                if any(close(n, x) for n in agent_numbers + text_numbers) or (single and row_count == x):
                    continue
                failures.append(f"нет эталонного значения {x:g}")
            elif str(v).casefold() not in (answer or "").casefold():
                if not any(str(v) == str(c) for r in (sql or {}).get("rows") or [] for c in r):
                    failures.append(f"нет эталонного значения «{v}»")
    return failures


def check_turn(expect: dict, result, ref_rows: list[tuple] | None, side_effects: int) -> list[str]:
    """result — TurnResult; возвращает список нарушений (пусто — шаг пройден)."""
    f: list[str] = []
    answer = result.answer or ""
    low = answer.casefold()

    if "route" in expect:
        allowed = expect["route"] if isinstance(expect["route"], list) else [expect["route"]]
        if result.route not in allowed:
            f.append(f"маршрут {result.route}, ожидался {'/'.join(allowed)}")
    if "status" in expect and result.status != expect["status"]:
        f.append(f"статус {result.status}, ожидался {expect['status']}")
    if "status_not" in expect and result.status == expect["status_not"]:
        f.append(f"статус {result.status} недопустим")
    if "language" in expect and result.language != expect["language"]:
        f.append(f"язык {result.language}, ожидался {expect['language']}")
    for s in expect.get("answer_contains", []):
        if str(s).casefold() not in low:
            f.append(f"в ответе нет «{s}»")
    if expect.get("answer_contains_any") and not any(str(s).casefold() in low for s in expect["answer_contains_any"]):
        f.append("в ответе нет ни одного из: " + ", ".join(map(str, expect["answer_contains_any"])))
    for s in expect.get("answer_not_contains", []):
        if str(s).casefold() in low:
            f.append(f"в ответе лишнее «{s}»")
    for pattern in expect.get("answer_not_regex", []):
        if re.search(pattern, answer):
            f.append(f"ответ совпал с запрещённым шаблоном {pattern}")

    cited = {(c.get("slug"), c.get("section")) for c in result.citations}
    cited_slugs = {c.get("slug") for c in result.citations}

    def has(ref: str) -> bool:
        slug, _, section = ref.partition("#")
        return (slug, section) in cited if section else slug in cited_slugs

    for ref in expect.get("citations", []):
        if not has(ref):
            f.append(f"нет ссылки на {ref}")
    if expect.get("citations_any") and not any(has(r) for r in expect["citations_any"]):
        f.append("нет ссылки ни на один из: " + ", ".join(expect["citations_any"]))
    if expect.get("no_citations") and result.citations:
        f.append("ответ ссылается на документы, хотя ответа в них нет")

    if expect.get("handoff") and not result.handoff:
        f.append("вопрос не передан человеку")
    if "pending" in expect:
        p = result.pending_action or {}
        for k, v in expect["pending"].items():
            if p.get(k) != v:
                f.append(f"действие: {k}={p.get(k)!r}, ожидалось {v!r}")
    if "side_effects" in expect and side_effects != expect["side_effects"]:
        f.append(f"действий в Битрикс24: {side_effects}, ожидалось {expect['side_effects']}")
    if "trace_contains" in expect and not any(expect["trace_contains"] in s["title"] for s in result.trace):
        f.append(f"в трассе нет шага «{expect['trace_contains']}»")
    if ref_rows is not None:
        f += check_reference(ref_rows, answer, result.sql)
    return f
