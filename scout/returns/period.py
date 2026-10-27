"""Period order return rate.

Yankho's definition: returns submitted in a window divided by orders placed
in the same window. It is not a delivery cohort. A return counted here can
belong to an order placed before the window.

The numerator is the Redo ``RETURN`` table in ``SNOWFLAKE_DATABASE`` and
``SNOWFLAKE_SCHEMA``. There is no submitted-at column. ``CREATED_AT``
(``TIMESTAMP_TZ``) is filled on every return and is the moment the return was
created, so the window uses that timestamp in America/New_York.

The denominator is distinct orders in ``MAIN_OS.OS_ALL_ORDERS`` in
``SNOWFLAKE_DATABASE``.
``OS_DT`` is the order-date column. On every row it equals the UTC calendar
date of ``CREATED_AT`` (``TIMESTAMP_NTZ``). The window therefore filters
``CREATED_AT`` converted from UTC to America/New_York, and the return's
``CREATED_AT`` is converted to the same timezone. Both sides use those bounds.

The default denominator excludes ``OS_ORDER_STATUS`` values ``redo_exchange``
and ``loop_exchange`` (the only exchange or replacement order types on that
column), orders whose tags are exactly ``test`` or ``test order``, and orders
that are voided (``OS_ORDER_STATUS = 'voided'``) or cancelled
(``ORDER_CANCELLED_AT`` is not null). The default numerator excludes returns
with status ``rejected`` or ``deleted``. ``/returns`` shows that default.
Comparison figures stay off the Slack message.

There is no sales-channel column, so the rate is all channels.
``SHOP_ORDER_SOURCE_NAME`` is not a channel. Unmatched returns stay in the
numerator and are reported beside it.

Exchange share of submitted returns stays unavailable. Item outcome is not
one Redo column. The unit-cohort rates in ``scout.returns.metrics`` are a
different metric and stay unavailable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping, Sequence

from scout.returns.cohorts import BUSINESS_TZ_NAME, today_et
from scout.returns.discovery import ident
from scout.returns.metrics import MetricStatus, ratio_pct

RETURNS_SOURCE_LABEL = "SNOWFLAKE_DATABASE.SNOWFLAKE_SCHEMA.RETURN"
ORDERS_SOURCE_LABEL = "SNOWFLAKE_DATABASE.MAIN_OS.OS_ALL_ORDERS"
SUBMITTED_AT_COLUMN = "CREATED_AT"
ORDER_DATE_COLUMN = "OS_DT"
CANCELLED_AT_COLUMN = "ORDER_CANCELLED_AT"
PERIOD_LENGTHS: tuple[int, ...] = (7, 30, 180)
REPLACEMENT_ORDER_STATUSES: tuple[str, ...] = ("redo_exchange", "loop_exchange")
EXCLUDED_RETURN_STATUSES: tuple[str, ...] = ("rejected", "deleted")
TEST_ORDER_TAGS: tuple[str, ...] = ("test", "test order")

EXCHANGE_SHARE_REASON = (
    "Redo item outcome is not one column, so exchange share of submitted returns is not computed"
)
FILTERS_APPLIED: tuple[str, ...] = (
    "denominator excludes redo_exchange and loop_exchange order types",
    "denominator excludes orders tagged exactly test or test order",
    "denominator excludes voided orders and orders with ORDER_CANCELLED_AT set",
    "numerator excludes rejected and deleted returns",
)
FILTERS_NOT_APPLIED: tuple[str, ...] = (
    "no sales-channel column, so the rate is all channels",
)
PERIOD_NOTE = (
    "Default until the open choices are settled. Not a cohort rate: a return in the window "
    "can belong to an order placed earlier, so the ratio can exceed 100%. "
    "Order-date column is OS_DT (the UTC date of order CREATED_AT); both sides use "
    "America/New_York. All channels: OS_ALL_ORDERS has no sales-channel column. "
    "Denominator excludes redo_exchange and loop_exchange, test and test-order tags, "
    "and voided or cancelled orders. Numerator excludes rejected and deleted returns."
)


@dataclass(frozen=True)
class PeriodWindow:
    label: str
    start: date
    end: date
    as_of: date
    timezone: str = BUSINESS_TZ_NAME

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    def describe(self) -> str:
        last = self.end - timedelta(days=1)
        return f"{self.start:%b %d} – {last:%b %d, %Y} (ET)"


def period_window(days: int, as_of: date) -> PeriodWindow:
    """The ``days`` complete New York dates ending the day before ``as_of``."""
    if days <= 0:
        raise ValueError("days must be positive")
    return PeriodWindow(label=f"L{days}", start=as_of - timedelta(days=days), end=as_of, as_of=as_of)


def standard_windows(as_of: date | None = None) -> tuple[PeriodWindow, ...]:
    day = today_et() if as_of is None else as_of
    return tuple(period_window(days, day) for days in PERIOD_LENGTHS)


@dataclass(frozen=True)
class PeriodRate:
    """Headline counts use the default filters. The other counts are comparisons."""

    window: PeriodWindow
    orders_with_return: int | None
    return_count: int | None
    orders_placed: int | None
    unmatched_returns: int | None
    value_pct: float | None
    status: MetricStatus
    reason: str
    orders_with_return_including_rejected: int | None = None
    return_count_including_rejected: int | None = None
    unmatched_including_rejected: int | None = None
    orders_including_replacement_and_test: int | None = None
    orders_including_voided_and_cancelled: int | None = None


@dataclass(frozen=True)
class PeriodReport:
    rates: tuple[PeriodRate, ...]
    exchange_share_reason: str = EXCHANGE_SHARE_REASON
    filters_applied: tuple[str, ...] = FILTERS_APPLIED
    filters_not_applied: tuple[str, ...] = FILTERS_NOT_APPLIED
    note: str = PERIOD_NOTE

    @property
    def exchange_share_status(self) -> MetricStatus:
        return MetricStatus.UNAVAILABLE


def _qualified(database: str, schema: str, table: str) -> str:
    return ".".join(ident(part) for part in (database, schema, table))


def period_query(
    windows: Sequence[PeriodWindow],
    *,
    database: str | None = None,
    schema: str | None = None,
) -> tuple[str, dict[str, object]]:
    """One read-only statement for every window. Dates and labels are bound.

    The database and Redo schema come from the Snowflake environment. Tests
    pass stand-in names.
    """
    if not windows:
        raise ValueError("at least one window is required")
    database = os.environ["SNOWFLAKE_DATABASE"] if database is None else database
    schema = os.environ["SNOWFLAKE_SCHEMA"] if schema is None else schema
    values = []
    params: dict[str, object] = {}
    for i, window in enumerate(windows):
        values.append(f"(%(label_{i})s, %(start_{i})s, %(end_{i})s)")
        params[f"label_{i}"] = window.label
        params[f"start_{i}"] = window.start.isoformat()
        params[f"end_{i}"] = window.end.isoformat()
    returns = _qualified(database, schema, "RETURN")
    orders = _qualified(database, "MAIN_OS", "OS_ALL_ORDERS")
    submitted = ident(SUBMITTED_AT_COLUMN)
    placed_at = ident(SUBMITTED_AT_COLUMN)
    replacement = ", ".join(repr(status) for status in REPLACEMENT_ORDER_STATUSES)
    excluded_returns = ", ".join(repr(status) for status in EXCLUDED_RETURN_STATUSES)
    test_tags = "|".join(TEST_ORDER_TAGS)
    sql = f"""
