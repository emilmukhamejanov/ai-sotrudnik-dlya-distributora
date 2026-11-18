import type { Meta } from "../api";
import type { Tab } from "../App";
import { modelName } from "../format";

interface Props {
  tab: Tab;
  onTab: (tab: Tab) => void;
  meta: Meta | null;
}

const TABS: { id: Tab; label: string }[] = [
  { id: "dialog", label: "Диалог" },
  { id: "quality", label: "Качество и расходы" },
];

export default function Header({ tab, onTab, meta }: Props) {
  return (
    <header className="topbar">
      <div className="brand">
        <div className="brand-seal" aria-hidden="true">
          Д
        </div>
        <div>
          <div className="brand-name">ИИ-сотрудник</div>
          <div className="brand-sub">ТОО «Дала Дистрибьюшн», демо-данные</div>
        </div>
      </div>

      <nav className="tabs" role="tablist" aria-label="Разделы">
        {TABS.map((t) => (
          <button
            key={t.id}
            className="tab"
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => onTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {meta && (
        <div className="topbar-meta">
          <span>
            Модель <b>{modelName(meta.model)}</b>, роутер <b>{modelName(meta.router_model)}</b>
          </span>
          <span>
            Битрикс24: <b>{meta.bitrix_mode === "webhook" ? "портал" : "заглушка"}</b>
          </span>
          {meta.demo_mode.enabled && meta.demo_mode.remaining_today_usd !== undefined && (
            <span>
              Демо: на сегодня осталось <b>${meta.demo_mode.remaining_today_usd.toFixed(2).replace(".", ",")}</b>
            </span>
          )}
          {meta.export_date && (
            <span>
              Выгрузка 1С на <b>{meta.export_date.split("-").reverse().join(".")}</b>
            </span>
          )}
        </div>
      )}
    </header>
  );
}
