"""
Product-feedback repository.

Owns all Snowflake SQL for the product-feedback pipeline:
  - fetch candidate conversations by tag scope + product mention
  - fetch customer-authored message bodies for those conversations
  - strict vs fallback retrieval strategies

Candidate queries take an explicit, timezone-aware window and the same
channel / tag / agent filters that ``queries/voc.py`` applies to metrics.
Product phrases are matched as given; no pass broadens them to a first word.
A body mention counts only in a customer-authored message, so an agent
reply or product recommendation does not pull a ticket into scope.

No business logic lives here — only query building and execution.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Set

from scout.integrations.snowflake import execute_query_dict
from scout.sql import agent_filter, autoreply_exclusion, channel_filter, created_at_window, tag_filter
from scout.taxonomy.tag_ids import TAG_NAME_TO_UUIDS

logger = logging.getLogger("scout.repository.product_feedback")


# ---------------------------------------------------------------------------
# SQL fragments
# ---------------------------------------------------------------------------

_AUTOREPLY_EXCLUSION = autoreply_exclusion("c", indent="    ")

_CUSTOMER_AUTHORED_FILTER = """
    AND m.AUTHOR_ID = c.CUSTOMER_ID
"""

_CUSTOMER_AUTHORED_BROAD_FILTER = """
    AND m.AUTHOR_ID NOT IN ('hello@jackarcher.com', 'operator', 'jack.archer')
    AND m.AUTHOR_ID NOT LIKE 'jackarcher229%%'
"""

_BODY_NOT_EMPTY = """
    AND m.BODY IS NOT NULL
    AND LENGTH(TRIM(m.BODY)) > 10
"""

_NOT_DELETED = """
    AND c._FIVETRAN_DELETED = FALSE
    AND m._FIVETRAN_DELETED = FALSE