WITH windows AS (
    SELECT column1 AS label, column2::DATE AS start_date, column3::DATE AS end_date
    FROM VALUES {", ".join(values)}
),
submitted AS (
    SELECT
        w.label,
        ret.ORDER_EXTERNAL_ID AS order_external_id,
        IFF(ret.STATUS IN ({excluded_returns}), 1, 0) AS excluded_status,
        IFF(
            ret.ORDER_EXTERNAL_ID IS NULL
            OR NOT EXISTS (
                SELECT 1
                FROM {orders} ord
                WHERE ord.SHOP_ORDER_ID = TRY_TO_NUMBER(ret.ORDER_EXTERNAL_ID)
                   OR ord.OS_ORDER_ID = TO_VARCHAR(ret.ORDER_EXTERNAL_ID)
            ),
            1, 0
        ) AS unmatched
    FROM {returns} ret
    JOIN windows w
      ON CONVERT_TIMEZONE('America/New_York', ret.{submitted})::DATE >= w.start_date
     AND CONVERT_TIMEZONE('America/New_York', ret.{submitted})::DATE < w.end_date
),
return_counts AS (
    SELECT
        label,
        COUNT(DISTINCT IFF(excluded_status = 0, order_external_id, NULL)) AS orders_with_return,
        COUNT(DISTINCT order_external_id) AS orders_with_return_including_rejected,
        SUM(IFF(excluded_status = 0, 1, 0)) AS return_count,
        COUNT(*) AS return_count_including_rejected,
        SUM(IFF(excluded_status = 0 AND unmatched = 1, 1, 0)) AS unmatched_returns,
        SUM(unmatched) AS unmatched_including_rejected
    FROM submitted
    GROUP BY label
),
placed AS (
    -- OS_DT is the order-date column: the UTC date of CREATED_AT.
    -- The window is that timestamp converted from UTC to America/New_York.
    SELECT
        w.label,
        ord.SHOP_ORDER_ID AS shop_order_id,
        IFF(ord.OS_ORDER_STATUS IN ({replacement}), 1, 0) AS is_replacement,
        IFF(
            REGEXP_LIKE(
                ',' || LOWER(COALESCE(ord.ORDER_TAGS, '')) || ',',
                ',[[:space:]]*({test_tags})[[:space:]]*,'
            ),
            1, 0
        ) AS is_test,
        IFF(ord.OS_ORDER_STATUS = 'voided' OR ord.ORDER_CANCELLED_AT IS NOT NULL, 1, 0) AS is_void_or_cancel
    FROM windows w
    JOIN {orders} ord
      ON CONVERT_TIMEZONE('UTC', 'America/New_York', ord.{placed_at})::DATE >= w.start_date
     AND CONVERT_TIMEZONE('UTC', 'America/New_York', ord.{placed_at})::DATE < w.end_date
),
order_counts AS (
    SELECT
        label,
        COUNT(DISTINCT IFF(is_replacement = 0 AND is_test = 0 AND is_void_or_cancel = 0, shop_order_id, NULL))
            AS orders_placed,
        COUNT(DISTINCT IFF(is_void_or_cancel = 0, shop_order_id, NULL))
            AS orders_including_replacement_and_test,
        COUNT(DISTINCT IFF(is_replacement = 0 AND is_test = 0, shop_order_id, NULL))
            AS orders_including_voided_and_cancelled
    FROM placed
    GROUP BY label
)
SELECT
    w.label,
    w.start_date,
    w.end_date,
    COALESCE(r.orders_with_return, 0) AS orders_with_return,
    COALESCE(r.return_count, 0) AS return_count,
    COALESCE(r.unmatched_returns, 0) AS unmatched_returns,
    COALESCE(o.orders_placed, 0) AS orders_placed,
    COALESCE(r.orders_with_return_including_rejected, 0) AS orders_with_return_including_rejected,
    COALESCE(r.return_count_including_rejected, 0) AS return_count_including_rejected,
    COALESCE(r.unmatched_including_rejected, 0) AS unmatched_including_rejected,
    COALESCE(o.orders_including_replacement_and_test, 0) AS orders_including_replacement_and_test,
    COALESCE(o.orders_including_voided_and_cancelled, 0) AS orders_including_voided_and_cancelled
