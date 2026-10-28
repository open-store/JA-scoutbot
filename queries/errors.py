"""
Scout Errors Query Module
Identifies error-related customer issues from Snowflake.

The error rate is the tag-based support error rate in
``scout.health.error_rate``. Categories, the CSAT comparison, and samples
still come from the subject keywords below.
"""

from typing import Sequence

from scout.health.error_rate import (
    NO_TAGS_REASON,
    ErrorRateReport,
    RateWindow,
    error_rate_query,
    report_from_rows,
    standard_rate_windows,
    unavailable_report,
)
from scout.integrations.snowflake import execute_query_dict
from scout.routing.command_parser import ParsedCommand
from scout.taxonomy.issue_tags import ERROR_RATE_GROUP, resolve_issue_tags


# Error-related keywords to search in subjects
ERROR_KEYWORDS = [
    'error', 'bug', 'broken', 'issue', 'problem', 'not working',
    'cannot', 'unable', 'fail', 'failed', 'failure',
    'glitch', 'crash', 'outage', 'wrong',
    'discount code', 'promo code', 'coupon',
    'checkout', 'payment', 'charge',
    'website', 'page',
    'login', 'password', 'sign in',
    'subscription', 'billing',
]


def _build_error_subject_filter() -> str:
    """Build a SQL LIKE/ILIKE filter for error-related subjects."""
    conditions = []
    for kw in ERROR_KEYWORDS:
        conditions.append(f"LOWER(SUBJECT) LIKE '%{kw.lower()}%'")
    return " OR ".join(conditions)


def run_error_rates(windows: Sequence[RateWindow] | None = None,
                    group: str = ERROR_RATE_GROUP) -> ErrorRateReport:
    """Support error rate per window. Default: L7, L30, and L180."""
    chosen = tuple(standard_rate_windows() if windows is None else windows)
    if not resolve_issue_tags(group)[0]:
        return unavailable_report(chosen, group, NO_TAGS_REASON)
    sql, params = error_rate_query(chosen, group)
    return report_from_rows(chosen, execute_query_dict(sql, params), group)


def run_errors(cmd: ParsedCommand) -> dict:
    """
    Execute error analysis for the given command.
    Returns a dict with all the data needed for formatting.
    """
    start = cmd.start_date.strftime("%Y-%m-%d %H:%M:%S")
    end = cmd.end_date.strftime("%Y-%m-%d %H:%M:%S")
    prev_start = cmd.previous_start_date.strftime("%Y-%m-%d %H:%M:%S")
    prev_end = cmd.previous_end_date.strftime("%Y-%m-%d %H:%M:%S")

    error_filter = _build_error_subject_filter()

    # --- Current period: total volume and error volume ---
    current_sql = f"""
    SELECT
        COUNT(*) AS total_conversations,
        COUNT(CASE WHEN ({error_filter}) THEN 1 END) AS error_conversations,
        ROUND(
            COUNT(CASE WHEN ({error_filter}) THEN 1 END) * 100.0 /
            NULLIF(COUNT(*), 0),
            1
        ) AS error_rate_pct
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= '{start}'
      AND CREATED_AT < '{end}'
    """
    current = execute_query_dict(current_sql)
    current_data = current[0] if current else {}

    # --- Previous period ---
    prev_sql = f"""
    SELECT
        COUNT(*) AS total_conversations,
        COUNT(CASE WHEN ({error_filter}) THEN 1 END) AS error_conversations,
        ROUND(
            COUNT(CASE WHEN ({error_filter}) THEN 1 END) * 100.0 /
            NULLIF(COUNT(*), 0),
            1
        ) AS error_rate_pct
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= '{prev_start}'
      AND CREATED_AT < '{prev_end}'
    """
    prev = execute_query_dict(prev_sql)
    prev_data = prev[0] if prev else {}

    # --- Error categorization by keyword group ---
    category_sql = f"""
    SELECT
        CASE
            WHEN LOWER(SUBJECT) LIKE '%discount%' OR LOWER(SUBJECT) LIKE '%promo%' OR LOWER(SUBJECT) LIKE '%coupon%'
                THEN 'Discount/Promo Code Issues'
            WHEN LOWER(SUBJECT) LIKE '%checkout%' OR LOWER(SUBJECT) LIKE '%payment%' OR LOWER(SUBJECT) LIKE '%charge%'
                THEN 'Checkout/Payment Issues'
            WHEN LOWER(SUBJECT) LIKE '%login%' OR LOWER(SUBJECT) LIKE '%account%' OR LOWER(SUBJECT) LIKE '%password%' OR LOWER(SUBJECT) LIKE '%sign in%'
                THEN 'Account/Login Issues'
            WHEN LOWER(SUBJECT) LIKE '%subscription%' OR LOWER(SUBJECT) LIKE '%billing%'
                THEN 'Subscription/Billing Issues'
            WHEN LOWER(SUBJECT) LIKE '%website%' OR LOWER(SUBJECT) LIKE '%site%' OR LOWER(SUBJECT) LIKE '%page%' OR LOWER(SUBJECT) LIKE '%app%'
                THEN 'Website/App Issues'
            ELSE 'Other Errors'
        END AS error_category,
        COUNT(*) AS cnt
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= '{start}'
      AND CREATED_AT < '{end}'
      AND ({error_filter})
    GROUP BY error_category
    ORDER BY cnt DESC
    """
    category_data = execute_query_dict(category_sql)

    # --- CSAT on error tickets vs overall ---
    csat_sql = f"""
    SELECT
        CASE WHEN ({error_filter}) THEN 'error' ELSE 'non_error' END AS ticket_type,
        COUNT(CASE WHEN SATISFACTION_RATING IS NOT NULL THEN 1 END) AS total_rated,
        ROUND(
            COUNT(CASE WHEN SATISFACTION_RATING IN ('4', '5') THEN 1 END) * 100.0 /
            NULLIF(COUNT(CASE WHEN SATISFACTION_RATING IS NOT NULL THEN 1 END), 0),
            1
        ) AS csat_pct
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= '{start}'
      AND CREATED_AT < '{end}'
    GROUP BY ticket_type
    """
    csat_data = execute_query_dict(csat_sql)

    # --- Sample error subjects ---
    samples_sql = f"""
    SELECT
        SUBJECT,
        CHANNEL,
        SATISFACTION_RATING,
        CREATED_AT
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= '{start}'
      AND CREATED_AT < '{end}'
      AND ({error_filter})
    ORDER BY CREATED_AT DESC
    LIMIT 15
    """
    samples_data = execute_query_dict(samples_sql)

    if cmd.start_date is None or cmd.end_date is None:
        raise ValueError("errors command has no window")
    error_rate = run_error_rates((
        RateWindow("current", cmd.start_date, cmd.end_date),
        RateWindow("previous", cmd.previous_start_date, cmd.previous_end_date),
    ))

    return {
        "current": current_data,
        "previous": prev_data,
        "error_rate": error_rate,
        "categories": category_data,
        "csat_comparison": csat_data,
        "samples": samples_data,
        "timeframe_label": cmd.timeframe_label,
        "days": cmd.days,
    }
