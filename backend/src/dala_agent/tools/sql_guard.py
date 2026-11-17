"""Проверка SQL от модели перед выполнением — первый рубеж защиты.

Правила:
  * ровно один оператор, и это SELECT (в т.ч. WITH … SELECT, UNION);
  * никаких DML/DDL нигде в дереве (включая CTE), SELECT INTO, FOR UPDATE, SET, COPY;
  * источники — только представления из белого списка схемы analytics (или CTE из этого же запроса);
  * запрещены служебные функции (pg_*, lo_*, dblink, set_config, *_to_xml и т.п.);
  * имена представлений квалифицируются схемой analytics, LIMIT ограничивается сверху.

Выполняется не исходный текст, а SQL, заново собранный из проверенного дерева, — так нет
расхождения между тем, что проверили, и тем, что выполнит Postgres.

Второй рубеж — сама база: роль agent_reader с правами только на чтение analytics и statement_timeout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

ALLOWED_SCHEMA = "analytics"
ALLOWED_VIEWS = frozenset(
    {
        "export_info",
        "warehouses",
        "products",
        "managers",
        "clients",
        "sales",
        "sales_documents",
        "stock",
        "receivables",
        "plan_fact",
    }
)

_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name)
    for name in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "TruncateTable", "Command",
        "Copy", "Into", "Lock", "Set", "Transaction", "Commit", "Rollback", "Grant", "Revoke", "Use",
        "Pragma", "LoadData", "Describe",
    )
    if hasattr(exp, name)
)  # fmt: skip

_FORBIDDEN_TYPED_FUNCS: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name) for name in ("CurrentUser", "CurrentSchema", "CurrentCatalog") if hasattr(exp, name)
)

_FORBIDDEN_FUNC_RE = re.compile(
    r"^(pg_|lo_|dblink|set_config|current_setting|query_to_xml|.*_to_xml|xmlparse|txid_|nextval|setval|"
    r"currval|has_\w*_privilege|inet_|version$|current_user$|session_user$|current_database$|"
    r"current_schemas?$|user$|current_query$|format_type$|to_regclass$|to_regproc|row_security_active$)",
    re.I,
)

_TABLE_FUNCTIONS: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name) for name in ("GenerateSeries", "ExplodingGenerateSeries", "Unnest") if hasattr(exp, name)
)


class SQLRejected(ValueError):
    """Запрос не прошёл проверку; текст ошибки возвращается модели для исправления."""


@dataclass(frozen=True)
class CheckedSQL:
    sql: str  # SQL, собранный из проверенного дерева: квалифицированные имена, LIMIT
    tables: tuple[str, ...]
    limit_applied: int


def check_sql(sql: str, *, max_rows: int = 200) -> CheckedSQL:
    text = (sql or "").strip()
    if not text:
        raise SQLRejected("Пустой запрос")
    try:
        statements = [s for s in sqlglot.parse(text, read="postgres") if s is not None]
    except ParseError as e:
        raise SQLRejected(f"Не удалось разобрать SQL: {_short(e)}") from e
    if len(statements) != 1:
        raise SQLRejected("Разрешён ровно один оператор SELECT")
    tree = statements[0]

    if not isinstance(tree, exp.Select | exp.SetOperation):
        raise SQLRejected(f"Разрешён только SELECT, получен {tree.key.upper()}")

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise SQLRejected(f"Запрещённая конструкция: {node.key.upper()}")
        if isinstance(node, _FORBIDDEN_TYPED_FUNCS):
            raise SQLRejected(f"Запрещённая функция: {node.sql_name()}")
        if isinstance(node, exp.Anonymous) and _FORBIDDEN_FUNC_RE.match(node.name or ""):
            raise SQLRejected(f"Запрещённая функция: {node.name}")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    used: set[str] = set()
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            if isinstance(table.this, _TABLE_FUNCTIONS):
                continue
            raise SQLRejected(f"Табличная функция не разрешена: {table.this.sql(dialect='postgres')[:60]}")
        name = table.name.lower()
        schema = (table.db or "").lower()
        if table.catalog:
            raise SQLRejected(f"Обращение к другой базе запрещено: {table.sql(dialect='postgres')}")
        if not schema and name in cte_names:
            continue
        if schema and schema != ALLOWED_SCHEMA:
            raise SQLRejected(
                f"Схема {schema} недоступна. Используйте только представления {ALLOWED_SCHEMA}: "
                + ", ".join(sorted(ALLOWED_VIEWS))
            )
        if name not in ALLOWED_VIEWS:
            raise SQLRejected(
                f"Представление {name} не из белого списка. Доступны: " + ", ".join(sorted(ALLOWED_VIEWS))
            )
        table.set("db", exp.to_identifier(ALLOWED_SCHEMA))
        used.add(name)

    limit = _apply_limit(tree, max_rows)
    return CheckedSQL(sql=tree.sql(dialect="postgres", comments=False), tables=tuple(sorted(used)), limit_applied=limit)


def _apply_limit(tree: exp.Expression, max_rows: int) -> int:
    current = tree.args.get("limit")
    if current is not None:
        value = current.expression if isinstance(current, exp.Limit) else current
        if isinstance(value, exp.Literal) and value.is_int and int(value.this) <= max_rows:
            return int(value.this)
    tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
    return max_rows


def _short(e: Exception) -> str:
    return str(e).splitlines()[0][:200]
