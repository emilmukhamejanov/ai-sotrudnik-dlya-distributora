import type { KeyboardEvent } from "react";
import type { Resolution } from "../App";
import type { PendingAction, SqlResult, Turn } from "../api";
import { cell, duration, num, ROUTE_LABELS, usd } from "../format";
import { renderMarkdown } from "../markdown";

interface Props {
  turn: Turn;
  selected: boolean;
  onSelect: () => void;
  onConfirm: (actionId: string, approved: boolean) => void;
  resolution: Resolution | undefined;
  canConfirm: boolean;
}

export default function AgentMessage({ turn, selected, onSelect, onConfirm, resolution, canConfirm }: Props) {
  const onKey = (e: KeyboardEvent<HTMLElement>) => {
    if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) {
      e.preventDefault();
      onSelect();
    }
  };
  const pending = turn.status === "needs_confirmation" ? turn.pending_action : null;
  const showAnswer = !pending; // при ожидании подтверждения текст ответа = описание действия в карточке

  return (
    <article
      className="msg-agent"
      aria-current={selected}
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={onKey}
      aria-label="Ответ агента — нажмите, чтобы открыть путевой лист"
    >
      <div className="msg-route">
        <span>Маршрут: {turn.route ? (ROUTE_LABELS[turn.route] ?? turn.route) : "—"}</span>
        <span>{duration(turn.duration_ms)}</span>
        {turn.usage.cost_usd !== null && <span>{usd(turn.usage.cost_usd)}</span>}
      </div>

      {showAnswer && <div className="answer" dangerouslySetInnerHTML={{ __html: renderMarkdown(turn.answer) }} />}

      {pending && (
        <ActionCard
          action={pending}
          resolution={resolution}
          disabled={!canConfirm}
          onConfirm={(approved) => onConfirm(pending.action_id, approved)}
        />
      )}

      {turn.citations.length > 0 && (
        <div className="citations" aria-label="Источники">
          {turn.citations.map((c) => (
            <span className="citation" key={`${c.slug}-${c.section}`}>
              {c.citation ?? c.slug}
            </span>
          ))}
        </div>
      )}

      {turn.sql?.ok && turn.sql.columns.length > 0 && <SqlBlock sql={turn.sql} />}

      <Stamp turn={turn} />
    </article>
  );
}

function Stamp({ turn }: { turn: Turn }) {
  if (turn.route === "blocked") return <div className="stamp danger stamp-in">Отклонено защитой</div>;
  if (turn.handoff) return <div className="stamp stamp-in">Передано сотруднику, {turn.handoff.ticket_id}</div>;
  if (turn.action_result?.ok)
    return <div className="stamp stamp-in">Создано в Битрикс24, {turn.action_result.external_id}</div>;
  if (turn.action_result && !turn.action_result.ok)
    return <div className="stamp danger stamp-in">Битрикс24: ошибка</div>;
  if (!turn.action_result && /ничего не создано|ештеңе құрылған жоқ/i.test(turn.answer))
    return <div className="stamp danger stamp-in">Отменено</div>;
  return null;
}

function ActionCard({
  action,
  resolution,
  disabled,
  onConfirm,
}: {
  action: PendingAction;
  resolution: Resolution | undefined;
  disabled: boolean;
  onConfirm: (approved: boolean) => void;
}) {
  const rows: [string, string | null][] = [
    ["Что", action.kind === "create_task" ? "Задача" : "Сделка в CRM"],
    ["Название", action.title],
    ["Ответственный", action.responsible_name],
    ["Срок", action.deadline ? action.deadline.split("-").reverse().join(".") : null],
    ["Клиент", action.client_name],
    ["Сумма", action.amount_kzt !== null ? `${num(action.amount_kzt)} ₸` : null],
    ["Описание", action.description && action.description !== action.title ? action.description : null],
  ];
  return (
    <div className="action-card" onClick={(e) => e.stopPropagation()}>
      <dl>
        {rows
          .filter(([, v]) => v)
          .map(([k, v]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
      </dl>
      {resolution ? (
        <div className="note">{resolution === "approved" ? "Вы подтвердили действие." : "Вы отменили действие."}</div>
      ) : (
        <div className="btn-row">
          <button className="btn btn-primary" disabled={disabled} onClick={() => onConfirm(true)}>
            Создать в Битрикс24
          </button>
          <button className="btn" disabled={disabled} onClick={() => onConfirm(false)}>
            Отменить
          </button>
        </div>
      )}
    </div>
  );
}

function SqlBlock({ sql }: { sql: SqlResult }) {
  const numeric = sql.columns.map((_, i) => sql.rows.some((r) => typeof r[i] === "number"));
  return (
    <details className="sql-block" onClick={(e) => e.stopPropagation()}>
      <summary>
        Данные: {sql.row_count} {sql.row_count === 1 ? "строка" : sql.row_count < 5 ? "строки" : "строк"}
        {sql.truncated ? " (обрезано)" : ""}
      </summary>
      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              {sql.columns.map((c, i) => (
                <th key={c} className={numeric[i] ? "num" : undefined}>
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sql.rows.map((r, ri) => (
              <tr key={ri}>
                {r.map((v, i) => (
                  <td key={i} className={numeric[i] ? "num" : undefined}>
                    {cell(v)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sql.assumptions.length > 0 && (
        <ul className="note">
          {sql.assumptions.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      )}
      {sql.sql && <pre className="code">{sql.sql}</pre>}
    </details>
  );
}