FROM windows w
LEFT JOIN return_counts r ON r.label = w.label
LEFT JOIN order_counts o ON o.label = w.label
""".strip()
    return sql, params


def _cell(row: Mapping[str, object], name: str) -> object:
    lowered = {str(key).lower(): value for key, value in row.items()}
    return lowered.get(name)


def _whole(raw: object) -> int:
    if raw is None:
        raise ValueError("period count is missing")
    if isinstance(raw, bool):
        raise TypeError("A count cannot be a boolean.")
    return int(raw)


def _optional_whole(raw: object) -> int | None:
    if raw is None:
        return None
    return _whole(raw)


def evaluate_period(
    window: PeriodWindow,
    *,
    orders_with_return: int,
    return_count: int,
    orders_placed: int,
    unmatched_returns: int,
    orders_with_return_including_rejected: int | None = None,
    return_count_including_rejected: int | None = None,
    unmatched_including_rejected: int | None = None,
    orders_including_replacement_and_test: int | None = None,
    orders_including_voided_and_cancelled: int | None = None,
) -> PeriodRate:
    """Turn one window's counts into a rate. Over 100% is allowed."""
    for name, value in (
        ("orders_with_return", orders_with_return),
        ("return_count", return_count),
        ("orders_placed", orders_placed),
        ("unmatched_returns", unmatched_returns),
    ):
        if value < 0:
            raise ValueError(f"{name} cannot be negative")
    if unmatched_returns > return_count:
        raise ValueError("unmatched returns cannot exceed submitted returns")
    extras = dict(
        orders_with_return_including_rejected=orders_with_return_including_rejected,
        return_count_including_rejected=return_count_including_rejected,
        unmatched_including_rejected=unmatched_including_rejected,
        orders_including_replacement_and_test=orders_including_replacement_and_test,
        orders_including_voided_and_cancelled=orders_including_voided_and_cancelled,
    )
    if orders_placed <= 0:
        return PeriodRate(
            window, orders_with_return, return_count, orders_placed, unmatched_returns,
            None, MetricStatus.UNAVAILABLE, "no orders placed in the window", **extras,
        )
    return PeriodRate(
        window, orders_with_return, return_count, orders_placed, unmatched_returns,
        ratio_pct(orders_with_return, orders_placed), MetricStatus.PROVISIONAL,
        "not yet reconciled against live data", **extras,
    )