"""


def _tag_names_to_uuids(tag_names: Set[str]) -> List[str]:
    """Resolve a set of tag names to their Richpanel UUIDs."""
    uuids: List[str] = []
    for name in tag_names:
        uuids.extend(TAG_NAME_TO_UUIDS.get(name, []))
    return uuids


def _safe_phrase(phrase: str) -> str:
    """Strip characters that would end the literal or act as LIKE wildcards."""
    return "".join(ch for ch in phrase if ch not in "'\\%_")


def _build_alias_predicates(aliases: List[str], column: str) -> str:
    """Build a SQL OR clause matching any alias in a column.

    Example output: ``(LOWER(m.BODY) LIKE '%anytime crewneck%' OR LOWER(m.BODY) LIKE '%anytime crew neck%')``
    """
    safe = [s for s in (_safe_phrase(a) for a in aliases) if s.strip()]
    if not safe:
        return "FALSE"
    clauses = [f"LOWER({column}) LIKE '%{a}%'" for a in safe]
    return "(" + " OR ".join(clauses) + ")"


def build_scope_filters(filters: Optional[Mapping[str, str]], alias: str = "c") -> str:
    """Channel, tag, and agent predicates for the candidate queries."""
    if not filters:
        return ""
    clauses: List[str] = []
    if filters.get("channel"):
        clauses.append(channel_filter(filters["channel"], alias))
    if filters.get("tag"):
        clauses.append(tag_filter(filters["tag"], alias))
    if filters.get("agent"):
        clauses.append(agent_filter(filters["agent"], alias))
    return "\n      ".join(clauses)


# ---------------------------------------------------------------------------
# Candidate conversation retrieval
# ---------------------------------------------------------------------------

def fetch_strict_candidates(
    tag_uuids: List[str],
    aliases: List[str],
    start: datetime,
    end: datetime,
    filters: Optional[Mapping[str, str]] = None,
    max_conversations: int = 500,
) -> List[Dict[str, Any]]:
    """Strict pass: conversation has a product-feedback tag AND
    (message body or subject mentions the product/alias).

    Returns a list of dicts with CONVERSATION_ID, SUBJECT, TAGS, CHANNEL,
    CREATED_AT, CUSTOMER_EMAIL.
    """
    if not tag_uuids or not aliases:
        return []

    tag_list = ", ".join(f"'{u}'" for u in tag_uuids)
    body_pred = _build_alias_predicates(aliases, "m.BODY")
    subj_pred = _build_alias_predicates(aliases, "c.SUBJECT")

    sql = f"""
    SELECT DISTINCT
        c.ID            AS CONVERSATION_ID,
        c.SUBJECT,
        c.TAGS,
        c.CHANNEL,
        c.CREATED_AT,
        c.CUSTOMER_EMAIL
    FROM CONVERSATIONS c
    JOIN MESSAGES m ON m.CONVERSATION_ID = c.ID
    , LATERAL FLATTEN(input => PARSE_JSON(c.TAGS)) f
    WHERE f.value::STRING IN ({tag_list})
      AND ((m.AUTHOR_ID = c.CUSTOMER_ID AND {body_pred}) OR {subj_pred})
      {created_at_window(start, end)}
      {build_scope_filters(filters)}
      {_AUTOREPLY_EXCLUSION}
      {_NOT_DELETED}
    ORDER BY c.CREATED_AT DESC
    LIMIT {int(max_conversations)}
    """
    logger.info("Strict candidate query: tag_uuids=%d, aliases=%s, window=%s..%s",
                len(tag_uuids), aliases, start.isoformat(), end.isoformat())
    return execute_query_dict(sql)


def fetch_fallback_body_only_candidates(
    aliases: List[str],
    start: datetime,
    end: datetime,
    filters: Optional[Mapping[str, str]] = None,
    max_conversations: int = 500,
) -> List[Dict[str, Any]]:
    """Fallback pass: body/subject mention only (no tag requirement).

    Used when tag coverage is weak. The product match is the same as the
    strict pass; only the product-feedback tag requirement is dropped.
    """
    if not aliases:
        return []

    body_pred = _build_alias_predicates(aliases, "m.BODY")
    subj_pred = _build_alias_predicates(aliases, "c.SUBJECT")

    sql = f"""
    SELECT DISTINCT
        c.ID            AS CONVERSATION_ID,
        c.SUBJECT,
        c.TAGS,
        c.CHANNEL,
        c.CREATED_AT,
        c.CUSTOMER_EMAIL
    FROM CONVERSATIONS c
    JOIN MESSAGES m ON m.CONVERSATION_ID = c.ID
    WHERE ((m.AUTHOR_ID = c.CUSTOMER_ID AND {body_pred}) OR {subj_pred})
      {created_at_window(start, end)}
      {build_scope_filters(filters)}
      {_AUTOREPLY_EXCLUSION}
      {_NOT_DELETED}
    ORDER BY c.CREATED_AT DESC
    LIMIT {int(max_conversations)}
    """
    logger.info("Fallback body-only query: aliases=%s, window=%s..%s",
                aliases, start.isoformat(), end.isoformat())
    return execute_query_dict(sql)


# ---------------------------------------------------------------------------
# Message body retrieval
# ---------------------------------------------------------------------------

def fetch_customer_messages(
    conversation_ids: List[str],
    max_messages: int = 200,
    strict_customer: bool = True,
) -> List[Dict[str, Any]]:
    """Fetch customer-authored message bodies for a set of conversations.

    Parameters
    ----------
    conversation_ids : list of conversation IDs
    max_messages : cap on total messages returned
    strict_customer : if True, use ``AUTHOR_ID = CUSTOMER_ID`` (most precise).
        If False, use the broader exclusion filter (excludes known agents/system).

    Returns list of dicts with MESSAGE_ID, CONVERSATION_ID, AUTHOR_ID, BODY,
    CREATED_AT.
    """
    if not conversation_ids:
        return []

    # Snowflake IN clause has a practical limit; chunk if needed
    id_list = ", ".join(f"'{cid.replace(chr(39), '')}'" for cid in conversation_ids)
    customer_filter = _CUSTOMER_AUTHORED_FILTER if strict_customer else _CUSTOMER_AUTHORED_BROAD_FILTER

    sql = f"""
    SELECT
        m.ID            AS MESSAGE_ID,
        m.CONVERSATION_ID,
        m.AUTHOR_ID,
        m.BODY,
        m.CREATED_AT
    FROM MESSAGES m
    JOIN CONVERSATIONS c ON m.CONVERSATION_ID = c.ID
    WHERE m.CONVERSATION_ID IN ({id_list})
      {customer_filter}
      {_BODY_NOT_EMPTY}
      {_NOT_DELETED}
    ORDER BY m.CREATED_AT DESC
    LIMIT {max_messages}
    """
    logger.info("Fetching customer messages: conversations=%d, strict=%s, max=%d",
                len(conversation_ids), strict_customer, max_messages)
    return execute_query_dict(sql)


def _id_list(conversation_ids: List[str]) -> str:
    return ", ".join(f"'{cid.replace(chr(39), '')}'" for cid in conversation_ids)


def fetch_agent_messages(
    conversation_ids: List[str],
    max_messages: int = 400,
) -> List[Dict[str, Any]]:
    """Non-customer message bodies in these conversations.

    Used only to recognise agent text a customer quoted back. Never evidence.
    """
    if not conversation_ids:
        return []
    sql = f"""
    SELECT
        m.ID            AS MESSAGE_ID,
        m.CONVERSATION_ID,
        m.BODY
    FROM MESSAGES m
    JOIN CONVERSATIONS c ON m.CONVERSATION_ID = c.ID
    WHERE m.CONVERSATION_ID IN ({_id_list(conversation_ids)})
      AND m.AUTHOR_ID IS DISTINCT FROM c.CUSTOMER_ID
      {_BODY_NOT_EMPTY}
      {_NOT_DELETED}
    ORDER BY m.CREATED_AT DESC
    LIMIT {int(max_messages)}
    """
    logger.info("Fetching agent messages for quote removal: conversations=%d", len(conversation_ids))
    return execute_query_dict(sql)


def fetch_conversation_context(conversation_ids: List[str]) -> List[Dict[str, Any]]:
    """Per conversation: subject, channel, creation time, customer email, and
    whether the customer wrote the first message (and so the subject).

    The email is used only to match orders; it is not logged or displayed.
    """
    if not conversation_ids:
        return []
    sql = f"""
    SELECT
        c.ID                                    AS CONVERSATION_ID,
        ANY_VALUE(c.SUBJECT)                    AS SUBJECT,
        ANY_VALUE(c.CHANNEL)                    AS CHANNEL,
        ANY_VALUE(c.CREATED_AT)                 AS CREATED_AT,
        ANY_VALUE(LOWER(TRIM(c.CUSTOMER_EMAIL))) AS CUSTOMER_EMAIL,
        MIN_BY(IFF(m.AUTHOR_ID = c.CUSTOMER_ID, TRUE, FALSE), m.CREATED_AT) AS FIRST_FROM_CUSTOMER
    FROM CONVERSATIONS c
    JOIN MESSAGES m ON m.CONVERSATION_ID = c.ID
    WHERE c.ID IN ({_id_list(conversation_ids)})
      {_NOT_DELETED}
    GROUP BY c.ID
    """
    logger.info("Fetching conversation context: conversations=%d", len(conversation_ids))
    return execute_query_dict(sql)


def fetch_tagged_candidates_outside_scope(
    tag_uuids: List[str],
    start: datetime,
    end: datetime,
    exclude_ids: List[str],
    filters: Optional[Mapping[str, str]] = None,
    max_conversations: int = 200,
) -> List[Dict[str, Any]]:
    """Product-feedback-tagged conversations in the window that are not in the scope.

    These are candidates for the inferred tier: the customer described a
    product issue without a product mention that matched.
    """
    if not tag_uuids:
        return []
    tag_list = ", ".join(f"'{u}'" for u in tag_uuids)
    exclude = f"AND c.ID NOT IN ({_id_list(exclude_ids)})" if exclude_ids else ""
    sql = f"""
    SELECT DISTINCT
        c.ID            AS CONVERSATION_ID,
        c.CREATED_AT
    FROM CONVERSATIONS c
    , LATERAL FLATTEN(input => PARSE_JSON(c.TAGS)) f
    WHERE f.value::STRING IN ({tag_list})
      {created_at_window(start, end)}
      {build_scope_filters(filters)}
      {exclude}
      {_AUTOREPLY_EXCLUSION}
      AND c._FIVETRAN_DELETED = FALSE
    ORDER BY c.CREATED_AT DESC
    LIMIT {int(max_conversations)}
    """
    logger.info("Inferred-tier candidates: window=%s..%s, excluded=%d", start.isoformat(), end.isoformat(), len(exclude_ids))
    return execute_query_dict(sql)


def resolve_tag_names_to_uuids(tag_names: Set[str]) -> List[str]:
    """Public wrapper for tag name → UUID resolution."""
    return _tag_names_to_uuids(tag_names)
