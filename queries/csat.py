"""
Scout CSAT Query Module
Generates and executes CSAT queries against Snowflake.
"""

from command_parser import ParsedCommand
from snowflake_client import execute_query_dict


def run_csat(cmd: ParsedCommand) -> dict:
    """
    Execute CSAT analysis for the given command.
    Returns a dict with all the data needed for formatting.
    """
    start = cmd.start_date.strftime("%Y-%m-%d %H:%M:%S")
    end = cmd.end_date.strftime("%Y-%m-%d %H:%M:%S")
    prev_start = cmd.previous_start_date.strftime("%Y-%m-%d %H:%M:%S")
    prev_end = cmd.previous_end_date.strftime("%Y-%m-%d %H:%M:%S")

    # --- Current period CSAT ---
    current_sql = f"""
    SELECT
        COUNT(*) AS total_conversations,
        COUNT(CASE WHEN SATISFACTION_RATING IS NOT NULL THEN 1 END) AS total_rated,
        COUNT(CASE WHEN SATISFACTION_RATING IN ('4', '5') THEN 1 END) AS positive_ratings,
        COUNT(CASE WHEN SATISFACTION_RATING IN ('1', '2', '3') THEN 1 END) AS negative_ratings,
        ROUND(
            COUNT(CASE WHEN SATISFACTION_RATING IN ('4', '5') THEN 1 END) * 100.0 /
            NULLIF(COUNT(CASE WHEN SATISFACTION_RATING IS NOT NULL THEN 1 END), 0),
            1
        ) AS csat_pct
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= '{start}'
      AND CREATED_AT < '{end}'
    """
    current = execute_query_dict(current_sql)
    current_data = current[0] if current else {}

    # --- Previous period CSAT ---
    prev_sql = f"""
    SELECT
        COUNT(*) AS total_conversations,
        COUNT(CASE WHEN SATISFACTION_RATING IS NOT NULL THEN 1 END) AS total_rated,
        COUNT(CASE WHEN SATISFACTION_RATING IN ('4', '5') THEN 1 END) AS positive_ratings,
        ROUND(
            COUNT(CASE WHEN SATISFACTION_RATING IN ('4', '5') THEN 1 END) * 100.0 /
            NULLIF(COUNT(CASE WHEN SATISFACTION_RATING IS NOT NULL THEN 1 END), 0),
            1
        ) AS csat_pct
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= '{prev_start}'
      AND CREATED_AT < '{prev_end}'
    """
    prev = execute_query_dict(prev_sql)
    prev_data = prev[0] if prev else {}

    # --- CSAT by channel ---
    channel_sql = f"""
    SELECT
        CHANNEL,
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
      AND SATISFACTION_RATING IS NOT NULL
    GROUP BY CHANNEL
    ORDER BY total_rated DESC
    """
    channel_data = execute_query_dict(channel_sql)

    # --- CSAT rating distribution ---
    dist_sql = f"""
    SELECT
        SATISFACTION_RATING,
        SATISFACTION_RATING_TEXT,
        COUNT(*) AS cnt
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= '{start}'
      AND CREATED_AT < '{end}'
      AND SATISFACTION_RATING IS NOT NULL
    GROUP BY SATISFACTION_RATING, SATISFACTION_RATING_TEXT
    ORDER BY SATISFACTION_RATING DESC
    """
    dist_data = execute_query_dict(dist_sql)

    # --- Low-CSAT conversations (subjects for theme analysis) ---
    low_csat_sql = f"""
    SELECT
        SUBJECT,
        CHANNEL,
        SATISFACTION_RATING,
        SATISFACTION_RATING_TEXT,
        TAGS,
        CREATED_AT
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= '{start}'
      AND CREATED_AT < '{end}'
      AND SATISFACTION_RATING IN ('1', '2', '3')
    ORDER BY CREATED_AT DESC
    LIMIT 25
    """
    low_csat_data = execute_query_dict(low_csat_sql)

    return {
        "current": current_data,
        "previous": prev_data,
        "by_channel": channel_data,
        "distribution": dist_data,
        "low_csat_samples": low_csat_data,
        "timeframe_label": cmd.timeframe_label,
        "days": cmd.days,
    }
