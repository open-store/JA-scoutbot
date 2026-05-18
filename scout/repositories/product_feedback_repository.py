"""
Product-feedback repository.

Owns all Snowflake SQL for the product-feedback pipeline:
  - fetch candidate conversations by tag scope + product mention
  - fetch customer-authored message bodies for those conversations
  - strict vs fallback retrieval strategies

No business logic lives here — only query building and execution.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Set

# Use the existing shared Snowflake client
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from snowflake_client import execute_query_dict
from tag_mapping import TAG_ID_TO_NAME

logger = logging.getLogger("scout.repository.product_feedback")

# Reverse mapping: tag name → set of UUIDs (some names map to multiple UUIDs)
_TAG_NAME_TO_UUIDS: Dict[str, List[str]] = {}
for _uuid, _name in TAG_ID_TO_NAME.items():
    _TAG_NAME_TO_UUIDS.setdefault(_name, []).append(_uuid)


# ---------------------------------------------------------------------------
# SQL fragments
# ---------------------------------------------------------------------------

_AUTOREPLY_EXCLUSION = """
    AND LOWER(c.SUBJECT) NOT LIKE '%automatic reply%'
    AND LOWER(c.SUBJECT) NOT LIKE '%autoreply%'
    AND LOWER(c.SUBJECT) NOT LIKE '%auto-reply%'
    AND LOWER(c.SUBJECT) NOT LIKE '%out of office%'
    AND LOWER(c.SUBJECT) NOT LIKE '%vacation%'
    AND LOWER(c.SUBJECT) NOT LIKE '%away from the office%'
    AND LOWER(c.SUBJECT) NOT LIKE '%unsubscribe%'
"""

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
        uuids.extend(_TAG_NAME_TO_UUIDS.get(name, []))
    return uuids


def _build_alias_predicates(aliases: List[str], column: str) -> str:
    """Build a SQL OR clause matching any alias in a column.

    Example output: ``(LOWER(m.BODY) LIKE '%anytime crewneck%' OR LOWER(m.BODY) LIKE '%anytime crew neck%')``
    """
    if not aliases:
        return "FALSE"
    clauses = [f"LOWER({column}) LIKE '%{a.replace(chr(39), '')}%'" for a in aliases]
    return "(" + " OR ".join(clauses) + ")"


# ---------------------------------------------------------------------------
# Candidate conversation retrieval
# ---------------------------------------------------------------------------

def fetch_strict_candidates(
    tag_uuids: List[str],
    aliases: List[str],
    timeframe_days: int,
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
      AND ({body_pred} OR {subj_pred})
      AND c.CREATED_AT >= DATEADD('day', -{timeframe_days}, CURRENT_TIMESTAMP())
      {_AUTOREPLY_EXCLUSION}
      {_NOT_DELETED}
    ORDER BY c.CREATED_AT DESC
    LIMIT {max_conversations}
    """
    logger.info("Strict candidate query: tag_uuids=%d, aliases=%s, days=%d",
                len(tag_uuids), aliases, timeframe_days)
    return execute_query_dict(sql)


def fetch_fallback_relaxed_candidates(
    tag_uuids: List[str],
    aliases: List[str],
    timeframe_days: int,
    max_conversations: int = 500,
) -> List[Dict[str, Any]]:
    """Fallback pass 1: product-feedback tag + relaxed alias match.

    Uses broader matching (first keyword of product name) in body/subject.
    Still requires a product-feedback tag.
    """
    if not tag_uuids or not aliases:
        return []

    tag_list = ", ".join(f"'{u}'" for u in tag_uuids)
    # Use just the first word of each alias for broader matching
    broad_aliases = list({a.split()[0] for a in aliases if a.split()})
    body_pred = _build_alias_predicates(broad_aliases, "m.BODY")
    subj_pred = _build_alias_predicates(broad_aliases, "c.SUBJECT")

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
      AND ({body_pred} OR {subj_pred})
      AND c.CREATED_AT >= DATEADD('day', -{timeframe_days}, CURRENT_TIMESTAMP())
      {_AUTOREPLY_EXCLUSION}
      {_NOT_DELETED}
    ORDER BY c.CREATED_AT DESC
    LIMIT {max_conversations}
    """
    logger.info("Fallback relaxed query: tag_uuids=%d, broad_aliases=%s, days=%d",
                len(tag_uuids), broad_aliases, timeframe_days)
    return execute_query_dict(sql)


def fetch_fallback_body_only_candidates(
    aliases: List[str],
    timeframe_days: int,
    max_conversations: int = 300,
) -> List[Dict[str, Any]]:
    """Fallback pass 2: body/subject mention only (no tag requirement).

    Used when tag coverage is weak. Results should be ranked/filtered
    by the evidence service before synthesis.
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
    WHERE ({body_pred} OR {subj_pred})
      AND c.CREATED_AT >= DATEADD('day', -{timeframe_days}, CURRENT_TIMESTAMP())
      {_AUTOREPLY_EXCLUSION}
      {_NOT_DELETED}
    ORDER BY c.CREATED_AT DESC
    LIMIT {max_conversations}
    """
    logger.info("Fallback body-only query: aliases=%s, days=%d",
                aliases, timeframe_days)
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


def resolve_tag_names_to_uuids(tag_names: Set[str]) -> List[str]:
    """Public wrapper for tag name → UUID resolution."""
    return _tag_names_to_uuids(tag_names)
