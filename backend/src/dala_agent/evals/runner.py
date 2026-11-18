"""Прогон evals: каждый кейс — новый диалог с настоящей моделью и базой, Битрикс24 — всегда заглушка.

    python -m dala_agent.evals                     # все кейсы
    python -m dala_agent.evals --category docs     # одна категория
    python -m dala_agent.evals --case data-01 sec-02

Отчёт: var/evals/<время>.json и .md — точность по категориям, причины провалов, время и токены.
"""

from __future__ import annotations

import json
import statistics
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path

import yaml

from ..actions.bitrix import BitrixStub
from ..agent.graph import AgentDeps
from ..agent.service import Agent
from ..config import REPO_ROOT
from ..pricing import Pricing
from .checks import check_turn

CASES_PATH = REPO_ROOT / "evals" / "cases.yaml"
REPORTS_DIR = REPO_ROOT / "var" / "evals"


def load_cases(path: Path = CASES_PATH, *, ids: list[str] | None = None, categories: list[str] | None = None):
    cases = yaml.safe_load(path.read_text(encoding="utf-8"))
    for c in cases:
        if "turns" not in c:
            c["turns"] = [{"message": c["question"], "expect": c.get("expect", {})}]
    if ids:
        cases = [c for c in cases if c["id"] in ids]
    if categories:
        cases = [c for c in cases if c["category"] in categories]
    return cases


@dataclass
class TurnReport:
    input: str
    passed: bool
    failures: list[str]
    route: str | None
    status: str
    answer: str
    sql: str | None
    citations: list[str]
    duration_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None
    notes: list[str] = field(default_factory=list)  # ошибки инструментов из трассы — чтобы понять причину провала


@dataclass
class CaseReport:
    id: str
    category: str
    passed: bool
    turns: list[TurnReport] = field(default_factory=list)
    error: str | None = None

    @property
    def duration_ms(self) -> int:
        return sum(t.duration_ms for t in self.turns)


def run_case(case: dict, deps: AgentDeps, executor, pricing: Pricing | None = None) -> CaseReport:
    stub = BitrixStub()
    agent = Agent(replace(deps, bitrix=stub), pricing=pricing)
    thread = f"eval-{case['id']}-{uuid.uuid4().hex[:6]}"
    report = CaseReport(id=case["id"], category=case["category"], passed=True)
    for turn in case["turns"]:
        expect = turn.get("expect", {})
        before = len([r for r in stub.records if r["type"] != "handoff"])
        try:
            if "message" in turn:
                label = turn["message"]
                result = agent.chat(thread, turn["message"])
            else:
                approved = bool(turn.get("confirm"))
                label = "[подтверждение]" if approved else "[отказ]"
                pending = agent.pending(thread)
                action_id = pending["action"]["action_id"] if pending else None
                result = agent.confirm(thread, approved=approved, action_id=action_id)
        except Exception as e:  # noqa: BLE001 — любая ошибка агента = провал кейса, прогон идёт дальше
            report.passed = False
            report.error = f"{type(e).__name__}: {e}"
            break
        side_effects = len([r for r in stub.records if r["type"] != "handoff"]) - before
        refs = expect.get("reference_sql")
        refs = [refs] if isinstance(refs, str) else (refs or [None])
        # Несколько эталонов — несколько допустимых прочтений вопроса: засчитываем лучший.
        attempts = [check_turn(expect, result, executor.fetch(r) if r else None, side_effects) for r in refs]
        failures = min(attempts, key=len)
        report.turns.append(
            TurnReport(
                input=label,
                passed=not failures,
                failures=failures,
                route=result.route,
                status=result.status,
                answer=result.answer,
                sql=(result.sql or {}).get("sql"),
                citations=[c.get("citation") or c.get("slug") for c in result.citations],
                duration_ms=result.duration_ms,
                input_tokens=result.usage.get("input_tokens", 0),
                output_tokens=result.usage.get("output_tokens", 0),
                cost_usd=result.usage.get("cost_usd"),
                notes=_trace_notes(result.trace),
            )
        )
        if failures:
            report.passed = False
    return report


