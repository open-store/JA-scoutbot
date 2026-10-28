"""Support error rate.

Yankho's definition: distinct conversations carrying a tag from one Issue
sub-category, divided by all conversations, in the same window. Both counts
read ``FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS`` with
``_FIVETRAN_DELETED = FALSE``. ``TAGS`` is a JSON array of tag UUIDs; the
group's tag names resolve to UUIDs through ``scout.taxonomy.tag_ids``.

Which sub-category counts as an error is not decided.
``scout.taxonomy.issue_tags.ERROR_RATE_GROUP`` holds the choice and defaults
to Storefront / UX, the tags behind the ``web_errors`` scope.

Windows are complete America/New_York days, the same L7, L30, and L180 as the
period order return rate. Bounds carry their UTC offset, so the Snowflake
session timezone does not move them. The rate is provisional and is never
marked validated here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from scout.returns.cohorts import BUSINESS_TZ_NAME
from scout.returns.metrics import MetricStatus, ratio_pct
from scout.returns.period import standard_windows
from scout.taxonomy.issue_tags import resolve_issue_tags

CONVERSATIONS = "FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS"
DATE_BASIS = (
    "Conversation CREATED_AT, complete America/New_York days bound with their UTC offsets; "
    "the same windows as the period order return rate. _FIVETRAN_DELETED = FALSE."
)
NO_TAGS_REASON = "no tag in the error group has a Richpanel UUID"

_BUSINESS_TZ = ZoneInfo(BUSINESS_TZ_NAME)


@dataclass(frozen=True)
class RateWindow:
    label: str
    start: datetime
    end: datetime

    def describe(self) -> str:
        last = self.end - timedelta(days=1)
        return f"{self.start:%b %d} – {last:%b %d, %Y} (ET)"


@dataclass(frozen=True)
class ErrorRate:
    window: RateWindow
    tagged: int | None
    total: int | None
    value_pct: float | None
    status: MetricStatus
    reason: str


@dataclass(frozen=True)
class ErrorRateReport:
    group: str
    rates: tuple[ErrorRate, ...]
    tags_matched: tuple[str, ...]
    tags_unresolved: tuple[str, ...]

    def rate(self, label: str) -> ErrorRate | None:
        return next((r for r in self.rates if r.window.label == label), None)


def rate_line_label(group: str) -> str:
    return f"Error rate ({group} tags, provisional)"


def _et_midnight(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=_BUSINESS_TZ)


def standard_rate_windows(as_of: date | None = None) -> tuple[RateWindow, ...]:
    """L7, L30, and L180 complete New York days, as ``scout.returns.period`` defines them."""
    return tuple(RateWindow(w.label, _et_midnight(w.start), _et_midnight(w.end)) for w in standard_windows(as_of))


def error_rate_query(windows: Sequence[RateWindow], group: str) -> tuple[str, dict[str, object]]:
    """One read-only statement for every window. Bounds and tag UUIDs are bound."""
    if not windows:
        raise ValueError("at least one window is required")
    uuids, _, _ = resolve_issue_tags(group)
    if not uuids:
        raise ValueError(NO_TAGS_REASON)
    values = []
    params: dict[str, object] = {}
    for i, window in enumerate(windows):
        if window.start.tzinfo is None or window.end.tzinfo is None:
            raise ValueError("error rate windows need timezone-aware bounds")
        values.append(f"(%(label_{i})s, %(start_{i})s, %(end_{i})s)")
        params[f"label_{i}"] = window.label
        params[f"start_{i}"] = window.start.isoformat()
        params[f"end_{i}"] = window.end.isoformat()
    tags = []
    for i, uuid in enumerate(uuids):
        tags.append(f"%(tag_{i})s")
        params[f"tag_{i}"] = uuid
    sql = f"""
WITH windows AS (
    SELECT column1 AS label, TO_TIMESTAMP_TZ(column2) AS start_at, TO_TIMESTAMP_TZ(column3) AS end_at
    FROM VALUES {", ".join(values)}
),
conversations AS (
    SELECT w.label, c.ID, c.TAGS
    FROM windows w
    JOIN {CONVERSATIONS} c
      ON c.CREATED_AT >= w.start_at
     AND c.CREATED_AT < w.end_at
    WHERE c._FIVETRAN_DELETED = FALSE
),
tagged AS (
    SELECT DISTINCT c.label, c.ID
    FROM conversations c, LATERAL FLATTEN(input => PARSE_JSON(c.TAGS)) f
    WHERE f.value::STRING IN ({", ".join(tags)})
)
SELECT
    w.label,
    COUNT(DISTINCT c.ID) AS total_conversations,
    COUNT(DISTINCT t.ID) AS tagged_conversations
FROM windows w
LEFT JOIN conversations c ON c.label = w.label
LEFT JOIN tagged t ON t.label = c.label AND t.ID = c.ID
GROUP BY w.label
""".strip()
    return sql, params


def _cell(row: Mapping[str, object], name: str) -> object:
    return {str(key).lower(): value for key, value in row.items()}.get(name)


def _whole(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, Decimal, str)):
        raise ValueError("error rate count is missing")
    value = int(raw)
    if value < 0:
        raise ValueError("error rate counts cannot be negative")
    return value


def evaluate(window: RateWindow, tagged: int, total: int) -> ErrorRate:
    if tagged > total:
        raise ValueError("tagged conversations cannot exceed all conversations")
    if total <= 0:
        return ErrorRate(window, tagged, total, None, MetricStatus.UNAVAILABLE, "no conversations in the window")
    return ErrorRate(window, tagged, total, ratio_pct(tagged, total), MetricStatus.PROVISIONAL,
                     "not yet reconciled against live data")


def report_from_rows(windows: Sequence[RateWindow], rows: Sequence[Mapping[str, object]],
                     group: str) -> ErrorRateReport:
    _, matched, unresolved = resolve_issue_tags(group)
    by_label = {str(_cell(row, "label")): row for row in rows}
    rates = []
    for window in windows:
        row = by_label.get(window.label)
        if row is None:
            rates.append(ErrorRate(window, None, None, None, MetricStatus.UNAVAILABLE, "count missing"))
            continue
        rates.append(evaluate(window, _whole(_cell(row, "tagged_conversations")),
                              _whole(_cell(row, "total_conversations"))))
    return ErrorRateReport(group, tuple(rates), matched, unresolved)


def unavailable_report(windows: Sequence[RateWindow], group: str, reason: str) -> ErrorRateReport:
    _, matched, unresolved = resolve_issue_tags(group)
    rates = tuple(ErrorRate(w, None, None, None, MetricStatus.UNAVAILABLE, reason) for w in windows)
    return ErrorRateReport(group, rates, matched, unresolved)
