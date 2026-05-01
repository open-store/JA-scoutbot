"""
Scout VOC Query Module
Generates and executes Voice of Customer queries against Snowflake.
Uses the Richpanel tag mapping for deterministic theme classification.
Supports optional filters: product (subject keyword), tag (name or UUID), channel, agent.
"""

from command_parser import ParsedCommand
from snowflake_client import execute_query_dict
from tag_mapping import TAG_ID_TO_NAME
from product_feedback import get_product_messages, synthesize_product_feedback


def _build_filter_clause(cmd: ParsedCommand) -> tuple[str, str]:
    """
    Build additional WHERE clause fragments and a human-readable filter label
    from the parsed command's filters dict.
    Returns (sql_fragment, label_string).
    """
    clauses = []
    labels = []

    product = cmd.filters.get("product")
    if product:
        safe = product.replace("'", "''")
        clauses.append(f"AND LOWER(SUBJECT) LIKE '%{safe.lower()}%'")
        labels.append(f"product: {product}")

    channel = cmd.filters.get("channel")
    if channel:
        safe = channel.replace("'", "''")
        clauses.append(f"AND LOWER(CHANNEL) = '{safe.lower()}'")
        labels.append(f"channel: {channel}")

    tag = cmd.filters.get("tag")
    if tag:
        # Support filtering by tag name (resolve to UUIDs) or by UUID directly
        matching_uuids = [
            uuid for uuid, name in TAG_ID_TO_NAME.items()
            if tag.lower() in name.lower()
        ]
        if matching_uuids:
            uuid_list = ", ".join(f"'{u}'" for u in matching_uuids)
            clauses.append(
                f"AND EXISTS (SELECT 1 FROM LATERAL FLATTEN(input => PARSE_JSON(TAGS)) ft "
                f"WHERE ft.value::STRING IN ({uuid_list}))"
            )
        else:
            # Assume it's a raw UUID or partial UUID
            safe = tag.replace("'", "''")
            clauses.append(f"AND LOWER(TAGS::STRING) LIKE '%{safe.lower()}%'")
        labels.append(f"tag: {tag}")

    agent = cmd.filters.get("agent")
    if agent:
        safe = agent.replace("'", "''")
        clauses.append(f"AND LOWER(ASSIGNED_AGENT_ID::STRING) LIKE '%{safe.lower()}%'")
        labels.append(f"agent: {agent}")

    return "\n    ".join(clauses), ", ".join(labels) if labels else ""


# Tags that represent system processes, automation, or noise — not customer intent
EXCLUDE_TAGS = {
    "unknown", "other", "action", "comment", "issue",
    "ai-social-media-moderator", "ai-social-media-moderator-hidden",
    "ai-social-media-moderator-flagged", "ai-social-media-moderator-replied",
    "ai-social-media-moderator-autoclosed",
    "generic-comments-spam", "inappropriate-engagements",
    "gmail-import", "exclude-csat", "l1-manual", "l2-manual", "l2",
    "campaigns", "contest-giveaway",
}


def _resolve_tags(tag_rows: list[dict]) -> list[dict]:
    """
    Resolve tag UUIDs to human-readable names using the tag mapping.
    Excludes automation/noise tags to surface real customer intent themes.
    Returns enriched rows with TAG_NAME added.
    """
    resolved = []
    for row in tag_rows:
        uuid = row.get("TAG_UUID", "")
        name = TAG_ID_TO_NAME.get(uuid, None)
        if name and name not in EXCLUDE_TAGS:
            row["TAG_NAME"] = name
            resolved.append(row)
    return resolved


def run_voc(cmd: ParsedCommand) -> dict:
    """
    Execute VOC analysis for the given command.
    Returns a dict with all the data needed for formatting.
    Uses tag mapping for theme classification instead of subject-line keywords.
    """
    start = cmd.start_date.strftime("%Y-%m-%d %H:%M:%S")
    end = cmd.end_date.strftime("%Y-%m-%d %H:%M:%S")
    prev_start = cmd.previous_start_date.strftime("%Y-%m-%d %H:%M:%S")
    prev_end = cmd.previous_end_date.strftime("%Y-%m-%d %H:%M:%S")

    filter_sql, filter_label = _build_filter_clause(cmd)

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
      AND CREATED_AT >= '{start}'
      AND CREATED_AT < '{end}'
    {filter_sql}
    """
    volume = execute_query_dict(volume_sql)
    volume_data = volume[0] if volume else {}

    # --- Previous period volume ---
    prev_volume_sql = f"""
    SELECT
        COUNT(*) AS total_conversations
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= '{prev_start}'
      AND CREATED_AT < '{prev_end}'
    {filter_sql}
    """
    prev_volume = execute_query_dict(prev_volume_sql)
    prev_volume_data = prev_volume[0] if prev_volume else {}

    # --- Volume by channel ---
    channel_sql = f"""
    SELECT
        CHANNEL,
        COUNT(*) AS cnt
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= '{start}'
      AND CREATED_AT < '{end}'
    {filter_sql}
    GROUP BY CHANNEL
    ORDER BY cnt DESC
    """
    channel_data = execute_query_dict(channel_sql)

    # --- Tag frequency (current period) — the primary theme source ---
    tag_sql = f"""
    SELECT
        f.value::STRING AS tag_uuid,
        COUNT(*) AS cnt
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS,
    LATERAL FLATTEN(input => PARSE_JSON(TAGS)) f
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= '{start}'
      AND CREATED_AT < '{end}'
      AND TAGS IS NOT NULL
    {filter_sql}
    GROUP BY tag_uuid
    ORDER BY cnt DESC
    LIMIT 25
    """
    tag_data = execute_query_dict(tag_sql)

    # --- Tag frequency (previous period) ---
    prev_tag_sql = f"""
    SELECT
        f.value::STRING AS tag_uuid,
        COUNT(*) AS cnt
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS,
    LATERAL FLATTEN(input => PARSE_JSON(TAGS)) f
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= '{prev_start}'
      AND CREATED_AT < '{prev_end}'
      AND TAGS IS NOT NULL
    {filter_sql}
    GROUP BY tag_uuid
    ORDER BY cnt DESC
    LIMIT 50
    """
    prev_tag_data = execute_query_dict(prev_tag_sql)

    # --- Resolve tags to human-readable names ---
    resolved_tags = _resolve_tags(tag_data)
    resolved_prev_tags = _resolve_tags(prev_tag_data)

    # --- Status distribution ---
    status_sql = f"""
    SELECT
        STATUS,
        COUNT(*) AS cnt
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS
    WHERE _FIVETRAN_DELETED = FALSE
      AND CREATED_AT >= '{start}'
      AND CREATED_AT < '{end}'
    {filter_sql}
    GROUP BY STATUS
    ORDER BY cnt DESC
    """
    status_data = execute_query_dict(status_sql)

    # --- Product feedback synthesis (only when product filter is active) ---
    product_feedback = None
    product = cmd.filters.get("product") if cmd.filters else None
    if product:
        messages = get_product_messages(product, start, end, execute_query_dict)
        product_feedback = synthesize_product_feedback(
            messages, product, volume_data.get("TOTAL_CONVERSATIONS", 0)
        )

    return {
        "volume": volume_data,
        "prev_volume": prev_volume_data,
        "by_channel": channel_data,
        "tags": resolved_tags,
        "prev_tags": resolved_prev_tags,
        "status": status_data,
        "timeframe_label": cmd.timeframe_label,
        "days": cmd.days,
        "filter_label": filter_label,
        "product_feedback": product_feedback,
    }
