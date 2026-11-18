import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, type EvalReport, type EvalReportListItem, type EvalRunState, type Meta, type Turn } from "../api";
import {
  CATEGORY_LABELS,
  duration,
  modelName,
  num,
  percent,
  plural,
  runTime,
  tokens,
  usd,
} from "../format";
import ColumnChart from "./ColumnChart";

interface Props {
  meta: Meta | null;
  sessionTurns: Turn[];
}

export default function QualityView({ meta, sessionTurns }: Props) {
  const [reports, setReports] = useState<EvalReportListItem[] | null>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [report, setReport] = useState<EvalReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadReports = useCallback(async (select?: string) => {
    try {
      const list = await api.evalReports();
      setReports(list);
      setSelectedName((cur) => select ?? cur ?? list[0]?.name ?? null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось загрузить отчёты");
      setReports([]);
    }
  }, []);

  useEffect(() => {
    loadReports();
  }, [loadReports]);

  useEffect(() => {
    if (!selectedName) return;
    setReport(null);
    api
      .evalReport(selectedName)
      .then(setReport)
      .catch(() => setError("Не удалось открыть отчёт"));
  }, [selectedName]);

  const chronological = useMemo(() => [...(reports ?? [])].reverse(), [reports]);
  const selectedIndex = chronological.findIndex((r) => r.name === selectedName);
  const s = report?.summary;

  return (
    <main className="quality">
      <div className="quality-inner">
        <div className="q-head">
          <div>
            <h1>Качество и расходы</h1>
            <p>
              Evals — 35 эталонных вопросов с автоматической проверкой: цифры сверяются с эталонным SQL, ссылки — с
              пунктами документов, действия в Битрикс24 считаются в заглушке.
            </p>
          </div>
          {s && (
            <div className="score" aria-label={`Пройдено ${s.passed} из ${s.cases}`}>
              <span className="score-big">{s.passed}</span>
              <span className="score-of">
                из {s.cases} в прогоне {runTime(report!.started_at)}
                <br />
                {modelName(report!.model)} и {modelName(report!.router_model)}
              </span>
            </div>
          )}
        </div>

        {error && (
          <p className="error-line" role="alert">
            {error}
          </p>
        )}

        <div className="q-grid">
          <div>
            <section className="panel">
              <h2>Прогоны</h2>
              <p className="panel-sub">Сколько вопросов из набора агент прошёл в каждом прогоне.</p>
              {reports && reports.length === 0 && (
                <p className="note">Прогонов пока нет. Запустите первую проверку справа.</p>
              )}
              {chronological.length > 0 && (
                <ColumnChart
                  ariaLabel="Пройдено вопросов по прогонам"
                  data={chronological.map((r) => ({
                    label: runTime(r.name).split(", ")[1],
                    value: r.summary.passed,
                    tooltip: `${runTime(r.name)}: ${r.summary.passed} из ${r.summary.cases} (${num(r.summary.pass_rate, 1)}%)`,
                  }))}
                  yMax={Math.max(...chronological.map((r) => r.summary.cases))}
                  ticks={ticksFor(Math.max(...chronological.map((r) => r.summary.cases)))}
                  highlight={selectedIndex >= 0 ? selectedIndex : null}
                  formatValue={(v) => String(v)}
                  onSelect={(i) => setSelectedName(chronological[i].name)}
                />
              )}
              {reports && reports.length > 0 && (
                <ul className="runs-list" aria-label="Список прогонов">
                  {reports.map((r) => (
                    <li key={r.name}>
                      <button
                        className="run-item"
                        aria-current={r.name === selectedName}
                        onClick={() => setSelectedName(r.name)}
                      >
                        <span>{runTime(r.name)}</span>
                        <span>
                          {r.summary.passed} из {r.summary.cases}
                        </span>
                        <span className="muted">{usd(r.summary.cost_usd)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {report && <Categories report={report} />}
            {report && <Failures report={report} />}
          </div>

          <div>
            <RunEvals onDone={(name) => loadReports(name)} lastCost={reports?.[0]?.summary.cost_usd ?? null} />
            {report && <RunCost report={report} />}
            <SessionCost turns={sessionTurns} meta={meta} />
          </div>
        </div>
      </div>
    </main>
  );
}

function ticksFor(max: number): number[] {
  const step = max <= 10 ? 2 : max <= 40 ? 10 : 20;
  const out = [];
  for (let t = 0; t <= max; t += step) out.push(t);
  return out;
}

function Categories({ report }: { report: EvalReport }) {
  const order = Object.keys(CATEGORY_LABELS);
  const cats = Object.entries(report.summary.by_category).sort(
    ([a], [b]) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99),
  );
  return (
    <section className="panel">
      <h2>По категориям</h2>
      <p className="panel-sub">Прогон {runTime(report.started_at)}</p>
      {cats.map(([cat, v]) => (
        <div className="cat-row" key={cat}>
          <span>{CATEGORY_LABELS[cat] ?? cat}</span>
          <div className="meter" role="img" aria-label={`${v.passed} из ${v.cases}`}>
            <span style={{ width: `${percent(v.passed, v.cases)}%` }} />
          </div>
          <span className="cat-value">
            {v.passed} / {v.cases}
          </span>
        </div>
      ))}
    </section>
  );
}

function Failures({ report }: { report: EvalReport }) {
  const failed = report.cases.filter((c) => !c.passed);
  return (
    <section className="panel">
      <h2>{failed.length ? "Что не прошло" : "Все вопросы пройдены"}</h2>
      <p className="panel-sub">
        {failed.length
          ? "Причины — как их записала автоматическая проверка, и начало ответа агента."
          : "В этом прогоне агент ответил правильно на все вопросы набора."}
      </p>
      {failed.map((c) => (
        <div className="failure" key={c.id}>
          <div className="failure-head">
            <span className="failure-id">{c.id}</span>
            <span className="failure-q">{c.turns[0]?.input}</span>
          </div>
          {c.error && <ul><li>{c.error}</li></ul>}
          {c.turns
            .filter((t) => !t.passed)
            .map((t, i) => (
              <div key={i}>
                <ul>
                  {t.failures.map((f) => (
                    <li key={f}>{f}</li>
                  ))}
                  {(t.notes ?? []).map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
                <div className="failure-answer">{t.answer.slice(0, 260)}{t.answer.length > 260 ? "…" : ""}</div>
              </div>
            ))}
        </div>
      ))}
    </section>
  );
}

function RunEvals({ onDone, lastCost }: { onDone: (name?: string) => void; lastCost: number | null }) {
  const [confirming, setConfirming] = useState(false);
  const [run, setRun] = useState<EvalRunState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!run || run.status !== "running") return;
    const t = window.setTimeout(async () => {
      try {
        const next = await api.evalRun(run.run_id);
        setRun(next);
        if (next.status === "done") onDone();
        if (next.status === "error") setError(next.error ?? "Прогон завершился с ошибкой");
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Потеряна связь с сервером");
      }
    }, 3000);
    return () => window.clearTimeout(t);
  }, [run, onDone]);

  const start = async () => {
    setConfirming(false);
    setError(null);
    try {
      const { run_id, total } = await api.startEvals();
      setRun({ run_id, status: "running", done: 0, total, results: [] });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось запустить прогон");
    }
  };

  const running = run?.status === "running";
  const failed = run?.results.filter((r) => !r.passed) ?? [];

  return (
    <section className="panel">
      <h2>Проверить агента</h2>
      <p className="panel-sub">
        Прогон задаёт агенту все вопросы набора на настоящей модели. Задачи в Битрикс24 при этом не создаются.
      </p>
      {!running && !confirming && (
        <button className="btn btn-primary" onClick={() => setConfirming(true)}>
          Запустить проверку
        </button>
      )}
      {confirming && (
        <div>
          <p style={{ margin: "0 0 10px" }}>
            Около 6 минут и {lastCost !== null ? `≈ ${usd(lastCost)}` : "≈ $0,6"} за вызовы модели. Запустить?
          </p>
          <div className="btn-row">
            <button className="btn btn-primary" onClick={start}>
              Запустить
            </button>
            <button className="btn" onClick={() => setConfirming(false)}>
              Не сейчас
            </button>
          </div>
        </div>
      )}
      {run && (
        <div aria-live="polite">
          <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={run.total} aria-valuenow={run.done}>
            <span style={{ width: `${percent(run.done, run.total)}%` }} />
          </div>
          <div className="note">
            {run.status === "done"
              ? `Готово: ${run.total - failed.length} из ${run.total}. Отчёт добавлен в список прогонов.`
              : `Проверено ${run.done} из ${run.total}${failed.length ? `, не прошло: ${failed.map((f) => f.id).join(", ")}` : ""}`}
          </div>
        </div>
      )}
      {error && (
        <p className="error-line" role="alert" style={{ marginTop: 10 }}>
          {error}
        </p>
      )}
    </section>
  );
}

function RunCost({ report }: { report: EvalReport }) {
  const s = report.summary;
  const questions = report.cases.reduce((n, c) => n + c.turns.length, 0);
  return (
    <section className="panel">
      <h2>Расходы прогона</h2>
      <p className="panel-sub">
        {questions} {plural(questions, "обращение", "обращения", "обращений")} к агенту за {duration(report.duration_s * 1000)}
      </p>
      <div className="stats">
        <div>
          <div className="stat-value money">{usd(s.cost_usd)}</div>
          <div className="stat-label">весь прогон</div>
        </div>
        <div>
          <div className="stat-value money">{s.cost_usd !== null ? usd(s.cost_usd / Math.max(1, questions)) : "—"}</div>
          <div className="stat-label">в среднем за ответ</div>
        </div>
        <div>
          <div className="stat-value">{duration(s.latency_ms.median)}</div>
          <div className="stat-label">медиана ответа, p95 {duration(s.latency_ms.p95)}</div>
        </div>
        <div>
          <div className="stat-value">{tokens(s.tokens.input + s.tokens.output)}</div>
          <div className="stat-label">
            токенов: {tokens(s.tokens.input)} на входе, {tokens(s.tokens.output)} на выходе
          </div>
        </div>
      </div>
      {s.cost_usd === null && (
        <p className="note">
          Стоимость этого прогона не записана — он сделан до того, как в агенте появились цены моделей. В новых
          прогонах она считается сама (для моделей, кроме Claude Sonnet 5.5 и Haiku 5.5, — по LLM_PRICE_* в .env).
        </p>
      )}
    </section>
  );
}

function SessionCost({ turns, meta }: { turns: Turn[]; meta: Meta | null }) {
  const byModel = new Map<string, { tokens: number; cost: number | null; calls: number }>();
  for (const t of turns) {
    for (const [model, u] of Object.entries(t.usage.by_model ?? {})) {
      const cur = byModel.get(model) ?? { tokens: 0, cost: 0, calls: 0 };
      cur.tokens += u.input_tokens + u.output_tokens;
      cur.calls += u.calls;
      cur.cost = cur.cost === null || u.cost_usd === null ? null : cur.cost + u.cost_usd;
      byModel.set(model, cur);
    }
  }
  const costs = turns.map((t) => t.usage.cost_usd);
  const total = costs.every((c) => c !== null) ? costs.reduce<number>((a, c) => a + (c ?? 0), 0) : null;
  const maxMs = Math.max(1000, ...turns.map((t) => t.duration_ms));

  return (
    <section className="panel">
      <h2>Этот диалог</h2>
      <p className="panel-sub">
        {turns.length
          ? `${turns.length} ${plural(turns.length, "ответ", "ответа", "ответов")} агента во вкладке «Диалог»`
          : "Задайте агенту вопрос во вкладке «Диалог» — здесь появятся время и стоимость ответов."}
      </p>
      {turns.length > 0 && (
        <>
          <div className="stats">
            <div>
              <div className="stat-value money">{usd(total)}</div>
              <div className="stat-label">стоимость диалога</div>
            </div>
            <div>
              <div className="stat-value">
                {duration(turns.reduce((a, t) => a + t.duration_ms, 0) / turns.length)}
              </div>
              <div className="stat-label">в среднем на ответ</div>
            </div>
          </div>
          <ColumnChart
            ariaLabel="Время каждого ответа, секунды"
            data={turns.map((t, i) => ({
              label: String(i + 1),
              value: t.duration_ms / 1000,
              tooltip: `Ответ ${i + 1}: ${duration(t.duration_ms)}, ${usd(t.usage.cost_usd)}`,
            }))}
            yMax={Math.ceil(maxMs / 5000) * 5}
            ticks={Array.from({ length: Math.ceil(maxMs / 5000) + 1 }, (_, i) => i * 5)}
            formatTick={(v) => `${v} с`}
            height={150}
          />
          <div className="legend">
            {[...byModel.entries()].map(([model, u]) => (
              <span key={model}>
                <span
                  className="swatch"
                  style={{ background: model === meta?.router_model ? "var(--series-2)" : "var(--series-1)" }}
                />
                {modelName(model)}: {u.calls} {plural(u.calls, "вызов", "вызова", "вызовов")}, {tokens(u.tokens)},{" "}
                {usd(u.cost)}
              </span>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
