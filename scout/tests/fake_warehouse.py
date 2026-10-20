"""Test doubles for Snowflake and order lookups in product VOC tests.

``FakeWarehouse`` answers each product-feedback and VOC query by its shape
and records the SQL it was sent. ``FakeOrderLookup`` stands in for the
Snowflake order lookup. All data here is fixture data.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Sequence

from scout.models.orders import OrderLine, OrderRecord

TICKET_TIME = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)


def _ids_in(sql: str) -> List[str]:
    inside = sql.split("IN (", 1)[1].split(")", 1)[0] if "IN (" in sql else ""
    return re.findall(r"'([^']+)'", inside)


class FakeWarehouse:
    def __init__(self, cmd, current_strict, previous_strict, current_body=(), previous_body=(),
                 messages=None, agent_messages=None, contexts=None, outside_ids=()):
        self.prev_start = cmd.previous_start_date.isoformat()
        self.ids = {
            ("current", "strict"): list(current_strict),
            ("current", "body"): list(current_body),
            ("previous", "strict"): list(previous_strict),
            ("previous", "body"): list(previous_body),
        }
        self.messages: Dict[str, list] = messages if messages is not None else {}
        self.agent_messages: Dict[str, list] = agent_messages or {}
        self.contexts: Dict[str, dict] = contexts or {}
        self.outside_ids = list(outside_ids)
        self.repo_sql: List[str] = []
        self.voc_sql: List[str] = []

    # -- classification ------------------------------------------------------

    @staticmethod
    def is_candidate(sql: str) -> bool:
        return "AS CONVERSATION_ID" in sql and "LIKE '%" in sql and "FIRST_FROM_CUSTOMER" not in sql

    def candidate_calls(self, period: str, kind: str) -> List[str]:
        out = []
        for sql in self.repo_sql:
            if not self.is_candidate(sql):
                continue
            is_prev = f"TO_TIMESTAMP_TZ('{self.prev_start}')" in sql
            is_strict = "PARSE_JSON(c.TAGS)) f" in sql
            if (period == "previous") == is_prev and (kind == "strict") == is_strict:
                out.append(sql)
        return out

    def context_for(self, cid: str) -> dict:
        row = {
            "CONVERSATION_ID": cid, "SUBJECT": "", "CHANNEL": "email", "CREATED_AT": TICKET_TIME,
            "CUSTOMER_EMAIL": f"{cid}@example.com", "FIRST_FROM_CUSTOMER": True,
        }
        row.update(self.contexts.get(cid, {}))
        return row

    # -- answers -------------------------------------------------------------

    def repo(self, sql, params=None):
        self.repo_sql.append(sql)
        limit = re.search(r"LIMIT (\d+)", sql)
        cap = int(limit.group(1)) if limit else None
        if "FIRST_FROM_CUSTOMER" in sql:
            return [self.context_for(cid) for cid in _ids_in(sql)]
        if "IS DISTINCT FROM c.CUSTOMER_ID" in sql:
            rows = [m for cid in _ids_in(sql) for m in self.agent_messages.get(cid, [])]
            return rows[:cap] if cap else rows
        if "AS MESSAGE_ID" in sql:
            rows = [m for cid in sorted(set(_ids_in(sql))) for m in self.messages.get(cid, [])]
            return rows[:cap] if cap else rows
        if "c.ID NOT IN" in sql or ("LIKE '%" not in sql and "PARSE_JSON(c.TAGS)) f" in sql):
            return [{"CONVERSATION_ID": cid, "CREATED_AT": TICKET_TIME} for cid in self.outside_ids]
        period = "previous" if f"TO_TIMESTAMP_TZ('{self.prev_start}')" in sql else "current"
        kind = "strict" if "PARSE_JSON(c.TAGS)) f" in sql else "body"
        return [{"CONVERSATION_ID": cid} for cid in self.ids[(period, kind)]]

    def voc(self, sql, params=None):
        self.voc_sql.append(sql)
        ids = re.findall(r"'(conv-[^']+|prev-[^']+)'", sql)
        if "csat_pct" in sql:
            return [{"TOTAL_CONVERSATIONS": len(ids), "TOTAL_RATED": 0, "CSAT_PCT": None}]
        if "COUNT(*) AS total_conversations" in sql:
            return [{"TOTAL_CONVERSATIONS": len(ids)}]
        return []

    def previous_volume_sql(self) -> str:
        return next(s for s in self.voc_sql if "COUNT(*) AS total_conversations" in s and "csat_pct" not in s)

    def current_volume_sql(self) -> str:
        return next(s for s in self.voc_sql if "csat_pct" in s)


def message(cid: str, mid: str, body: str, author: str = "cust") -> dict:
    return {"MESSAGE_ID": mid, "CONVERSATION_ID": cid, "AUTHOR_ID": f"{author}-{cid}", "BODY": body,
            "CREATED_AT": TICKET_TIME}


def order(order_id: str, number: str, email: str, products: Iterable[str],
          placed: Optional[datetime] = None) -> OrderRecord:
    return OrderRecord(
        order_id=order_id,
        order_name=f"#{number}",
        customer_email=email,
        created_at=placed or (TICKET_TIME - timedelta(days=10)),
        lines=tuple(OrderLine(line_id=f"{order_id}-L{i}", product_title=p, quantity=1)
                    for i, p in enumerate(products)),
    )


class FakeOrderLookup:
    available = True
    source = "FIXTURE.ORDERS"
    reason = ""

    def __init__(self, orders: Sequence[OrderRecord]):
        self.orders = list(orders)
        self.number_calls: List[List[str]] = []
        self.email_calls: List[List[str]] = []

    def orders_by_number(self, order_numbers):
        self.number_calls.append(list(order_numbers))
        wanted = set(order_numbers)
        return [o for o in self.orders if o.order_number in wanted]

    def orders_for_emails(self, emails, start, end):
        self.email_calls.append(list(emails))
        wanted = set(emails)
        return [o for o in self.orders
                if o.customer_email in wanted and o.created_at is not None and start <= o.created_at < end]
