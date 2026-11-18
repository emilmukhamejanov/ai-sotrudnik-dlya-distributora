"""python -m dala_agent.evals [--category docs …] [--case data-01 …]"""

from __future__ import annotations

import argparse

from ..agent.factory import build_deps
from ..config import get_settings
from ..db import AgentSQLExecutor
from .runner import load_cases, run_evals, save_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Прогон evals на настоящей модели и базе")
    parser.add_argument("--category", nargs="*", help="data, docs, data_and_docs, action, human, security")
    parser.add_argument("--case", nargs="*", help="id кейсов")
    args = parser.parse_args()

    settings = get_settings()
    cases = load_cases(ids=args.case, categories=args.category)
    deps = build_deps(settings)
    executor = AgentSQLExecutor(settings.agent_dsn())

    def progress(i, total, rep):
        mark = "ok  " if rep.passed else "FAIL"
        print(f"[{i:>2}/{total}] {mark} {rep.id:<10} {rep.duration_ms / 1000:5.1f} с", flush=True)
        for t in rep.turns:
            for f in t.failures:
                print(f"           - {f}")
        if rep.error:
            print(f"           - ошибка: {rep.error}")

    run = run_evals(deps, executor, cases, price=settings.price(), on_progress=progress)
    s = run["summary"]
    json_path, md_path = save_report(run)
    print(f"\nПройдено {s['passed']} из {s['cases']} ({s['pass_rate']}%). Отчёт: {md_path}")


if __name__ == "__main__":
    main()
