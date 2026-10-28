"""`/returns`: refund volume stays unchanged, rates show explicit states.

Snowflake is mocked. Nothing here opens a connection.
"""

from datetime import date

import pytest

from queries import returns
from scout.formatting import format_returns
from scout.returns import cohorts, metrics
from scout.returns.metrics import (
    MetricStatus,
    OutcomeBreakdown,
    RatesReport,
    UnitCounts,
    evaluate_rates,
)


class _Cursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql):
        self.log.append(sql)

    def fetchone(self):
        if "MIN(REFUND_DATE_ET)" in self.log[-1]:
            return (1000, 1300, 120, 150, 80, 90, 800, 1060, 700, 14.2, "2026-09-20", "2026-10-19")
        return (950, 1210)

    def fetchall(self):
        sql = self.log[-1]
        if "GROUP BY PRODUCT_TITLE" in sql:
            return [("Clubhouse Polo - Navy / M", 60, 55), ("Atlas Short - Khaki / 32", 40, 38)]
        if "GROUP BY CATEGORY" in sql:
            return [("Tops", 500), ("Bottoms", 420)]
        if "GROUP BY REFUND_NOTE" in sql:
            return [("Customer requested refund", 30), ("Damaged", 12)]
        if "GROUP BY RETURN_PLATFORM" in sql:
            return [("Redo", 700), ("Manual", 300)]
        return []

    def close(self):
        pass


class _Conn:
    def __init__(self, log):
        self.log = log

    def cursor(self):
        return _Cursor(self.log)

    def close(self):
        pass


PERIOD_ROWS = [
    {
        "LABEL": "L7", "ORDERS_WITH_RETURN": 10, "RETURN_COUNT": 12, "UNMATCHED_RETURNS": 1, "ORDERS_PLACED": 100,
        "ORDERS_WITH_EXCHANGE": 2, "RETURNS_WITH_EXCHANGE": 3, "EXCHANGE_ITEMS": 4, "RETURNED_ITEMS": 20,
        "REFUND_ITEMS": 10, "STORE_CREDIT_ITEMS": 4, "NONE_ITEMS": 2,
        "REDO_EXCHANGE_ORDERS_PLACED": 6, "EXCHANGE_ORDER_IDS": 5,
        "EXCHANGE_RETURNS_MISSING_ORDER_ID": 1, "EXCHANGE_ORDER_IDS_PLACED_IN_WINDOW": 1,
    },
    {
        "LABEL": "L30", "ORDERS_WITH_RETURN": 40, "RETURN_COUNT": 45, "UNMATCHED_RETURNS": 2, "ORDERS_PLACED": 400,
        "ORDERS_WITH_EXCHANGE": 8, "RETURNS_WITH_EXCHANGE": 9, "EXCHANGE_ITEMS": 10, "RETURNED_ITEMS": 50,
        "REFUND_ITEMS": 20, "STORE_CREDIT_ITEMS": 15, "NONE_ITEMS": 5,
        "REDO_EXCHANGE_ORDERS_PLACED": 12, "EXCHANGE_ORDER_IDS": 11,
        "EXCHANGE_RETURNS_MISSING_ORDER_ID": 2, "EXCHANGE_ORDER_IDS_PLACED_IN_WINDOW": 4,
    },
    {
        "LABEL": "L180", "ORDERS_WITH_RETURN": 90, "RETURN_COUNT": 100, "UNMATCHED_RETURNS": 0, "ORDERS_PLACED": 900,
        "ORDERS_WITH_EXCHANGE": 18, "RETURNS_WITH_EXCHANGE": 20, "EXCHANGE_ITEMS": 22, "RETURNED_ITEMS": 80,
        "REFUND_ITEMS": 40, "STORE_CREDIT_ITEMS": 10, "NONE_ITEMS": 8,
        "REDO_EXCHANGE_ORDERS_PLACED": 30, "EXCHANGE_ORDER_IDS": 25,
        "EXCHANGE_RETURNS_MISSING_ORDER_ID": 4, "EXCHANGE_ORDER_IDS_PLACED_IN_WINDOW": 9,
    },
]


def fake_period_query(sql, params):
    """Period-rate rows only. Unit cohort rates must not reach this query."""
    if "COHORT_UNITS" in sql or "eligible_delivered_units" in sql:
        raise AssertionError("unit cohort rates must not query Snowflake while they are unverified")
    assert "ORDER_CANCELLED_AT" in sql
    assert "CONVERT_TIMEZONE('America/New_York'" in sql
    return PERIOD_ROWS


@pytest.fixture
def export_queries(monkeypatch):
    log: list[str] = []
    monkeypatch.setattr(returns, "get_connection", lambda: _Conn(log))
    monkeypatch.setattr(returns, "execute_query_dict", fake_period_query)
    return log


def test_refund_volume_numbers_are_unchanged(export_queries):
    data = returns.run_returns(30)
    assert len(export_queries) == 6
    assert all(returns.TABLE in sql for sql in export_queries)
    assert (data["total_lines"], data["total_qty_returned"]) == (1000, 1300)
    assert (data["exchange_pct"], data["cancel_pct"], data["straight_pct"], data["redo_pct"]) == (8.0, 12.0, 80.0, 70.0)
    assert data["volume_change_pct"] == 5.3


def test_rates_are_unavailable_with_exact_blockers(export_queries):
    report = returns.run_returns(30)["rates"]
    assert isinstance(report, RatesReport)
    assert not report.available
    assert report.window is None
    assert all(r.status is MetricStatus.UNAVAILABLE and r.value_pct is None for r in report.results)
    text = " ".join(report.blockers)
    assert "FCT_SHOPIFY__ORDER_LINES" in text
    assert "not a single day horizon" in text
    assert "cohort join is not built" in text
    assert "until the Redo table is profiled" not in text


