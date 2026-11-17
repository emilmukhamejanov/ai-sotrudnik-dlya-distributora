"""Описание схемы analytics для модели: представления, колонки и комментарии из COMMENT ON + справочники."""

from __future__ import annotations

from dataclasses import dataclass, field

from .sql_guard import ALLOWED_SCHEMA, ALLOWED_VIEWS

_COLUMNS_SQL = """
SELECT c.relname, obj_description(c.oid, 'pg_class'), a.attname,
       format_type(a.atttypid, a.atttypmod), col_description(c.oid, a.attnum)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = %s AND c.relkind IN ('v', 'm', 'r')
ORDER BY c.relname, a.attnum
"""

_DICTIONARIES = {
    "Категории товаров (products.category, sales.category)": (
        "SELECT DISTINCT category FROM analytics.products ORDER BY 1"
    ),
    "Сегменты клиентов (clients.segment)": "SELECT DISTINCT segment FROM analytics.clients ORDER BY 1",
    "Города клиентов (clients.city, sales.client_city)": "SELECT DISTINCT city FROM analytics.clients ORDER BY 1",
    "Склады (warehouses.name)": "SELECT name || ' (' || city || ')' FROM analytics.warehouses ORDER BY id",
    "Типы документов (sales.doc_type)": "SELECT DISTINCT doc_type FROM analytics.sales_documents ORDER BY 1",
    "Бренды (products.brand)": "SELECT DISTINCT brand FROM analytics.products ORDER BY 1",
}


@dataclass
class SchemaContext:
    views: dict[str, dict] = field(default_factory=dict)  # name → {"comment", "columns": [(name, type, comment)]}
    dictionaries: dict[str, list[str]] = field(default_factory=dict)
    export_date: str = ""
    period: str = ""

    def render(self) -> str:
        lines = [f"Схема {ALLOWED_SCHEMA} (PostgreSQL 16). Доступны только эти представления:"]
        for name, view in self.views.items():
            lines.append(f"\n{ALLOWED_SCHEMA}.{name} — {view['comment'] or ''}")
            for col, typ, comment in view["columns"]:
                lines.append(f"  - {col} {typ}" + (f" — {comment}" if comment else ""))
        if self.dictionaries:
            lines.append("\nЗначения справочников (пиши их в SQL ровно так):")
            for title, values in self.dictionaries.items():
                lines.append(f"  - {title}: " + ", ".join(values))
        return "\n".join(lines)


def load_schema_context(executor) -> SchemaContext:
    ctx = SchemaContext()
    for relname, rel_comment, col, typ, col_comment in executor.fetch(_COLUMNS_SQL, (ALLOWED_SCHEMA,)):
        if relname not in ALLOWED_VIEWS:
            continue
        view = ctx.views.setdefault(relname, {"comment": rel_comment, "columns": []})
        view["columns"].append((col, typ, col_comment))
    for title, sql in _DICTIONARIES.items():
        ctx.dictionaries[title] = [str(r[0]) for r in executor.fetch(sql)]
    row = executor.fetch("SELECT export_date, period_start, period_end FROM analytics.export_info LIMIT 1")
    if row:
        export_date, start, end = row[0]
        ctx.export_date = str(export_date)
        ctx.period = f"{start} — {end}"
    return ctx
