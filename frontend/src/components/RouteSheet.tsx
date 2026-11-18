import type { ReactNode } from "react";
import type { TraceStep, Turn } from "../api";
import { duration, modelName, NODE_LABELS, ROUTE_LABELS, tokens, usd } from "../format";

/** «Путевой лист» ответа: остановки графа по порядку, время, токены и детали каждой. */
export default function RouteSheet({ turn }: { turn: Turn | null }) {
  if (!turn) {
    return (
      <aside className="routesheet is-empty" aria-label="Путевой лист ответа">
        <h2>Путевой лист ответа</h2>
        <p className="routesheet-sub">Шаги агента появятся здесь после первого ответа.</p>
        <div className="empty-sheet">
          Для каждого ответа видно, куда роутер направил вопрос, какой SQL выполнился, какие пункты документов
          нашлись, что скрыла защита, сколько это заняло и стоило.
        </div>
      </aside>
    );
  }

  const models = Object.entries(turn.usage.by_model ?? {});
  const stepsTotal = Math.max(1, ...turn.trace.map((s) => s.duration_ms));

  return (
    <aside className="routesheet" aria-label="Путевой лист ответа">
      <h2>Путевой лист ответа</h2>
      <p className="routesheet-sub">
        {turn.trace.length} {turn.trace.length === 1 ? "шаг" : turn.trace.length < 5 ? "шага" : "шагов"}, нажмите на
        шаг, чтобы раскрыть детали
      </p>

      <div className="totals">
        <div className="total">
          <div className="total-value">{duration(turn.duration_ms)}</div>
          <div className="total-label">время ответа</div>
        </div>
        <div className="total">
          <div className="total-value">{tokens(turn.usage.input_tokens + turn.usage.output_tokens)}</div>
          <div className="total-label">токенов</div>
        </div>
        <div className="total">
          <div className="total-value money">{usd(turn.usage.cost_usd)}</div>
          <div className="total-label">стоимость</div>
        </div>
      </div>

      {models.length > 0 && (
        <div className="legend" style={{ margin: "-8px 0 18px" }}>
          {models.map(([model, u]) => (
            <span key={model}>
              <span
                className="swatch"
                style={{ background: isRouterModel(model, turn) ? "var(--series-2)" : "var(--series-1)" }}
              />
              {modelName(model)}: {u.calls} {u.calls === 1 ? "вызов" : u.calls < 5 ? "вызова" : "вызовов"},{" "}
              {tokens(u.input_tokens + u.output_tokens)}, {usd(u.cost_usd)}
            </span>
          ))}
        </div>
      )}

      <ol className="stops">
        {turn.trace.map((step, i) => (
          <Stop key={i} step={step} max={stepsTotal} routerModel={routerModelOf(turn)} />
        ))}
      </ol>
    </aside>
  );
}

function routerModelOf(turn: Turn): string | null {
  return turn.trace.find((s) => s.node === "router")?.usage?.model ?? null;
}

function isRouterModel(model: string, turn: Turn): boolean {
  const router = routerModelOf(turn);
  const others = turn.trace.filter((s) => s.node !== "router" && s.usage?.model === model);
  return router === model && others.length === 0;
}

function Stop({ step, max, routerModel }: { step: TraceStep; max: number; routerModel: string | null }) {
  const d = step.detail ?? {};
  const warn =
    Boolean(d.error) ||
    step.title.includes("ошибка") ||
    step.title.includes("отклон") ||
    step.title.includes("нет") ||
    step.node === "handoff";
  const cls = ["stop", warn ? "warn" : "", step.node === "guard" || step.node === "finalize" ? "guard" : ""].join(" ");
  const barClass = step.usage && step.usage.model === routerModel && step.node === "router" ? "router" : "";
  const details = renderDetails(step);

  return (
    <li className={cls}>
      <span className="stop-dot" aria-hidden="true" />
      <div className="stop-head">
        <span className="stop-name">{NODE_LABELS[step.node] ?? step.node}</span>
        <span className="stop-time">{duration(step.duration_ms)}</span>
      </div>
      <div className="stop-title">{stepTitle(step)}</div>
      {step.usage ? (
        <>
          <div className="stop-bar" aria-hidden="true">
            <span className={barClass} style={{ width: `${Math.max(2, (step.duration_ms / max) * 100)}%` }} />
          </div>
          <div className="stop-usage">
            {modelName(step.usage.model)}: {tokens(step.usage.input_tokens)} на входе, {tokens(step.usage.output_tokens)} на
            выходе
          </div>
        </>
      ) : null}
      {details && (
        <details>
          <summary>Детали</summary>
          {details}
        </details>
      )}
    </li>
  );
}