def test_report_leads_with_the_period_rate_and_labels_the_export_honestly(export_queries):
    text = format_returns(returns.run_returns(30))
    period_at = text.index("*Period order return rate*")
    unit_at = text.index("*Unit cohort rates*")
    assert period_at < unit_at < text.index("*Refund volume*") < text.index("*Refund mix*")
    assert "• *L30" in text and "10.0% (40 orders with a return submitted ÷ 400 orders placed)" in text
    assert "*Raw returns submitted:* 45" in text
    assert "*Unmatched returns:* 2" in text
    assert "Not a cohort rate" in text
    assert "Order-date column is OS_DT" in text
    assert "America/New_York" in text
    assert "no sales-channel column" in text
    assert "EXCHANGE_ORDER_EXTERNAL_ID" in text
    assert "are not exclusion rules" in text
    assert "rejected and deleted" in text
    assert "including rejected" not in text
    assert "with them included" not in text
    assert "Period exchange rate:* 2.0% (8 orders with an exchanged item ÷ 400 orders placed)" in text
    assert "Exchange share of submitted returns:* 20.0% (9 returns with an exchanged item ÷ 45 returns submitted)" in text
    assert "Exchanged items ÷ returned items:* 20.0% (10 exchanged items ÷ 50 returned items)" in text
    assert "Outcome mix of returned items:* 10 exchange, 20 refund, 15 store credit, 5 none" in text
    assert "redo_exchange cross-check:* 12 redo_exchange orders placed; 11 exchange-order ids on submitted returns (2 exchange returns have no id); 4 of those ids were placed in the window" in text
    assert "EXCHANGE_ITEM_QUANTITY > 0" in text
    for label in (
        "Unit cohort item return rate",
        "Unit cohort item exchange rate",
        "Unit cohort exchange share of returned units",
        "Unit cohort item refund rate",
    ):
        assert f"• *{label}:* unavailable" in text
    assert "Not available yet:" in text
    assert "Shopify refund export (`EXPORT_CSX__RETURNS`)" in text
    assert "(8.0% of lines)" in text
    assert "share of refund lines, not a rate against orders" in text
    assert "*Internal refund notes*" in text and "not customer reasons" in text
    assert "Refund volume is up" in text
    period_text = text.split("*Unit cohort rates*")[0]
    assert "All channels" in period_text
    assert "sales_channel = online_store" not in period_text
    assert "Notable refund reasons" not in text
    assert "the most returned product this period" not in text


def test_product_filter_keeps_rates_unavailable(export_queries):
    report = returns.run_returns(30, product_filter="Atlas Short")["rates"]
    assert "product-level rates need SKUs resolved from the catalog" in report.blockers


def test_unblocked_rates_are_provisional_never_validated(monkeypatch):
    monkeypatch.setattr(returns, "source_blockers", lambda: {})
    monkeypatch.setattr(cohorts, "MATURITY_DAYS", 30)
    monkeypatch.setattr(cohorts, "today_et", lambda now=None: date(2026, 10, 20))
    monkeypatch.setattr(metrics, "COHORT_UNITS_RELATION", "COHORT_UNITS")
    captured = {}

    def _fake_query(sql, params):
        captured["sql"], captured["params"] = sql, params
        return [{
            "ELIGIBLE_DELIVERED_UNITS": 10_000, "REDO_RETURNED_UNITS": 1_200,
            "REDO_EXCHANGED_UNITS": 300, "REDO_REFUNDED_UNITS": 700,
            "REDO_STORE_CREDIT_UNITS": 50, "REDO_OPEN_UNITS": 100,
        }]

    monkeypatch.setattr(returns, "execute_query_dict", _fake_query)
    report = returns.run_return_rates(30)
    assert captured["sql"].endswith("is_cohort_mature = TRUE")
    assert captured["params"]["start"] == "2026-08-21"
    assert report.available and not report.blockers
    assert all(r.status is MetricStatus.PROVISIONAL for r in report.results)


def test_provisional_rates_show_counts_denominators_and_window():
    window = cohorts.mature_window(30, 30, date(2026, 10, 20))
    counts = UnitCounts(eligible_delivered_units=10_032, redo_returned_units=1_234,
                        redo_exchanged_units=311, redo_refunded_units=800)
    report = RatesReport(
        results=evaluate_rates(counts, mature=True, reconciled=False),
        window=window,
        scope_label="online store, DTC",
        outcomes=OutcomeBreakdown(returned=1_234, refunded=800, exchanged=311, store_credit=50, open=60, unexplained=13),
    )
    text = format_returns({"total_lines": 1, "total_qty_returned": 1, "rates": report})
    assert "• *Unit cohort item return rate:* 12.3% (1,234 of 10,032 eligible delivered units) — _provisional: not yet reconciled against live data_" in text
    assert "• *Unit cohort item exchange rate:* 3.1% (311 of 10,032 eligible delivered units)" in text
    assert "• *Unit cohort exchange share of returned units:* 25.2% (311 of 1,234 Redo returned units)" in text
    assert "deliveries Aug 21 – Sep 19, 2026 (ET), returns within 30 days of delivery, mature" in text
    assert "13 other or unexplained" in text
    assert "Not available yet" not in text


def test_formatter_without_rates_still_renders_volume():
    text = format_returns({"total_lines": 3, "total_qty_returned": 4})
    assert "*Return and exchange rates*" not in text
    assert "*Refund volume*" in text


def test_help_names_the_period_rate_and_keeps_unit_cohort_rates_unavailable():
    from scout.formatting.help import format_help

    line = next(l for l in format_help().splitlines() if "/returns" in l)
    assert "Period return rate" in line
    assert "exchange rate" in line
    assert "not available yet" in line
