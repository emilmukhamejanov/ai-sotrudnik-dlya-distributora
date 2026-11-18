import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { Entry, Resolution } from "../App";
import AgentMessage from "./AgentMessage";
import RouteSheet from "./RouteSheet";

interface Props {
  entries: Entry[];
  selected: number | null;
  onSelect: (id: number) => void;
  busy: boolean;
  onSend: (text: string) => void;
  onConfirm: (actionId: string, approved: boolean) => void;
  onReset: () => void;
  resolutions: Record<string, Resolution>;
  exportDate: string | null;
}

const STARTERS: { title: string; questions: string[] }[] = [
  {
    title: "Цифры из 1С",
    questions: [
      "Сколько продали напитков в Алматы за октябрь и как это к прошлому году?",
      "Топ-3 менеджера по выручке за октябрь 2026",
      "Кому из должников уже должны были заблокировать отгрузки?",
    ],
  },
  {
    title: "Регламенты и договоры",
    questions: [
      "Какая максимальная скидка для HoReCa без согласования?",
      "В какой срок покупатель может заявить о скрытых недостатках товара?",
      "Что пишет поставщик чая об изменении цен?",
    ],
  },
  {
    title: "Задачи в Битрикс24",
    questions: [
      "Поставь задачу Иванову позвонить клиенту с просрочкой до пятницы",
      "Поставь задачу Петренко подготовить отчёт по возвратам",
    ],
  },
  {
    title: "Қазақша",
    questions: ["Тапсырыстың ең аз сомасы қанша?", "Қазан айында Астанадағы клиенттерге қанша сатылды?"],
  },
];

export default function DialogView(props: Props) {
  const { entries, selected, onSelect, busy, onSend, onConfirm, onReset, resolutions } = props;
  const [draft, setDraft] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (entries.length === 0) return;
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [entries.length, busy]);

  const submit = () => {
    if (!draft.trim() || busy) return;
    onSend(draft);
    setDraft("");
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const selectedTurn = entries.find((e) => e.kind === "agent" && e.id === selected);

  return (
    <main className="dialog">
      <section className="conversation" aria-label="Диалог с ИИ-сотрудником">
        <div className="messages" ref={listRef}>
          <div className="messages-inner">
            {entries.length === 0 && (
              <div className="welcome">
                <h1>Спросите о продажах, долгах или регламентах</h1>
                <p>
                  Агент считает по выгрузке из 1С{props.exportDate ? ` на ${props.exportDate.split("-").reverse().join(".")}` : ""},
                  отвечает по документам со ссылкой на пункт и ставит задачи в Битрикс24 только после вашего
                  подтверждения. Справа — путевой лист каждого ответа: какие шаги он сделал, сколько это заняло и
                  стоило.
                </p>
                <div className="starters">
                  {STARTERS.map((g) => (
                    <div className="starter-group" key={g.title}>
                      <h2>{g.title}</h2>
                      {g.questions.map((q) => (
                        <button key={q} className="starter" onClick={() => onSend(q)} disabled={busy}>
                          {q}
                        </button>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {entries.map((e) => {
              if (e.kind === "user") {
                return (
                  <div className="msg-user" key={e.id}>
                    {e.text}
                  </div>
                );
              }
              if (e.kind === "error") {
                return (
                  <div className="error-line" key={e.id} role="alert">
                    {e.text}
                  </div>
                );
              }
              const isLastAgent = entries.filter((x) => x.kind === "agent").at(-1)?.id === e.id;
              return (
                <AgentMessage
                  key={e.id}
                  turn={e.turn}
                  selected={selected === e.id}
                  onSelect={() => onSelect(e.id)}
                  onConfirm={onConfirm}
                  resolution={e.turn.pending_action ? resolutions[e.turn.pending_action.action_id] : undefined}
                  canConfirm={isLastAgent && !busy}
                />
              );
            })}

            {busy && <div className="thinking">Агент работает</div>}
          </div>
        </div>

        <div className="composer">
          <div className="composer-inner">
            <textarea
              ref={inputRef}
              rows={1}
              value={draft}
              placeholder="Вопрос агенту: цифры, правила или задача в Битрикс24"
              aria-label="Сообщение агенту"
              onChange={(e) => {
                setDraft(e.target.value);
                e.target.style.height = "auto";
                e.target.style.height = `${e.target.scrollHeight}px`;
              }}
              onKeyDown={onKey}
            />
            <button className="btn btn-primary" onClick={submit} disabled={busy || !draft.trim()}>
              Отправить
            </button>
          </div>
          <div className="composer-hint">
            <span>Enter — отправить, Shift+Enter — новая строка. ИИН и телефоны скрываются до отправки в модель.</span>
            {entries.length > 0 && (
              <button className="link-btn" onClick={onReset} disabled={busy}>
                Новый диалог
              </button>
            )}
          </div>
        </div>
      </section>

      <RouteSheet turn={selectedTurn && selectedTurn.kind === "agent" ? selectedTurn.turn : null} />
    </main>
  );
}
