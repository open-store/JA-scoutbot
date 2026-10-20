"""
Shopify order lookups from Snowflake for product attribution.

Relations come from the returns discovery decision: Rocky reads
``JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDERS`` and ``FCT_SHOPIFY__ORDER_LINES``.
Columns Rocky's SQL uses are marked verified below; the rest are the
expected dbt names and must be confirmed with ``python -m
scout.returns.discovery`` (or ``DESCRIBE TABLE``) before
``SCOUT_ORDER_LOOKUP=snowflake`` is turned on. Scout's role has not been
checked for SELECT on ``JA_ANALYTICS``.

Both queries are read-only, bind their inputs, and have a row cap.
Customer emails are never logged.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import datetime
from typing import Any, Dict, List, Sequence

from scout.integrations.snowflake import execute_query_dict
from scout.models.orders import OrderLine, OrderRecord

logger = logging.getLogger("scout.repository.orders")

ORDERS_RELATION = "JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDERS"
ORDER_LINES_RELATION = "JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDER_LINES"
ORDER_SOURCE = f"{ORDERS_RELATION} + {ORDER_LINES_RELATION}"

# Column name in Snowflake, and whether Rocky's SQL already uses it.
ORDER_COLUMNS: Dict[str, tuple] = {
    "order_id": ("ORDER_ID", "unverified"),
    "order_name": ("ORDER_NAME", "unverified"),
    "email": ("EMAIL", "unverified"),
    "created_at": ("CREATED_AT", "unverified"),
    "is_reportable_sales_order": ("IS_REPORTABLE_SALES_ORDER", "verified in Rocky"),
    "is_test_order": ("IS_TEST_ORDER", "verified in Rocky"),
}
LINE_COLUMNS: Dict[str, tuple] = {
    "order_id": ("ORDER_ID", "unverified"),
    "line_id": ("ORDER_LINE_ID", "unverified"),
    "product": ("VARIANT_TITLE_IMD", "verified in Rocky"),
    "quantity": ("QUANTITY", "unverified"),
    "is_gwp_line": ("IS_GWP_LINE", "verified in Rocky"),
}

MAX_ORDER_NUMBERS = 20
MAX_EMAILS = 200
MAX_ROWS = 2000


def _o(key: str) -> str:
    return f"o.{ORDER_COLUMNS[key][0]}"


def _l(key: str) -> str:
    return f"l.{LINE_COLUMNS[key][0]}"


def _select() -> str:
    return f"""
    SELECT
        {_o('order_id')}::STRING      AS ORDER_ID,
        {_o('order_name')}::STRING    AS ORDER_NAME,
        LOWER(TRIM({_o('email')}))    AS CUSTOMER_EMAIL,
        {_o('created_at')}            AS ORDER_CREATED_AT,
        {_l('line_id')}::STRING       AS LINE_ID,
        TRIM({_l('product')})         AS PRODUCT_TITLE,
        {_l('quantity')}              AS QUANTITY
    FROM {ORDERS_RELATION} o
    JOIN {ORDER_LINES_RELATION} l ON {_l('order_id')} = {_o('order_id')}
    WHERE NOT COALESCE({_o('is_test_order')}, FALSE)
      AND NOT COALESCE({_l('is_gwp_line')}, FALSE)"""


def build_orders_by_number_sql(count: int) -> str:
    placeholders = ", ".join(f"%(n{i})s" for i in range(count))
    return f"""{_select()}
      AND REGEXP_REPLACE({_o('order_name')}, '[^0-9]', '') IN ({placeholders})
    ORDER BY ORDER_CREATED_AT
    LIMIT {MAX_ROWS}
    """


def build_orders_for_emails_sql(count: int) -> str:
    placeholders = ", ".join(f"%(e{i})s" for i in range(count))
    return f"""{_select()}
      AND COALESCE({_o('is_reportable_sales_order')}, FALSE)
      AND LOWER(TRIM({_o('email')})) IN ({placeholders})
      AND {_o('created_at')} >= TO_TIMESTAMP_TZ(%(start)s)
      AND {_o('created_at')} < TO_TIMESTAMP_TZ(%(end)s)
    ORDER BY ORDER_CREATED_AT
    LIMIT {MAX_ROWS}
    """


def rows_to_orders(rows: List[Dict[str, Any]]) -> List[OrderRecord]:
    grouped: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for row in rows:
        oid = str(row.get("ORDER_ID") or "")
        if not oid:
            continue
        slot = grouped.setdefault(oid, {"row": row, "lines": []})
        title = (row.get("PRODUCT_TITLE") or "").strip()
        if title:
            qty = row.get("QUANTITY")
            slot["lines"].append(OrderLine(
                line_id=str(row.get("LINE_ID") or ""),
                product_title=title,
                quantity=int(qty) if qty is not None else None,
            ))
    out = []
    for oid, slot in grouped.items():
        row = slot["row"]
        created = row.get("ORDER_CREATED_AT")
        out.append(OrderRecord(
            order_id=oid,
            order_name=row.get("ORDER_NAME"),
            customer_email=row.get("CUSTOMER_EMAIL"),
            created_at=created if isinstance(created, datetime) else None,
            lines=tuple(slot["lines"]),
        ))
    return out


class SnowflakeOrderLookup:
    available = True
    source = ORDER_SOURCE
    reason = ""

    def orders_by_number(self, order_numbers: Sequence[str]) -> List[OrderRecord]:
        numbers = [n for n in dict.fromkeys(order_numbers) if n.isdigit()][:MAX_ORDER_NUMBERS]
        if not numbers:
            return []
        params = {f"n{i}": n for i, n in enumerate(numbers)}
        rows = execute_query_dict(build_orders_by_number_sql(len(numbers)), params)
        logger.info("Order lookup by number: %d numbers → %d rows", len(numbers), len(rows))
        return rows_to_orders(rows)

    def orders_for_emails(self, emails: Sequence[str], start: datetime, end: datetime) -> List[OrderRecord]:
        unique = list(dict.fromkeys(e for e in emails if e))[:MAX_EMAILS]
        if not unique:
            return []
        params: Dict[str, Any] = {f"e{i}": e for i, e in enumerate(unique)}
        params["start"] = start.isoformat()
        params["end"] = end.isoformat()
        rows = execute_query_dict(build_orders_for_emails_sql(len(unique)), params)
        logger.info("Order lookup by customer: %d customers → %d rows", len(unique), len(rows))
        return rows_to_orders(rows)
