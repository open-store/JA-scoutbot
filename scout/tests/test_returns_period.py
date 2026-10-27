"""Period order return rate: windows, SQL shape, and ratio. No Snowflake connection."""

from datetime import date

import pytest

from scout.returns.discovery import assert_read_only
from scout.returns.metrics import MetricStatus
from scout.returns.period import (
    PERIOD_LENGTHS,
    evaluate_period,
    period_query,
    period_window,
    report_from_rows,
    standard_windows,
)


AS_OF = date(2026, 10, 27)


def test_standard_windows_are_complete_new_york_days_with_shared_bounds():
    windows = standard_windows(AS_OF)
    assert tuple(w.days for w in windows) == PERIOD_LENGTHS
    assert [w.label for w in windows] == ["L7", "L30", "L180"]
    assert all(w.end == AS_OF and w.timezone == "America/New_York" for w in windows)
    assert windows[0].start == date(2026, 10, 20)
    assert windows[1].start == date(2026, 9, 27)
    assert windows[2].start == date(2026, 4, 30)
    assert windows[0].describe() == "Oct 20 – Oct 26, 2026 (ET)"


def test_query_is_read_only_and_keeps_unmatched_returns_in_the_numerator():
    sql, params = period_query(standard_windows(AS_OF), database="SCOUT_DB", schema="SCOUT_SCHEMA")
    assert_read_only(sql)
    assert "ORDER_CANCELLED_AT" in sql and "IS NOT NULL" in sql
    assert "CONVERT_TIMEZONE('America/New_York', ret.\"CREATED_AT\")" in sql
    assert "CONVERT_TIMEZONE('UTC', 'America/New_York', ord.\"CREATED_AT\")" in sql
    assert "OS_DT is the order-date column" in sql
    assert "redo_exchange" in sql and "loop_exchange" in sql
    assert "rejected" in sql and "deleted" in sql
    assert "voided" in sql
    assert "test order" in sql
    assert '"SCOUT_DB"."SCOUT_SCHEMA"."RETURN"' in sql
    assert '"SCOUT_DB"."MAIN_OS"."OS_ALL_ORDERS"' in sql
    assert "online_store" not in sql
    assert "SALES_CHANNEL" not in sql
    assert "WHERE unmatched" not in sql
    assert "COUNT(DISTINCT order_external_id)" in sql
    for value in ("2026-10-20", "2026-10-27", "L180"):
        assert value not in sql
    assert params["label_0"] == "L7"
    assert params["start_0"] == "2026-10-20"
    assert params["end_2"] == "2026-10-27"
    assert params["start_2"] == "2026-04-30"


def test_rate_uses_distinct_orders_and_allows_a_ratio_above_100():
    window = period_window(7, AS_OF)
    rate = evaluate_period(
        window, orders_with_return=50, return_count=60, orders_placed=40, unmatched_returns=3,
    )
    assert rate.value_pct == pytest.approx(125.0)
    assert rate.status is MetricStatus.PROVISIONAL
    assert rate.return_count == 60 and rate.unmatched_returns == 3


def test_zero_orders_placed_is_unavailable_and_a_zero_numerator_is_zero():
    window = period_window(7, AS_OF)
    empty = evaluate_period(window, orders_with_return=0, return_count=0, orders_placed=0, unmatched_returns=0)
    assert empty.status is MetricStatus.UNAVAILABLE and empty.value_pct is None

    none = evaluate_period(window, orders_with_return=0, return_count=0, orders_placed=10, unmatched_returns=0)
    assert none.value_pct == 0.0 and none.status is MetricStatus.PROVISIONAL


def test_report_matches_rows_by_label_and_keeps_exchange_share_unavailable():
    windows = standard_windows(AS_OF)
    report = report_from_rows(windows, [{
        "label": "L30",
        "orders_with_return": 2,
        "return_count": 3,
        "unmatched_returns": 1,
        "orders_placed": 8,
    }])
    by_label = {r.window.label: r for r in report.rates}
    assert by_label["L30"].value_pct == pytest.approx(25.0)
    assert by_label["L30"].orders_including_replacement_and_test is None
    assert by_label["L7"].status is MetricStatus.UNAVAILABLE
    assert by_label["L7"].reason == "count missing"
    assert report.exchange_share_status is MetricStatus.UNAVAILABLE
    assert "not one column" in report.exchange_share_reason


def test_unmatched_cannot_exceed_submitted_returns():
    with pytest.raises(ValueError):
        evaluate_period(
            period_window(7, AS_OF),
            orders_with_return=1, return_count=1, orders_placed=10, unmatched_returns=2,
        )
