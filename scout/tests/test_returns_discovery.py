"""Returns discovery: read-only guard, Redo resolution, and privacy of profiles.

Snowflake is replaced by a fake runner. Nothing here opens a connection.
"""

import re

import pytest

from scout.returns import discovery
from scout.returns.discovery import (
    Column,
    Discovery,
    Relation,
    assert_read_only,
    classify,
    describe_candidates,
    ident,
    render,
    split_relation,
)

REDO_COLUMNS = [
    ("ID", "TEXT"),
    ("RETURN_ID", "TEXT"),
    ("ORDER_ID", "NUMBER"),
    ("LINE_ITEM_ID", "NUMBER"),
    ("STATUS", "TEXT"),
    ("REASON", "TEXT"),
    ("CUSTOMER_COMMENT", "TEXT"),
    ("CUSTOMER_EMAIL", "TEXT"),
    ("QUANTITY", "NUMBER"),
    ("CREATED_AT", "TIMESTAMP_NTZ"),
    ("ITEMS", "VARIANT"),
    ("IS_PAID", "BOOLEAN"),
]


class FakeWarehouse:
    def __init__(self, fail_on: str | None = None, redo_rows=None):
        self.fail_on = fail_on
        self.redo_rows = redo_rows if redo_rows is not None else [
            ("RETURN_REDO", "RETURNS", "BASE TABLE", 1000, "2026-10-20 06:00"),
            ("RETURN_REDO", "RETURN_ITEMS", "BASE TABLE", 2500, "2026-10-20 06:00"),
        ]
        self.calls: list[tuple[str, dict | None]] = []

    def __call__(self, sql, params=None):
        self.calls.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("SQL compilation error: invalid identifier")
        if "CURRENT_TIMESTAMP()" in sql:
            return ["NOW_TS", "SESSION_DATE"], [("2026-10-20 12:00", "2026-10-20")]
        if sql.startswith("SHOW PARAMETERS"):
            return ["key", "value", "default", "level"], [("TIMEZONE", "America/Los_Angeles", "America/Los_Angeles", "")]
        if "UPPER(TABLE_SCHEMA)" in sql:
            return ["TABLE_SCHEMA", "TABLE_NAME", "TABLE_TYPE", "ROW_COUNT", "LAST_ALTERED"], self.redo_rows
        if sql.startswith("SHOW SCHEMAS"):
            return ["created_on", "name", "database_name"], [("2026-01-01", "RETURN_REDO", "ANALYTICS")]
        if sql.startswith("SHOW OBJECTS"):
            return ["created_on", "name", "database_name", "schema_name", "kind", "rows"], [
                ("x", "FCT_SHOPIFY__ORDER_LINES", "JA_ANALYTICS", "DBT", "TABLE", 9),
                ("x", "QUERY_HISTORY", "SNOWFLAKE", "ACCOUNT_USAGE", "VIEW", 1),
                ("x", "RETURN_REASONS", "ANALYTICS", "MISC", "TABLE", 3),
            ]
        if "INFORMATION_SCHEMA.TABLES" in sql:
            return ["TABLE_TYPE", "ROW_COUNT", "CREATED", "LAST_ALTERED", "COMMENT"], [
                ("BASE TABLE", 10, "2026-01-01", "2026-10-20", None)
            ]
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            if params and params.get("schema") == "RETURN_REDO":
                return ["COLUMN_NAME", "DATA_TYPE"], REDO_COLUMNS
            return ["COLUMN_NAME", "DATA_TYPE"], [("SKU", "TEXT")]
        if "COUNT(*) OVER ()" in sql:
            if '"STATUS"' in sql:
                return ["V", "N", "GROUPS"], [("complete", 800, 3), ("open", 150, 3), ("rejected", 50, 3)]
            if '"REASON"' in sql:
                return ["V", "N", "GROUPS"], [("free text a", 1, 500)]
            return ["V", "N", "GROUPS"], [(True, 10, 2), (False, 5, 2)]
        return ["VALUE"], [(1,)]


def _run(fake):
    disc = Discovery(run=fake)
    sections = disc.run()
    return disc, render(sections)


def test_every_statement_is_read_only_and_bounded():
    fake = FakeWarehouse()
    disc, _ = _run(fake)
    assert disc.statements
    for sql in disc.statements:
        assert_read_only(sql)
        if sql.startswith("SHOW PARAMETERS LIKE 'TIMEZONE'"):
            continue
        if sql.startswith("SHOW") or "GROUP BY 1)" in sql or "FLATTEN" in sql:
            assert "LIMIT" in sql, sql


