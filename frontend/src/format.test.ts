import { describe, expect, it } from "vitest";
import { cell, duration, modelName, num, percent, plural, runTime, tokens, usd } from "./format";

const s = (x: string) => x.replace(/ /g, " ");

describe("format", () => {
  it("числа и токены", () => {
    expect(s(num(35279117))).toBe("35 279 117");
    expect(s(tokens(980))).toBe("980");
    expect(s(tokens(4595))).toBe("4,6 тыс.");
    expect(s(tokens(198833))).toBe("199 тыс.");
    expect(s(tokens(1_250_000))).toBe("1,3 млн");
  });

  it("деньги", () => {
    expect(usd(null)).toBe("—");
    expect(usd(0.6123)).toBe("$0,61");
    expect(usd(0.00412)).toBe("$0,0041");
  });

  it("время", () => {
    expect(s(duration(850))).toBe("850 мс");
    expect(s(duration(4717))).toBe("4,7 с");
    expect(s(duration(17250))).toBe("17 с");
    expect(runTime("20261118-170347")).toBe("18.11, 17:03");
    expect(runTime("2026-11-18T17:32:01")).toBe("18.11, 17:32");
  });

  it("модели, проценты, склонения, ячейки", () => {
    expect(modelName("claude-sonnet-5-5")).toBe("Sonnet 5.5");
    expect(modelName("Qwen/Qwen3-32B")).toBe("Qwen/Qwen3-32B");
    expect(percent(34, 35)).toBe(97.1);
    expect(plural(1, "вопрос", "вопроса", "вопросов")).toBe("вопрос");
    expect(plural(3, "вопрос", "вопроса", "вопросов")).toBe("вопроса");
    expect(plural(35, "вопрос", "вопроса", "вопросов")).toBe("вопросов");
    expect(plural(11, "вопрос", "вопроса", "вопросов")).toBe("вопросов");
    expect(cell(2026)).toBe("2026");
    expect(s(cell(35279117))).toBe("35 279 117");
    expect(cell(true)).toBe("да");
  });
});

import { stepTitle } from "./components/RouteSheet";

describe("trace", () => {
  it("переводит коды маршрутов и режимов в заголовках шагов", () => {
    const base = { node: "router", duration_ms: 1, usage: null, detail: {} };
    expect(stepTitle({ ...base, title: "Маршрут: data_and_docs" })).toBe("Маршрут: данные и документы");
    expect(stepTitle({ ...base, node: "execute", title: "Битрикс24 (stub): ок" })).toBe("Битрикс24 (заглушка): ок");
  });
});