/** Заголовки шагов приходят из бэкенда; коды маршрутов и режимов переводим на язык пользователя. */
export function stepTitle(step: TraceStep): string {
  return step.title
    .replace(/^Маршрут: (\w+)/, (_, r: string) => `Маршрут: ${ROUTE_LABELS[r] ?? r}`)
    .replace("(stub)", "(заглушка)")
    .replace("(webhook)", "(портал)");
}

type Rec = Record<string, unknown>;
const str = (v: unknown) => (v === null || v === undefined ? "" : String(v));

function renderDetails(step: TraceStep): ReactNode {
  const d = step.detail as Rec;
  switch (step.node) {
    case "router":
      return (
        <dl className="kv">
          <dt>Почему</dt>
          <dd>{str(d.reason)}</dd>
          {d.search_query ? (
            <>
              <dt>Поиск по</dt>
              <dd>{str(d.search_query)}</dd>
            </>
          ) : null}
          {d.error ? (
            <>
              <dt>Ошибка</dt>
              <dd>{str(d.error)}</dd>
            </>
          ) : null}
        </dl>
      );
    case "guard":
      return d.kinds ? (
        <dl className="kv">
          <dt>Скрыто</dt>
          <dd>{humanKinds(d.found as Record<string, number>)}</dd>
        </dl>
      ) : (
        <p className="note">Сообщение похоже на попытку изменить правила агента — модель не вызывалась.</p>
      );
    case "docs":
      return <DocsDetails d={d} />;
    case "sql":
      return <SqlDetails d={d} />;
    case "plan_action": {
      const draft = (d.draft ?? {}) as Rec;
      return (
        <dl className="kv">
          {Object.entries(draft)
            .filter(([, v]) => v !== null && v !== "")
            .map(([k, v]) => (
              <div key={k} style={{ display: "contents" }}>
                <dt>{k}</dt>
                <dd>{str(v)}</dd>
              </div>
            ))}
        </dl>
      );
    }
    case "execute":
      return (
        <dl className="kv">
          <dt>Режим</dt>
          <dd>{d.mode === "stub" ? "заглушка (журнал var/bitrix_stub.jsonl)" : "портал (вебхук)"}</dd>
          <dt>Номер</dt>
          <dd>{str(d.external_id) || str(d.error)}</dd>
        </dl>
      );
    case "handoff":
      return (
        <dl className="kv">
          <dt>Обращение</dt>
          <dd>{str(d.ticket)}</dd>
          <dt>Причина</dt>
          <dd>{str(d.reason) || "—"}</dd>
        </dl>
      );
    case "compose":
      return d.error ? <p className="attempt-error">{str(d.error)}</p> : null;
    default:
      return null;
  }
}

function humanKinds(found: Record<string, number> | undefined): string {
  const names: Record<string, string> = { iin: "ИИН", phone: "телефон", email: "e-mail", iban: "счёт", card: "карта" };
  return Object.entries(found ?? {})
    .map(([k, n]) => `${names[k] ?? k}: ${n}`)
    .join(", ");
}

function DocsDetails({ d }: { d: Rec }) {
  const retrieved = (d.retrieved ?? []) as Rec[];
  const sanitized = (d.sanitized ?? []) as Rec[];
  return (
    <>
      {sanitized.length > 0 && (
        <p className="attempt-error">
          Защита вырезала из{" "}
          {sanitized.map((s) => `«${str(s.slug)}»`).join(", ")} скрытые комментарии и абзацы, адресованные ИИ.
        </p>
      )}
      {d.error ? <p className="attempt-error">{str(d.error)}</p> : null}
      <ul className="fragments">
        {retrieved.map((f, i) => (
          <li className="fragment" key={i}>
            <div className="fragment-head">
              <span>
                {str(f.title)}
                {f.section ? `, п. ${str(f.section)}` : ""}
              </span>
              <span className={`tag ${f.trust_level === "external" ? "external" : ""}`}>
                {f.trust_level === "external" ? "внешний" : `RRF ${Number(f.score).toFixed(3)}`}
              </span>
            </div>
            <div className="fragment-preview">{str(f.preview)}</div>
          </li>
        ))}
      </ul>
    </>
  );
}

function SqlDetails({ d }: { d: Rec }) {
  const attempts = (d.attempts ?? []) as Rec[];
  return (
    <>
      {attempts.map((a, i) => (
        <div key={i}>
          {attempts.length > 1 && <div className="note">Попытка {i + 1}</div>}
          {a.checked_sql || a.sql ? <pre className="code">{str(a.checked_sql || a.sql)}</pre> : null}
          {a.error ? <div className="attempt-error">{str(a.error)}</div> : null}
        </div>
      ))}
      {d.ok ? (
        <p className="note">
          Строк: {str(d.row_count)}
          {d.truncated ? ", результат обрезан" : ""}. Выполнено под ролью agent_reader, только чтение.
        </p>
      ) : null}
    </>
  );
}
