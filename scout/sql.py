"""Shared SQL fragments.

The LIKE list is the auto-reply exclusion used by general VOC and by the
product-feedback repository. ``alias`` is the table alias (``c`` in the
repository, none in VOC). ``indent`` keeps the surrounding query's
whitespace so the SQL string does not change.

The channel, tag, and agent filters are shared by ``queries/voc.py`` metrics
and the product-feedback scope, so both describe the same population.
"""
from datetime import datetime

from scout.taxonomy.tag_ids import TAG_ID_TO_NAME

_AUTOREPLY_PATTERNS = (
    "%automatic reply%",
    "%autoreply%",
    "%auto-reply%",
    "%out of office%",
    "%vacation%",
    "%away from the office%",
    "%unsubscribe%",
)


def autoreply_exclusion(alias: str = "", indent: str = "      ") -> str:
    """Return the auto-reply ``AND NOT LIKE`` block, including surrounding newlines."""
    column = f"{alias}.SUBJECT" if alias else "SUBJECT"
    lines = [
        f"{indent}AND LOWER({column}) NOT LIKE '{pattern}'"
        for pattern in _AUTOREPLY_PATTERNS
    ]
    return "\n" + "\n".join(lines) + "\n"


def _col(alias: str, name: str) -> str:
    return f"{alias}.{name}" if alias else name


def channel_filter(channel: str, alias: str = "") -> str:
    safe = channel.replace("'", "''")
    return f"AND LOWER({_col(alias, 'CHANNEL')}) = '{safe.lower()}'"


def tag_filter(tag: str, alias: str = "") -> str:
    """Tag names containing *tag* (case-insensitive), else a text match on TAGS."""
    tags_col = _col(alias, "TAGS")
    matching_uuids = [
        uuid for uuid, name in TAG_ID_TO_NAME.items()
        if tag.lower() in name.lower()
    ]
    if matching_uuids:
        uuid_list = ", ".join(f"'{u}'" for u in matching_uuids)
        return (
            f"AND EXISTS (SELECT 1 FROM LATERAL FLATTEN(input => PARSE_JSON({tags_col})) ft "
            f"WHERE ft.value::STRING IN ({uuid_list}))"
        )
    safe = tag.replace("'", "''")
    return f"AND LOWER({tags_col}::STRING) LIKE '%{safe.lower()}%'"


def agent_filter(agent: str, alias: str = "") -> str:
    safe = agent.replace("'", "''")
    return f"AND LOWER({_col(alias, 'ASSIGNED_AGENT_ID')}::STRING) LIKE '%{safe.lower()}%'"


def created_at_window(start: datetime, end: datetime, column: str = "c.CREATED_AT") -> str:
    """``start <= column < end`` with explicit UTC offsets.

    Bounds carry their offset, so the window does not depend on the
    Snowflake session timezone.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("created_at_window needs timezone-aware bounds")
    return (
        f"AND {column} >= TO_TIMESTAMP_TZ('{start.isoformat()}')\n"
        f"      AND {column} < TO_TIMESTAMP_TZ('{end.isoformat()}')"
    )
