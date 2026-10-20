"""Bounded, read-only Snowflake discovery for the returns sources.

Run ``python -m scout.returns.discovery`` where Snowflake credentials are set.
It prints Markdown with:

- how ``ANALYTICS.RETURN_REDO`` resolves (schema or table), and for each Redo
  relation: columns, row count, ``LAST_ALTERED``, distinct counts of ID
  columns (grain), value counts of low-cardinality status, outcome, and
  reason columns, date ranges, quantity sums, fill counts for comment
  columns, and key paths inside JSON columns;
- columns, row counts, and freshness of the Shopify order models, the refund
  export, and the post-purchase enrichment table;
- aggregate profiles of the refund export and the enrichment table;
- other relations whose names match returns, Redo, Shopify, or order patterns.

Every statement is checked as read-only before it runs, and every statement
that can return many rows carries a ``LIMIT``. Comment, note, name, email,
phone, and address columns are reduced to counts, and JSON columns to key
paths, so no customer text or identity is printed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from scout.returns.sources import (
    POST_PURCHASE_ENRICHMENT,
    REDO_RETURNS,
    REFUND_EXPORT,
    SHOPIFY_ORDER_LINES,
    SHOPIFY_ORDERS,
)

QueryResult = tuple[list[str], list[tuple]]
Runner = Callable[[str, "dict | None"], QueryResult]

SEARCH_PATTERNS = (
    "%REDO%",
    "%RETURN%",
    "%EXCHANGE%",
    "%REFUND%",
    "%SHOPIFY%",
    "%ORDER%",
    "%FULFILLMENT%",
    "%POST_PURCHASE%",
)
SHOW_LIMIT = 200
MAX_DESCRIBED = 40
MAX_REDO_RELATIONS = 15
MAX_COLUMNS = 300
MAX_PROFILED_PER_KIND = 15
MAX_VALUE_ROWS = 30
MAX_LISTED_DISTINCT = 200
NESTED_SAMPLE_ROWS = 1000
MAX_VIEW_SQL_CHARS = 4000
MAX_CELL_CHARS = 120
SYSTEM_DATABASES = frozenset({"SNOWFLAKE", "SNOWFLAKE_SAMPLE_DATA"})

_READ_ONLY_START = re.compile(r"^\s*(SELECT|SHOW|DESCRIBE|DESC|WITH)\b", re.IGNORECASE)
_WRITE_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|"
    r"COPY|PUT|REMOVE|CALL|EXECUTE|UNDROP)\b",
    re.IGNORECASE,
)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_PATTERN = re.compile(r"^%?[A-Z_]+%?$")

_SENSITIVE = ("COMMENT", "NOTE", "MESSAGE", "FEEDBACK", "DESCRIPTION", "TEXT",
              "EMAIL", "PHONE", "ADDRESS", "NAME", "TITLE", "URL", "TOKEN")
_CATEGORICAL = ("STATUS", "STATE", "OUTCOME", "TYPE", "RESOLUTION", "COMPENSATION",
                "METHOD", "KIND", "PROVIDER", "CHANNEL", "REASON", "SOURCE",
                "PLATFORM", "CATEGORY", "ACTION")
_QUANTITY = ("QUANTITY", "QTY", "UNITS", "AMOUNT", "PRICE", "TOTAL", "VALUE", "CREDIT")
_DATE_TYPES = ("DATE", "TIMESTAMP")
_NESTED_TYPES = ("VARIANT", "OBJECT", "ARRAY")
_NUMERIC_TYPES = ("NUMBER", "FLOAT", "DECIMAL", "INT")

ENRICHMENT_SCOPE = (
    "SALES_CHANNEL_GROUP = 'dtc' AND SALES_CHANNEL = 'online_store' "
    "AND IS_COHORT_MATURE = TRUE"
)


def assert_read_only(sql: str) -> None:
    """Raise ``ValueError`` unless *sql* is one read-only statement."""
    body = sql.strip().rstrip(";").strip()
    if ";" in body:
        raise ValueError("Multiple statements are not allowed in discovery.")
    if not _READ_ONLY_START.match(body):
        raise ValueError("Discovery only runs SELECT, WITH, SHOW, or DESCRIBE.")
    if _WRITE_KEYWORDS.search(body):
        raise ValueError("Discovery SQL contains a write keyword.")


def ident(name: str) -> str:
    """Quote a metadata identifier after checking it is a plain name."""
    if not _IDENTIFIER.match(name):
        raise ValueError(f"Not a plain identifier: {name!r}")
    return f'"{name}"'


def split_relation(relation: str) -> tuple[str, str, str]:
    """Split ``DB.SCHEMA.NAME`` and validate each part as a plain identifier."""
    parts = relation.split(".")
    if len(parts) != 3 or not all(_IDENTIFIER.match(p) for p in parts):
        raise ValueError(f"Not a plain fully qualified relation: {relation!r}")
    return parts[0].upper(), parts[1].upper(), parts[2].upper()


@dataclass(frozen=True)
class Relation:
    database: str
    schema: str
    name: str
    kind: str = ""
    rows: Any = None

    @property
    def fqn(self) -> str:
        return f"{self.database}.{self.schema}.{self.name}"

    @property
    def quoted(self) -> str:
        return ".".join(ident(p) for p in (self.database, self.schema, self.name))


@dataclass(frozen=True)
class Column:
    name: str
    data_type: str

    @property
    def upper(self) -> str:
        return self.name.upper()

    def is_type(self, prefixes: Sequence[str]) -> bool:
        return self.data_type.upper().startswith(tuple(prefixes))


@dataclass
class Section:
    title: str
    lines: list[str] = field(default_factory=list)


def classify(columns: Sequence[Column]) -> dict[str, list[Column]]:
    """Group columns by how discovery may profile them."""
    groups: dict[str, list[Column]] = {
        "ids": [], "categorical": [], "dates": [], "quantities": [],
        "sensitive": [], "nested": [],
    }
    for col in columns:
        name = col.upper
        if col.is_type(_NESTED_TYPES):
            groups["nested"].append(col)
        elif col.is_type(_DATE_TYPES):
            groups["dates"].append(col)
        elif name.endswith("ID") and not col.is_type(("BOOLEAN",)):
            groups["ids"].append(col)
        elif any(k in name for k in _SENSITIVE):
            groups["sensitive"].append(col)
        elif col.is_type(("BOOLEAN",)) or any(k in name for k in _CATEGORICAL):
            groups["categorical"].append(col)
        elif col.is_type(_NUMERIC_TYPES) and any(k in name for k in _QUANTITY):
            groups["quantities"].append(col)
    return {k: v[:MAX_PROFILED_PER_KIND] for k, v in groups.items()}


class Discovery:
    def __init__(self, run: Runner | None = None):
        if run is None:
            from scout.integrations.snowflake import execute_query

            run = execute_query
        self._run = run
        self.statements: list[str] = []

    def query(self, sql: str, params: dict | None = None) -> QueryResult:
        assert_read_only(sql)
        self.statements.append(sql)
        return self._run(sql, params)

    # ── Sections ──────────────────────────────────────────────────

    def session(self) -> Section:
        section = Section("Session")
        cols, rows = self.query(
            "SELECT CURRENT_TIMESTAMP() AS NOW_TS, CURRENT_DATE() AS SESSION_DATE"
        )
        section.lines += _md_table(cols, rows)
        cols, rows = self.query("SHOW PARAMETERS LIKE 'TIMEZONE' IN SESSION")
        section.lines += _md_table(*_pick(cols, rows, ("key", "value", "level")))
        return section

    def resolve_redo(self) -> tuple[Section, list[Relation]]:
        """Resolve the two-part Redo name as a schema or a table in its database."""
        named = REDO_RETURNS.relation or ""
        parts = named.split(".")
        if len(parts) != 2 or not all(_IDENTIFIER.match(p) for p in parts):
            raise ValueError(f"Expected DATABASE.NAME for Redo, got {named!r}")
        database, name = parts[0].upper(), parts[1].upper()
        section = Section(f"Resolve `{named}`")
        cols, rows = self.query(
            f"SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, ROW_COUNT, LAST_ALTERED "
            f"FROM {ident(database)}.INFORMATION_SCHEMA.TABLES "
            f"WHERE UPPER(TABLE_SCHEMA) = %(name)s OR UPPER(TABLE_NAME) = %(name)s "
            f"ORDER BY 1, 2 LIMIT {SHOW_LIMIT}",
            {"name": name},
        )
        section.lines += _md_table(cols, rows)
        relations = [
            Relation(database, str(r[0]), str(r[1]), kind=str(r[2] or ""), rows=r[3])
            for r in rows
        ]
        if not relations:
            section.lines.append(
                f"_No schema or table named {name} is visible in {database} to this role._"
            )
        cols, rows = self.query(f"SHOW SCHEMAS LIKE '%REDO%' IN ACCOUNT LIMIT {SHOW_LIMIT}")
        section.lines.append("*Schemas matching `%REDO%`*")
        section.lines += _md_table(*_pick(cols, rows, ("database_name", "name", "created_on")))
        return section, relations[:MAX_REDO_RELATIONS]

    def search(self) -> tuple[Section, list[Relation]]:
        section = Section("Other matching relations")
        found: dict[str, Relation] = {}
        for pattern in SEARCH_PATTERNS:
            if not _PATTERN.match(pattern):
                raise ValueError(f"Unsafe search pattern: {pattern!r}")
            cols, rows = self.query(
                f"SHOW OBJECTS LIKE '{pattern}' IN ACCOUNT LIMIT {SHOW_LIMIT}"
            )
            index = {c.lower(): i for i, c in enumerate(cols)}
            for row in rows:
                rel = Relation(
                    database=str(row[index["database_name"]]).upper(),
                    schema=str(row[index["schema_name"]]).upper(),
                    name=str(row[index["name"]]).upper(),
                    kind=str(row[index["kind"]]) if "kind" in index else "",
                    rows=row[index["rows"]] if "rows" in index else None,
                )
                if rel.database in SYSTEM_DATABASES:
                    continue
                found.setdefault(rel.fqn, rel)
            if len(rows) >= SHOW_LIMIT:
                section.lines.append(
                    f"_`{pattern}` hit the {SHOW_LIMIT}-row limit; results are partial._"
                )
        relations = sorted(found.values(), key=lambda r: r.fqn)
        section.lines += _md_table(
            ["relation", "kind", "rows"],
            [(r.fqn, r.kind, r.rows) for r in relations],
        )
        return section, relations

    def describe(self, rel: Relation) -> tuple[Section, list[Column]]:
        section = Section(f"`{rel.fqn}`")
        params = {"schema": rel.schema, "name": rel.name}
        cols, rows = self.query(
            f"SELECT TABLE_TYPE, ROW_COUNT, CREATED, LAST_ALTERED, COMMENT "
            f"FROM {ident(rel.database)}.INFORMATION_SCHEMA.TABLES "
            f"WHERE TABLE_SCHEMA = %(schema)s AND TABLE_NAME = %(name)s LIMIT 1",
            params,
        )
        if not rows:
            section.lines.append("_Not visible to this role, or does not exist._")
            return section, []
        section.lines += _md_table(cols, rows)
        table_type = str(rows[0][0] or "").upper()
        cols, rows = self.query(
            f"SELECT COLUMN_NAME, DATA_TYPE FROM {ident(rel.database)}.INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = %(schema)s AND TABLE_NAME = %(name)s "
            f"ORDER BY ORDINAL_POSITION LIMIT {MAX_COLUMNS}",
            params,
        )
        section.lines += _md_table(cols, rows)
        columns = [Column(str(r[0]), str(r[1])) for r in rows]
        if "VIEW" in table_type:
            cols, rows = self.query(
                f"SELECT VIEW_DEFINITION FROM {ident(rel.database)}.INFORMATION_SCHEMA.VIEWS "
                f"WHERE TABLE_SCHEMA = %(schema)s AND TABLE_NAME = %(name)s LIMIT 1",
                params,
            )
            if rows and rows[0][0]:
                sql_text = str(rows[0][0])[:MAX_VIEW_SQL_CHARS]
                section.lines += ["", "View definition (truncated):", "```sql", sql_text, "```"]
        return section, columns

    def profile_columns(self, rel: Relation, columns: Sequence[Column]) -> Section:
        """Aggregate-only profile of one relation, driven by its column metadata."""
        section = Section(f"Profile: `{rel.fqn}`")
        table = rel.quoted
        groups = classify([c for c in columns if _IDENTIFIER.match(c.name)])

        id_exprs = ", ".join(
            f"COUNT(DISTINCT {ident(c.name)}) AS {ident(c.name)}" for c in groups["ids"]
        )
        sql = f"SELECT COUNT(*) AS ROW_COUNT{', ' + id_exprs if id_exprs else ''} FROM {table}"
        section.lines.append("*Grain: row count and distinct ID values*")
        section.lines += self._table(sql)

        if groups["dates"]:
            exprs = ", ".join(
                f"MIN({ident(c.name)}) AS {ident('MIN_' + c.name)}, "
                f"MAX({ident(c.name)}) AS {ident('MAX_' + c.name)}, "
                f"COUNT({ident(c.name)}) AS {ident('N_' + c.name)}"
                for c in groups["dates"]
            )
            section.lines.append("*Date ranges*")
            section.lines += self._table(f"SELECT {exprs} FROM {table}", transpose=True)

        if groups["quantities"]:
            exprs = ", ".join(
                f"SUM({ident(c.name)}) AS {ident('SUM_' + c.name)}, "
                f"MIN({ident(c.name)}) AS {ident('MIN_' + c.name)}, "
                f"MAX({ident(c.name)}) AS {ident('MAX_' + c.name)}"
                for c in groups["quantities"]
            )
            section.lines.append("*Quantities*")
            section.lines += self._table(f"SELECT {exprs} FROM {table}", transpose=True)

        if groups["sensitive"]:
            exprs = ", ".join(
                f"COUNT_IF({ident(c.name)} IS NOT NULL AND TRIM(TO_VARCHAR({ident(c.name)})) <> '') "
                f"AS {ident('FILLED_' + c.name)}"
                for c in groups["sensitive"]
            )
            section.lines.append("*Text and identity columns: filled counts only*")
            section.lines += self._table(f"SELECT COUNT(*) AS ROW_COUNT, {exprs} FROM {table}", transpose=True)

        for col in groups["categorical"]:
            section.lines.append(f"*Values of `{col.name}`*")
            try:
                cols, rows = self.query(
                    f"SELECT V, N, GROUPS FROM (SELECT {ident(col.name)} AS V, COUNT(*) AS N, "
                    f"COUNT(*) OVER () AS GROUPS FROM {table} GROUP BY 1) "
                    f"ORDER BY N DESC LIMIT {MAX_VALUE_ROWS}"
                )
            except Exception as exc:  # noqa: BLE001 - report and continue
                section.lines.append(_failure(exc))
                continue
            distinct = int(rows[0][2]) if rows else 0
            if distinct > MAX_LISTED_DISTINCT:
                section.lines.append(
                    f"_{distinct} distinct values; too many to list as a structured field._"
                )
            else:
                section.lines += _md_table(["value", "rows"], [(r[0], r[1]) for r in rows])
                if distinct > MAX_VALUE_ROWS:
                    section.lines.append(f"_Top {MAX_VALUE_ROWS} of {distinct} values._")

        for col in groups["nested"][:5]:
            section.lines.append(f"*Key paths in `{col.name}` (first {NESTED_SAMPLE_ROWS} non-null rows)*")
            section.lines += self._table(
                f"SELECT REGEXP_REPLACE(f.PATH, '[[][0-9]+[]]', '[]') AS KEY_PATH, COUNT(*) AS N "
                f"FROM (SELECT {ident(col.name)} AS V FROM {table} WHERE {ident(col.name)} IS NOT NULL "
                f"LIMIT {NESTED_SAMPLE_ROWS}) s, LATERAL FLATTEN(input => s.V, recursive => TRUE) f "
                f"GROUP BY 1 ORDER BY 2 DESC LIMIT 100"
            )
        return section

    def profile_refund_export(self) -> Section:
        table = REFUND_EXPORT.relation or ""
        split_relation(table)
        section = Section(f"Profile: `{table}` (aggregates only)")
        window = "REFUND_DATE_ET >= DATEADD(day, -30, CURRENT_DATE()) AND REFUND_DATE_ET < CURRENT_DATE()"
        queries = (
            ("All rows", f"SELECT COUNT(*) AS ROW_COUNT, MIN(REFUND_DATE_ET) AS FIRST_REFUND_DATE, "
                         f"MAX(REFUND_DATE_ET) AS LAST_REFUND_DATE FROM {table}"),
            ("L30 grain", f"SELECT COUNT(*) AS ROW_COUNT, COUNT(DISTINCT REFUND_ID) AS REFUNDS, "
                          f"SUM(QTY_RETURNED) AS UNITS, SUM(GROSS_QUANTITY) AS GROSS_UNITS "
                          f"FROM {table} WHERE {window}"),
            ("L30 flags", f"SELECT RETURN_PLATFORM, IS_REDO_RETURN, IS_EXCHANGE, IS_CANCELLATION, "
                          f"RESTOCK_TYPE, COUNT(*) AS ROW_COUNT, SUM(QTY_RETURNED) AS UNITS "
                          f"FROM {table} WHERE {window} GROUP BY 1, 2, 3, 4, 5 "
                          f"ORDER BY ROW_COUNT DESC LIMIT 50"),
        )
        for label, sql in queries:
            section.lines.append(f"*{label}*")
            section.lines += self._table(sql)
        return section

    def profile_enrichment(self) -> Section:
        table = POST_PURCHASE_ENRICHMENT.relation or ""
        split_relation(table)
        section = Section(f"Profile: `{table}` (aggregates and a 5-row product sample)")
        recent = "DELIVERY_COHORT_DATE >= DATEADD(day, -180, CURRENT_DATE())"
        queries = (
            ("Range", f"SELECT COUNT(*) AS ROW_COUNT, COUNT(DISTINCT SKU) AS SKUS, "
                      f"MIN(DELIVERY_COHORT_DATE) AS FIRST_COHORT, MAX(DELIVERY_COHORT_DATE) AS LAST_COHORT "
                      f"FROM {table}"),
            ("Maturity boundary", f"SELECT IS_COHORT_MATURE, MIN(DELIVERY_COHORT_DATE) AS FIRST_COHORT, "
                                  f"MAX(DELIVERY_COHORT_DATE) AS LAST_COHORT, "
                                  f"DATEDIFF(day, MAX(DELIVERY_COHORT_DATE), CURRENT_DATE()) AS DAYS_SINCE_LAST, "
                                  f"COUNT(*) AS ROW_COUNT FROM {table} GROUP BY 1 ORDER BY 1 LIMIT 5"),
            ("Grain check", f"SELECT COUNT(*) AS ROW_COUNT, COUNT(DISTINCT DELIVERY_COHORT_DATE, "
                            f"SALES_CHANNEL_GROUP, SALES_CHANNEL, CUSTOMER_TRX_STATUS, SKU) AS DISTINCT_KEYS "
                            f"FROM {table}"),
            ("Channels, last 180 cohort days", f"SELECT SALES_CHANNEL_GROUP, SALES_CHANNEL, IS_COHORT_MATURE, "
                         f"SUM(DELIVERED_ELIGIBLE_UNITS) AS ELIGIBLE_UNITS, SUM(DELIVERED_ORDER_LINES) AS ORDER_LINES, "
                         f"SUM(RETURNED_UNITS) AS RETURNED, SUM(OPEN_RETURN_UNITS) AS OPEN_RETURNS, "
                         f"SUM(SETTLED_RETURN_UNITS) AS SETTLED, SUM(REFUNDED_UNITS) AS REFUNDED, "
                         f"SUM(EXCHANGED_UNITS) AS EXCHANGED FROM {table} WHERE {recent} "
                         f"GROUP BY 1, 2, 3 ORDER BY ELIGIBLE_UNITS DESC LIMIT 50"),
            ("Rate scale in scope", f"SELECT SUM(RETURNED_UNITS) / NULLIF(SUM(DELIVERED_ELIGIBLE_UNITS), 0) "
                                    f"AS POOLED_RETURN_FRACTION, AVG(RETURN_RATE_PCT) AS AVG_ROW_RATE, "
                                    f"MIN(RETURN_RATE_PCT) AS MIN_ROW_RATE, MAX(RETURN_RATE_PCT) AS MAX_ROW_RATE "
                                    f"FROM {table} WHERE {ENRICHMENT_SCOPE} AND {recent}"),
            ("Customer status", f"SELECT CUSTOMER_TRX_STATUS, COUNT(*) AS ROW_COUNT, "
                                f"SUM(DELIVERED_ELIGIBLE_UNITS) AS ELIGIBLE_UNITS FROM {table} "
                                f"WHERE {recent} GROUP BY 1 ORDER BY 3 DESC LIMIT 20"),
            ("Season and lifecycle", f"SELECT SEASON, LIFECYCLE, COUNT(DISTINCT SKU) AS SKUS, "
                                     f"SUM(DELIVERED_ELIGIBLE_UNITS) AS ELIGIBLE_UNITS FROM {table} "
                                     f"WHERE {recent} GROUP BY 1, 2 ORDER BY 4 DESC LIMIT 50"),
            ("SKU attribute conflicts", f"SELECT COUNT(*) AS SKUS, SUM(IFF(N_SEASON > 1, 1, 0)) AS MULTI_SEASON, "
                                        f"SUM(IFF(N_STYLE > 1, 1, 0)) AS MULTI_STYLE, "
                                        f"SUM(IFF(N_SUBCLASS > 1, 1, 0)) AS MULTI_SUBCLASS "
                                        f"FROM (SELECT SKU, COUNT(DISTINCT SEASON) AS N_SEASON, "
                                        f"COUNT(DISTINCT STYLE_NAME) AS N_STYLE, "
                                        f"COUNT(DISTINCT SUBCLASS) AS N_SUBCLASS FROM {table} GROUP BY SKU)"),
            ("Sample in scope", f"SELECT DELIVERY_COHORT_DATE, IS_COHORT_MATURE, SALES_CHANNEL_GROUP, "
                                f"SALES_CHANNEL, CUSTOMER_TRX_STATUS, STYLE_NAME, SKU, SUBCLASS, SEASON, "
                                f"LIFECYCLE, DELIVERED_ELIGIBLE_UNITS, DELIVERED_ORDER_LINES, RETURNED_UNITS, "
                                f"EXCHANGED_UNITS, REFUNDED_UNITS, RETURN_RATE_PCT FROM {table} "
                                f"WHERE {ENRICHMENT_SCOPE} ORDER BY DELIVERY_COHORT_DATE DESC LIMIT 5"),
        )
        for label, sql in queries:
            section.lines.append(f"*{label}*")
            section.lines += self._table(sql)
        return section

    # ── Orchestration ────────────────────────────────────────────

    def run(self) -> list[Section]:
        sections: list[Section] = [self._guard("Session", self.session)]

        redo: list[Relation] = []
        try:
            resolve_section, redo = self.resolve_redo()
            sections.append(resolve_section)
        except Exception as exc:  # noqa: BLE001 - report and continue
            sections.append(Section("Resolve Redo", [_failure(exc)]))

        for rel in redo:
            try:
                describe_section, columns = self.describe(rel)
                sections.append(describe_section)
                if columns:
                    sections.append(self.profile_columns(rel, columns))
            except Exception as exc:  # noqa: BLE001 - report and continue
                sections.append(Section(f"`{rel.fqn}`", [_failure(exc)]))

        fixed = [SHOPIFY_ORDERS, SHOPIFY_ORDER_LINES, REFUND_EXPORT, POST_PURCHASE_ENRICHMENT]
        seen = {r.fqn for r in redo}
        for source in fixed:
            try:
                rel = Relation(*split_relation(source.relation or ""))
            except ValueError as exc:
                sections.append(Section(source.label, [_failure(exc)]))
                continue
            seen.add(rel.fqn)
            sections.append(self._guard(f"`{rel.fqn}`", lambda r=rel: self.describe(r)[0]))

        sections.append(self._guard("Refund export profile", self.profile_refund_export))
        sections.append(self._guard("Enrichment profile", self.profile_enrichment))

        try:
            search_section, matches = self.search()
            sections.append(search_section)
        except Exception as exc:  # noqa: BLE001 - report and continue
            sections.append(Section("Other matching relations", [_failure(exc)]))
            matches = []
        for rel in describe_candidates(matches):
            if rel.fqn in seen:
                continue
            seen.add(rel.fqn)
            sections.append(self._guard(f"`{rel.fqn}`", lambda r=rel: self.describe(r)[0]))
        return sections

    def _table(self, sql: str, transpose: bool = False) -> list[str]:
        try:
            cols, rows = self.query(sql)
        except Exception as exc:  # noqa: BLE001 - report and continue
            return [_failure(exc), ""]
        if transpose and len(rows) == 1:
            return _md_table(["field", "value"], list(zip(cols, rows[0])))
        return _md_table(cols, rows)

    @staticmethod
    def _guard(title: str, fn: Callable[[], Section]) -> Section:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - report and continue
            return Section(title, [_failure(exc)])


def describe_candidates(relations: Sequence[Relation]) -> list[Relation]:
    """Pick search hits worth describing, Redo first, capped at ``MAX_DESCRIBED``."""

    def rank(rel: Relation) -> int | None:
        if "REDO" in rel.fqn:
            return 0
        if "SHOPIFY" in rel.fqn and any(k in rel.name for k in ("ORDER", "LINE", "FULFILL", "REFUND")):
            return 1
        if "POST_PURCHASE" in rel.name:
            return 2
        if any(k in rel.name for k in ("RETURN", "EXCHANGE")):
            return 3
        return None

    ranked = [(rank(r), r) for r in relations]
    picked = sorted(((k, r) for k, r in ranked if k is not None), key=lambda kr: (kr[0], kr[1].fqn))
    return [r for _, r in picked[:MAX_DESCRIBED]]


def render(sections: Sequence[Section]) -> str:
    out = ["# Returns source discovery", ""]
    for section in sections:
        out += [f"## {section.title}", ""] + section.lines + [""]
    return "\n".join(out)


def _pick(cols: list[str], rows: list[tuple], wanted: Sequence[str]) -> QueryResult:
    index = {c.lower(): i for i, c in enumerate(cols)}
    keep = [w for w in wanted if w in index]
    return keep, [tuple(row[index[k]] for k in keep) for row in rows]


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= MAX_CELL_CHARS else text[: MAX_CELL_CHARS - 1] + "…"


def _md_table(cols: Sequence[str], rows: Sequence[tuple]) -> list[str]:
    if not rows:
        return ["_No rows._", ""]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return lines + [""]


def _failure(exc: Exception) -> str:
    return f"_Section failed: {type(exc).__name__}: {_cell(str(exc))}_"


def main() -> None:
    print(render(Discovery().run()))


if __name__ == "__main__":
    main()
