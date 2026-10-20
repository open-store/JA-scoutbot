"""
Snowflake reads for the customer-health report.

- ``fetch_sync_times``: latest Fivetran sync and latest record for a synced
  table, looking back at most ``lookback_days``. Used to decide freshness.
- ``fetch_opening_messages``: the first customer-authored message of tickets
  created in the window, as ticket evidence for theme synthesis. The sample
  is spread across the window by a stable hash of the conversation ID rather
  than taken from the most recent days.

Both are read-only and bounded.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional

from scout.integrations.knocommerce import fqn as knocommerce_fqn
from scout.integrations.snowflake import execute_query_dict
from scout.sql import agent_filter, autoreply_exclusion, channel_filter, tag_filter

logger = logging.getLogger("scout.repository.health")

RICHPANEL_CONVERSATIONS = "FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS"
RICHPANEL_MESSAGES = "FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.MESSAGES"
KNOCOMMERCE_RESPONSE = knocommerce_fqn("RESPONSE")


def build_sync_sql(relation: str, lookback_days: int = 30) -> str:
    return f"""
    SELECT
        MAX(_FIVETRAN_SYNCED) AS LAST_SYNCED,
        MAX(CREATED_AT)       AS LATEST_RECORD
    FROM {relation}
    WHERE _FIVETRAN_SYNCED >= DATEADD('day', -{int(lookback_days)}, CURRENT_TIMESTAMP())
    """


def fetch_sync_times(relation: str, lookback_days: int = 30) -> Dict[str, Any]:
    rows = execute_query_dict(build_sync_sql(relation, lookback_days))
    return rows[0] if rows else {}


def _filters(filters: Optional[Mapping[str, str]]) -> str:
    if not filters:
        return ""
    clauses = []
    if filters.get("channel"):
        clauses.append(channel_filter(filters["channel"], "c"))
    if filters.get("tag"):
        clauses.append(tag_filter(filters["tag"], "c"))
    if filters.get("agent"):
        clauses.append(agent_filter(filters["agent"], "c"))
    return "\n      ".join(clauses)


def build_opening_messages_sql(start: str, end: str, filters: Optional[Mapping[str, str]] = None,
                               limit: int = 150) -> str:
    """``start``/``end`` use the same literal format as ``queries/voc.py`` so both read one window."""
    return f"""
    SELECT
        m.ID              AS MESSAGE_ID,
        m.CONVERSATION_ID,
        m.AUTHOR_ID,
        m.BODY,
        m.CREATED_AT,
        c.SUBJECT,
        c.CHANNEL
    FROM {RICHPANEL_MESSAGES} m
    JOIN {RICHPANEL_CONVERSATIONS} c ON m.CONVERSATION_ID = c.ID
    WHERE c._FIVETRAN_DELETED = FALSE
      AND m._FIVETRAN_DELETED = FALSE
      AND m.AUTHOR_ID = c.CUSTOMER_ID
      AND c.CREATED_AT >= '{start}'
      AND c.CREATED_AT < '{end}'
      AND m.BODY IS NOT NULL
      AND LENGTH(TRIM(m.BODY)) > 10
      {_filters(filters)}
      {autoreply_exclusion("c")}
    QUALIFY ROW_NUMBER() OVER (PARTITION BY m.CONVERSATION_ID ORDER BY m.CREATED_AT) = 1
    ORDER BY HASH(m.CONVERSATION_ID)
    LIMIT {int(limit)}
    """


def fetch_opening_messages(start: str, end: str, filters: Optional[Mapping[str, str]] = None,
                           limit: int = 150) -> List[Dict[str, Any]]:
    rows = execute_query_dict(build_opening_messages_sql(start, end, filters, limit))
    logger.info("Ticket evidence: %d opening messages (limit %d)", len(rows), limit)
    return rows
