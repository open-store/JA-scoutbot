"""
Scout VOC Query Module
Generates and executes Voice of Customer queries against Snowflake.
Supports optional filters: product (subject keyword), tag (UUID or partial), channel, agent.
"""

from command_parser import ParsedCommand
from snowflake_client import execute_query_dict


def _build_filter_clause_and_params(cmd: ParsedCommand) -> tuple[str, dict, str]:
    """
    Build additional WHERE clause fragments, bind params, and human-readable labels.
    Returns (sql_fragment, params, label_string).
    """
    clauses = []
    params: dict[str, str] = {}
    labels = []

    product = cmd.filters.get("product")
    if product:
        clauses.append("AND LOWER(SUBJECT) LIKE %(product)s")
        params["product"] = f"%{product.lower()}%"
        labels.append(f"product: {product}")

    channel = cmd.filters.get("channel")
    if channel:
        clauses.append("AND LOWER(CHANNEL) = %(channel)s")
        params["channel"] = channel.lower()
        labels.append(f"channel: {channel}")

    tag = cmd.filters.get("tag")
    if tag:
        clauses.append("AND LOWER(TAGS::STRING) LIKE %(tag)s")
        params["tag"] = f"%{tag.lower()}%"
        labels.append(f"tag: {tag}")

    agent = cmd.filters.get("agent")
    if agent:
        clauses.append("AND LOWER(ASSIGNED_AGENT_ID::STRING) LIKE %(agent)s")
        params["agent"] = f"%{agent.lower()}%"
        labels.append(f"agent: {agent}")

    return "\n    ".join(clauses), params, ", ".join(labels) if labels else ""


def run_voc(cmd: ParsedCommand) -> dict:
    """
    Execute VOC analysis for the given command.
    Returns a dict with all the data needed for formatting.
    Respects optional filters: product, channel, tag, agent.
    """
    start = cmd.start_date.strftime("%Y-%m-%d %H:%M:%S")
    end = cmd.end_date.strftime("%Y-%m-%d %H:%M:%S")
    prev_start = cmd.previous_start_date.strftime("%Y-%m-%d %H:%M:%S")
    prev_end = cmd.previous_end_date.strftime("%Y-%m-%d %H:%M:%S")

    filter_sql, filter_params, filter_label = _build_filter_clause_and_params(cmd)

    current_params = {"start": start, "end": end, **filter_params}
    previous_params = {"start": prev_start, "end": prev_end, **filter_params}

    # --- Current period volume ---
    volume_sql = f"""
    SELECT
        COUNT(*) AS total_conversations,
        COUNT(CASE WHEN SATISFACTION_RATING IS NOT NULL THEN 1 END) AS total_rated,
        ROUND(
            COUNT(CASE WHEN SATISFACTION_RATING IN ('4', '5') THEN 1 END) * 100.0 /
            NULLIF(COUNT(CASE WHEN SATISFACTION_RATING IS NOT NULL THEN 1 END), 0),
            1
        ) AS csat_pct
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= %(start)s
      AND CREATED_AT < %(end)s
    {filter_sql}
    """
    volume = execute_query_dict(volume_sql, current_params)
    volume_data = volume[0] if volume else {}

    # --- Previous period volume (same filters applied for fair comparison) ---
    prev_volume_sql = f"""
    SELECT
        COUNT(*) AS total_conversations
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= %(start)s
      AND CREATED_AT < %(end)s
    {filter_sql}
    """
    prev_volume = execute_query_dict(prev_volume_sql, previous_params)
    prev_volume_data = prev_volume[0] if prev_volume else {}

    # --- Volume by channel ---
    channel_sql = f"""
    SELECT
        CHANNEL,
        COUNT(*) AS cnt
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= %(start)s
      AND CREATED_AT < %(end)s
    {filter_sql}
    GROUP BY CHANNEL
    ORDER BY cnt DESC
    """
    channel_data = execute_query_dict(channel_sql, current_params)

    # --- Tag frequency (current period) ---
    tag_sql = f"""
    SELECT
        f.value::STRING AS tag_uuid,
        COUNT(*) AS cnt
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS,
    LATERAL FLATTEN(input => PARSE_JSON(TAGS)) f
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= %(start)s
      AND CREATED_AT < %(end)s
      AND TAGS IS NOT NULL
    {filter_sql}
    GROUP BY tag_uuid
    ORDER BY cnt DESC
    LIMIT 15
    """
    tag_data = execute_query_dict(tag_sql, current_params)

    # --- Tag frequency (previous period) ---
    prev_tag_sql = f"""
    SELECT
        f.value::STRING AS tag_uuid,
        COUNT(*) AS cnt
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS,
    LATERAL FLATTEN(input => PARSE_JSON(TAGS)) f
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= %(start)s
      AND CREATED_AT < %(end)s
      AND TAGS IS NOT NULL
    {filter_sql}
    GROUP BY tag_uuid
    ORDER BY cnt DESC
    LIMIT 30
    """
    prev_tag_data = execute_query_dict(prev_tag_sql, previous_params)

    # --- Subject themes: pull more subjects when filtered (for richer analysis) ---
    subject_limit = 200 if filter_sql else 100
    subjects_sql = f"""
    SELECT
        SUBJECT,
        CHANNEL,
        SATISFACTION_RATING,
        TAGS,
        CREATED_AT
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= %(start)s
      AND CREATED_AT < %(end)s
      AND SUBJECT IS NOT NULL
      AND SUBJECT != ''
    {filter_sql}
    ORDER BY CREATED_AT DESC
    LIMIT {subject_limit}
    """
    subjects_data = execute_query_dict(subjects_sql, current_params)

    # --- Status distribution ---
    status_sql = f"""
    SELECT
        STATUS,
        COUNT(*) AS cnt
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= %(start)s
      AND CREATED_AT < %(end)s
    {filter_sql}
    GROUP BY STATUS
    ORDER BY cnt DESC
    """
    status_data = execute_query_dict(status_sql, current_params)

    return {
        "volume": volume_data,
        "prev_volume": prev_volume_data,
        "by_channel": channel_data,
        "tags": tag_data,
        "prev_tags": prev_tag_data,
        "subjects": subjects_data,
        "status": status_data,
        "timeframe_label": cmd.timeframe_label,
        "days": cmd.days,
        "filter_label": filter_label,
    }