def _trace_notes(trace: list[dict]) -> list[str]:
    notes = []
    for s in trace:
        d = s.get("detail") or {}
        if d.get("error"):
            notes.append(f"{s['node']}: {d['error'][:300]}")
        for a in d.get("attempts") or []:
            if a.get("error"):
                notes.append(f"sql: {a['error'][:300]}")
    return notes


def summarize(reports: list[CaseReport]) -> dict:
    by_cat: dict[str, list[CaseReport]] = {}
    for r in reports:
        by_cat.setdefault(r.category, []).append(r)
    turns = [t for r in reports for t in r.turns]
    durations = sorted(t.duration_ms for t in turns) or [0]
    tin = sum(t.input_tokens for t in turns)
    tout = sum(t.output_tokens for t in turns)
    return {
        "cases": len(reports),
        "passed": sum(r.passed for r in reports),
        "pass_rate": round(sum(r.passed for r in reports) / len(reports) * 100, 1) if reports else 0.0,
        "by_category": {
            cat: {"cases": len(rs), "passed": sum(r.passed for r in rs)} for cat, rs in sorted(by_cat.items())
        },
        "latency_ms": {
            "median": int(statistics.median(durations)),
            "p95": durations[min(len(durations) - 1, int(len(durations) * 0.95))],
            "max": durations[-1],
        },
        "tokens": {"input": tin, "output": tout},
        "cost_usd": (
            round(sum(t.cost_usd for t in turns), 4) if turns and all(t.cost_usd is not None for t in turns) else None
        ),
    }


def run_evals(
    deps: AgentDeps,
    executor,
    cases: list[dict],
    *,
    pricing: Pricing | None = None,
    on_progress: Callable[[int, int, CaseReport], None] | None = None,
) -> dict:
    started = time.monotonic()
    reports = []
    for i, case in enumerate(cases, 1):
        rep = run_case(case, deps, executor, pricing)
        reports.append(rep)
        if on_progress:
            on_progress(i, len(cases), rep)
    return {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "duration_s": round(time.monotonic() - started, 1),
        "model": getattr(deps.llm, "model", ""),
        "router_model": getattr(deps.router_llm, "model", ""),
        "summary": summarize(reports),
        "cases": [asdict(r) | {"duration_ms": r.duration_ms} for r in reports],
    }


def to_markdown(run: dict) -> str:
    s = run["summary"]
    lines = [
        f"# Evals — {run['started_at']}",
        "",
        f"Модель: `{run['model']}`, роутер: `{run['router_model']}`. Прогон: {run['duration_s']} с.",
        "",
        f"**Пройдено: {s['passed']} из {s['cases']} ({s['pass_rate']}%)**",
        "",
        "| Категория | Пройдено |",
        "|---|---|",
        *[f"| {cat} | {v['passed']} / {v['cases']} |" for cat, v in s["by_category"].items()],
        "",
        f"Время ответа: медиана {s['latency_ms']['median'] / 1000:.1f} с, p95 {s['latency_ms']['p95'] / 1000:.1f} с. "
        f"Токены: {s['tokens']['input']:,} входных, {s['tokens']['output']:,} выходных".replace(",", " ")
        + (f", ≈ ${s['cost_usd']}." if s["cost_usd"] is not None else "."),
        "",
    ]
    failed = [c for c in run["cases"] if not c["passed"]]
    if failed:
        lines += ["## Провалы", ""]
        for c in failed:
            lines.append(f"### {c['id']} ({c['category']})")
            if c.get("error"):
                lines.append(f"- ошибка: {c['error']}")
            for t in c["turns"]:
                if t["failures"]:
                    lines.append(f"- «{t['input']}» → маршрут {t['route']}, статус {t['status']}")
                    lines += [f"  - {x}" for x in t["failures"]]
                    lines.append(f"  - ответ: {t['answer'][:300].replace(chr(10), ' ')}")
                    lines += [f"  - трасса: {n}" for n in t.get("notes", [])]
            lines.append("")
    return "\n".join(lines)


def save_report(run: dict, directory: Path | None = None) -> tuple[Path, Path]:
    directory = directory or REPORTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = directory / f"{stamp}.json"
    md_path = directory / f"{stamp}.md"
    json_path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(to_markdown(run), encoding="utf-8")
    return json_path, md_path
