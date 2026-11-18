import { useCallback, useEffect, useState } from "react";
import { accessCode, api, ApiError, type Meta, type Turn } from "./api";
import DialogView from "./components/DialogView";
import Header from "./components/Header";
import QualityView from "./components/QualityView";

export type Entry =
  | { kind: "user"; id: number; text: string }
  | { kind: "agent"; id: number; turn: Turn }
  | { kind: "error"; id: number; text: string };

export type Resolution = "approved" | "rejected";
export type Tab = "dialog" | "quality";

let nextId = 1;

export default function App() {
  const [tab, setTab] = useState<Tab>("dialog");
  const [meta, setMeta] = useState<Meta | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [resolutions, setResolutions] = useState<Record<string, Resolution>>({});
  const [needCode, setNeedCode] = useState<string | null>(null); // сообщение, которое ждёт кода доступа

  const refreshMeta = useCallback(() => {
    api.meta().then(setMeta).catch(() => setMeta(null));
  }, []);
  useEffect(refreshMeta, [refreshMeta]);

  const pushAgent = useCallback((turn: Turn) => {
    const id = nextId++;
    setEntries((e) => [...e, { kind: "agent", id, turn }]);
    setSelected(id);
    setThreadId(turn.thread_id);
  }, []);

  const pushError = useCallback((err: unknown) => {
    const text = err instanceof ApiError ? err.message : "Не удалось получить ответ. Попробуйте ещё раз.";
    setEntries((e) => [...e, { kind: "error", id: nextId++, text }]);
  }, []);

  const send = useCallback(
    async (text: string) => {
      const message = text.trim();
      if (!message || busy) return;
      setEntries((e) => [...e, { kind: "user", id: nextId++, text: message }]);
      setBusy(true);
      try {
        pushAgent(await api.chat(message, threadId));
        if (meta?.demo_mode.enabled) refreshMeta(); // обновить остаток дневного лимита
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          setEntries((e) => e.slice(0, -1));
          setNeedCode(message);
        } else {
          pushError(err);
        }
      } finally {
        setBusy(false);
      }
    },
    [busy, threadId, pushAgent, pushError, meta, refreshMeta],
  );

  const submitCode = useCallback(
    (code: string) => {
      accessCode.set(code.trim());
      const pending = needCode;
      setNeedCode(null);
      if (pending) void send(pending);
    },
    [needCode, send],
  );

  const confirm = useCallback(
    async (actionId: string, approved: boolean) => {
      if (!threadId || busy) return;
      setBusy(true);
      try {
        const turn = await api.confirm(threadId, approved, approved ? actionId : null);
        setResolutions((r) => ({ ...r, [actionId]: approved ? "approved" : "rejected" }));
        pushAgent(turn);
      } catch (err) {
        pushError(err);
      } finally {
        setBusy(false);
      }
    },
    [threadId, busy, pushAgent, pushError],
  );

  const reset = useCallback(() => {
    setEntries([]);
    setThreadId(null);
    setSelected(null);
    setResolutions({});
  }, []);

  const turns = entries.flatMap((e) => (e.kind === "agent" ? [e.turn] : []));

  return (
    <div className="app">
      <Header tab={tab} onTab={setTab} meta={meta} />
      {tab === "dialog" ? (
        <DialogView
          entries={entries}
          selected={selected}
          onSelect={setSelected}
          busy={busy}
          onSend={send}
          onConfirm={confirm}
          onReset={reset}
          resolutions={resolutions}
          exportDate={meta?.export_date ?? null}
          needCode={needCode !== null}
          onCode={submitCode}
        />
      ) : (
        <QualityView meta={meta} sessionTurns={turns} />
      )}
    </div>
  );
}