def test_redo_name_resolves_as_schema_and_each_table_is_profiled():
    fake = FakeWarehouse()
    _, report = _run(fake)
    resolve_sql, params = next(c for c in fake.calls if "UPPER(TABLE_SCHEMA)" in c[0])
    assert '"ANALYTICS".INFORMATION_SCHEMA.TABLES' in resolve_sql
    assert params == {"name": "RETURN_REDO"}
    assert "## Profile: `ANALYTICS.RETURN_REDO.RETURNS`" in report
    assert "## Profile: `ANALYTICS.RETURN_REDO.RETURN_ITEMS`" in report
    assert "| complete | 800 |" in report
    assert 'COUNT(DISTINCT "RETURN_ID")' in "\n".join(c[0] for c in fake.calls)


def test_unresolved_redo_name_is_reported():
    _, report = _run(FakeWarehouse(redo_rows=[]))
    assert "No schema or table named RETURN_REDO is visible in ANALYTICS" in report


def test_comment_and_identity_columns_are_counted_not_printed():
    disc, _ = _run(FakeWarehouse())
    for sql in disc.statements:
        for col in ("CUSTOMER_COMMENT", "CUSTOMER_EMAIL"):
            for match in re.finditer(rf'"{col}"', sql):
                prefix = sql[: match.start()]
                assert prefix.rstrip().endswith(("COUNT_IF(", "TO_VARCHAR(")), sql
    assert any('AS "FILLED_CUSTOMER_COMMENT"' in sql for sql in disc.statements)


def test_high_cardinality_reason_values_are_suppressed():
    _, report = _run(FakeWarehouse())
    assert "500 distinct values; too many to list" in report
    assert "free text a" not in report


def test_nested_columns_report_key_paths_only():
    disc, _ = _run(FakeWarehouse())
    nested = [s for s in disc.statements if "FLATTEN" in s]
    assert nested
    for sql in nested:
        assert sql.startswith("SELECT REGEXP_REPLACE(f.PATH")
        assert "f.VALUE" not in sql


def test_a_failing_section_does_not_stop_the_rest():
    _, report = _run(FakeWarehouse(fail_on="SHOPIFY_AGG_POST_PURCHASE"))
    assert "Section failed: RuntimeError" in report
    assert "## Profile: `ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS` (aggregates only)" in report
    assert "## `JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDER_LINES`" in report


def test_system_databases_are_skipped():
    disc, report = _run(FakeWarehouse())
    assert "SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY" not in report
    assert "ANALYTICS.MISC.RETURN_REASONS" in report


@pytest.mark.parametrize("sql", [
    "INSERT INTO t VALUES (1)",
    "SELECT 1; DROP TABLE t",
    "CREATE VIEW v AS SELECT 1",
    "WITH a AS (SELECT 1) DELETE FROM t",
    "GRANT SELECT ON t TO ROLE r",
    "USE ROLE ACCOUNTADMIN",
])
def test_read_only_guard_rejects_writes(sql):
    with pytest.raises(ValueError):
        assert_read_only(sql)


@pytest.mark.parametrize("sql", [
    "SHOW OBJECTS LIKE '%REDO%' IN ACCOUNT LIMIT 200",
    "WITH a AS (SELECT 1 AS X) SELECT X FROM a",
    "SELECT CREATED, LAST_ALTERED, UPDATED_AT FROM t",
    "DESCRIBE TABLE t",
])
def test_read_only_guard_allows_reads(sql):
    assert_read_only(sql)


def test_identifiers_are_validated():
    assert ident("RETURN_ITEMS") == '"RETURN_ITEMS"'
    with pytest.raises(ValueError):
        ident('X"; DROP TABLE T; --')
    assert split_relation("analytics.dbt.t") == ("ANALYTICS", "DBT", "T")
    with pytest.raises(ValueError):
        split_relation("ANALYTICS.RETURN_REDO")


def test_classify_groups_columns_conservatively():
    groups = classify([Column(n, t) for n, t in REDO_COLUMNS])
    names = {k: [c.name for c in v] for k, v in groups.items()}
    assert names["ids"] == ["ID", "RETURN_ID", "ORDER_ID", "LINE_ITEM_ID"]
    assert names["categorical"] == ["STATUS", "REASON", "IS_PAID"]
    assert names["sensitive"] == ["CUSTOMER_COMMENT", "CUSTOMER_EMAIL"]
    assert names["quantities"] == ["QUANTITY"]
    assert names["dates"] == ["CREATED_AT"]
    assert names["nested"] == ["ITEMS"]


def test_describe_candidates_puts_redo_first_and_caps(monkeypatch):
    monkeypatch.setattr(discovery, "MAX_DESCRIBED", 2)
    rels = [
        Relation("ANALYTICS", "MISC", "RETURN_REASONS"),
        Relation("JA_ANALYTICS", "DBT", "FCT_SHOPIFY__ORDER_LINES"),
        Relation("ANALYTICS", "RETURN_REDO", "ITEMS"),
        Relation("ANALYTICS", "MISC", "UNRELATED"),
    ]
    picked = [r.fqn for r in describe_candidates(rels)]
    assert picked == ["ANALYTICS.RETURN_REDO.ITEMS", "JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDER_LINES"]