def report_from_rows(windows: Sequence[PeriodWindow], rows: Sequence[Mapping[str, object]]) -> PeriodReport:
    by_label = {str(_cell(row, "label")): row for row in rows}
    rates = []
    for window in windows:
        row = by_label.get(window.label)
        if row is None:
            rates.append(PeriodRate(
                window, None, None, None, None, None, MetricStatus.UNAVAILABLE, "count missing",
            ))
            continue
        rates.append(evaluate_period(
            window,
            orders_with_return=_whole(_cell(row, "orders_with_return")),
            return_count=_whole(_cell(row, "return_count")),
            orders_placed=_whole(_cell(row, "orders_placed")),
            unmatched_returns=_whole(_cell(row, "unmatched_returns")),
            orders_with_return_including_rejected=_optional_whole(
                _cell(row, "orders_with_return_including_rejected")),
            return_count_including_rejected=_optional_whole(_cell(row, "return_count_including_rejected")),
            unmatched_including_rejected=_optional_whole(_cell(row, "unmatched_including_rejected")),
            orders_including_replacement_and_test=_optional_whole(
                _cell(row, "orders_including_replacement_and_test")),
            orders_including_voided_and_cancelled=_optional_whole(
                _cell(row, "orders_including_voided_and_cancelled")),
        ))
    return PeriodReport(rates=tuple(rates))
