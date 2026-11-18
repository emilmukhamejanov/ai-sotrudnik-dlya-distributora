// Форматирование и подписи — чистые функции (покрыты тестами).

const nbsp = " ";

/** 1234567 → «1 234 567»; дробные — с запятой. */
export function num(value: number, digits = 0): string {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits, minimumFractionDigits: 0 })
    .format(value)
    .replace(/ /g, nbsp);
}

/** Токены компактно: 980, 12,4 тыс., 1,2 млн. */
export function tokens(value: number): string {
  if (value < 1000) return num(value);
  if (value < 1_000_000) return `${num(value / 1000, value < 10_000 ? 1 : 0)}${nbsp}тыс.`;
  return `${num(value / 1_000_000, 1)}${nbsp}млн`;
}

/** Доллары: центы видны, доли цента — до 4 знаков; null — «нет цены». */
export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(4).replace(".", ",")}`;
  return `$${value.toFixed(2).replace(".", ",")}`;
}

/** Миллисекунды → «850 мс» / «4,7 с». */
export function duration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}${nbsp}мс`;
  return `${num(ms / 1000, ms < 10_000 ? 1 : 0)}${nbsp}с`;
}

/** «20261118-170347» или ISO → «18.11, 17:03». */
export function runTime(value: string): string {
  const m = value.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/) ?? value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!m) return value;
  return `${m[3]}.${m[2]}, ${m[4]}:${m[5]}`;
}

export function percent(part: number, whole: number): number {
  return whole ? Math.round((part / whole) * 1000) / 10 : 0;
}

export const NODE_LABELS: Record<string, string> = {
  guard: "Защита",
  router: "Роутер",
  docs: "Поиск по документам",
  sql: "Запрос к данным",
  compose: "Формулировка ответа",
  plan_action: "Подготовка действия",
  confirm: "Подтверждение",
  execute: "Битрикс24",
  handoff: "Передача человеку",
  finalize: "Проверка ответа",
};

export const ROUTE_LABELS: Record<string, string> = {
  data: "данные",
  docs: "документы",
  data_and_docs: "данные и документы",
  action: "действие в Битрикс24",
  human: "передача человеку",
  smalltalk: "разговор",
  blocked: "отклонено защитой",
};

export const CATEGORY_LABELS: Record<string, string> = {
  data: "Цифры из 1С",
  docs: "Регламенты и договоры",
  data_and_docs: "Цифры + правила",
  action: "Действия в Битрикс24",
  human: "Передача человеку",
  security: "Безопасность",
};

/** Короткое имя модели: claude-sonnet-5-5 → Sonnet 5.5. */
export function modelName(model: string | null | undefined): string {
  if (!model) return "—";
  const m = model.match(/^claude-(\w+)-(\d+)-(\d+)/);
  if (m) return `${m[1][0].toUpperCase()}${m[1].slice(1)} ${m[2]}.${m[3]}`;
  return model;
}

/** Значение ячейки SQL-таблицы: числа с разделителями, даты как есть. */
export function cell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return Math.abs(value) >= 10_000 || !Number.isInteger(value) ? num(value, 2) : String(value);
  if (typeof value === "boolean") return value ? "да" : "нет";
  return String(value);
}

/** Склонение: 1 вопрос, 2 вопроса, 5 вопросов. */
export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}
