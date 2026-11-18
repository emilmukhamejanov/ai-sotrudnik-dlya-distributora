// Типы и вызовы API агента (FastAPI). Поля — как в backend/src/dala_agent/api/main.py.

export type Status = "done" | "needs_confirmation" | "needs_clarification";
export type Route = "data" | "docs" | "data_and_docs" | "action" | "human" | "smalltalk" | "blocked";

export interface Usage {
  model: string;
  input_tokens: number;
  output_tokens: number;
}

export interface TraceStep {
  node: string;
  title: string;
  duration_ms: number;
  usage: Usage | null;
  detail: Record<string, unknown>;
}

export interface Citation {
  slug: string;
  section: string | null;
  citation?: string;
}

export interface SqlResult {
  ok: boolean;
  sql: string | null;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  assumptions: string[];
}

export interface PendingAction {
  kind: "create_task" | "create_deal";
  title: string;
  description: string;
  responsible_id: number | null;
  responsible_name: string | null;
  deadline: string | null;
  amount_kzt: number | null;
  client_name: string | null;
  action_id: string;
}

export interface ModelUsage {
  input_tokens: number;
  output_tokens: number;
  calls: number;
  cost_usd: number | null;
}

export interface TurnUsage {
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  by_model: Record<string, ModelUsage>;
}

export interface Turn {
  thread_id: string;
  status: Status;
  answer: string;
  language: "ru" | "kk";
  route: Route | null;
  citations: Citation[];
  pending_action: PendingAction | null;
  sql: SqlResult | null;
  handoff: { ticket_id: string; reason: string } | null;
  action_result: { ok: boolean; external_id: string | null; mode: string; error: string | null } | null;
  trace: TraceStep[];
  usage: TurnUsage;
  duration_ms: number;
}

export interface Meta {
  demo: boolean;
  model: string | null;
  router_model: string | null;
  embeddings: string | null;
  bitrix_mode: string | null;
  export_date: string | null;
  pricing: { override: [number, number] | null; models: Record<string, { input: number; output: number }> };
}

export interface EvalSummary {
  cases: number;
  passed: number;
  pass_rate: number;
  by_category: Record<string, { cases: number; passed: number }>;
  latency_ms: { median: number; p95: number; max: number };
  tokens: { input: number; output: number };
  cost_usd: number | null;
}

export interface EvalTurn {
  input: string;
  passed: boolean;
  failures: string[];
  route: string | null;
  status: string;
  answer: string;
  sql: string | null;
  citations: string[];
  duration_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd?: number | null;
  notes?: string[];
}

export interface EvalCase {
  id: string;
  category: string;
  passed: boolean;
  turns: EvalTurn[];
  error: string | null;
  duration_ms: number;
}

export interface EvalReport {
  started_at: string;
  duration_s: number;
  model: string;
  router_model: string;
  summary: EvalSummary;
  cases: EvalCase[];
}

export interface EvalReportListItem {
  name: string;
  started_at: string;
  model: string;
  router_model: string;
  duration_s: number;
  summary: EvalSummary;
}

export interface EvalRunState {
  run_id: string;
  status: "running" | "done" | "error";
  done: number;
  total: number;
  results: { id: string; passed: boolean; failures: string[] }[];
  error?: string;
  report?: EvalReport;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(path, {
      ...init,
      headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, "Сервер агента не отвечает. Проверьте, что окно start-windows.bat открыто.");
  }
  if (!resp.ok) {
    let detail = `Ошибка ${resp.status}`;
    try {
      const body = await resp.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* тело не JSON */
    }
    throw new ApiError(resp.status, detail);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  meta: () => request<Meta>("/meta"),
  chat: (message: string, thread_id: string | null) =>
    request<Turn>("/chat", { method: "POST", body: JSON.stringify({ message, thread_id }) }),
  confirm: (thread_id: string, approved: boolean, action_id: string | null) =>
    request<Turn>(`/chat/${encodeURIComponent(thread_id)}/confirm`, {
      method: "POST",
      body: JSON.stringify({ approved, action_id }),
    }),
  evalReports: () => request<EvalReportListItem[]>("/evals/reports"),
  evalReport: (name: string) => request<EvalReport>(`/evals/reports/${encodeURIComponent(name)}`),
  startEvals: () => request<{ run_id: string; total: number }>("/evals/run", { method: "POST", body: "{}" }),
  evalRun: (id: string) => request<EvalRunState>(`/evals/runs/${encodeURIComponent(id)}`),
};
