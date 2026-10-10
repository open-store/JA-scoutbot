"""KnoCommerce survey tables in Snowflake.

NPS, PPS, and attribution all read ``ANALYTICS.KNOCOMMERCE__NPS___SURVEYS_``.
``date_range`` is UTC now-minus-N-days. The slash parser uses New York
calendar days. Those windows are different on purpose: aligning them
would change the numbers Scout reports.
"""

from datetime import datetime, timedelta, timezone

DB = "ANALYTICS"
SCHEMA = "KNOCOMMERCE__NPS___SURVEYS_"

# Live survey IDs. NPS uses all four. PPS and attribution look up two of them.
SURVEY_IDS = {
    "NPS Survey": "61e08ea9-7cb0-4e81-aea5-39c0cf0d7e84",
    "NPS Survey Copy": "e69d0c24-ad9c-449b-bf31-4f10867eb06d",
    "Returning Customer PPS": "81361b00-a8c6-4957-a632-838e133dace1",
    "PPS - New Customers": "1dd68a35-2c53-4822-9068-04dd7678d990",
}


def fqn(table: str) -> str:
    """Fully qualified Snowflake table name."""
    return f'"{DB}"."{SCHEMA}"."{table}"'


def date_range(days: int) -> tuple[str, str]:
    """Return ``(start_date, end_date)`` strings for the last N UTC days."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
