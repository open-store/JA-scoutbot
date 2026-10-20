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


@pytest.fixture
def export_queries(monkeypatch):
    log: list[str] = []
    monkeypatch.setattr(returns, "get_connection", lambda: _Conn(log))

    def _no_rate_queries(*args, **kwargs):
        raise AssertionError("rates must not query Snowflake while sources are unverified")

    monkeypatch.setattr(returns, "execute_query_dict", _no_rate_queries)
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
    assert "`ANALYTICS.RETURN_REDO`" in text
    assert "`JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDER_LINES`" in text
    assert "cohort maturity rule not yet confirmed" in text
    assert "Redo-to-Shopify join is not built until the Redo table is profiled" in text


def test_report_leads_with_rate_states_and_labels_the_export_honestly(export_queries):
    text = format_returns(returns.run_returns(30))
    rates_at = text.index("*Return and exchange rates*")
    assert rates_at < text.index("*Refund volume*") < text.index("*Refund mix*")
    for label in ("Item return rate", "Item exchange rate", "Exchange share of returned units", "Item refund rate"):
        assert f"• *{label}:* unavailable" in text
    assert "Not available yet:" in text
    assert "Shopify refund export (`EXPORT_CSX__RETURNS`)" in text
    assert "(8.0% of lines)" in text
    assert "share of refund lines, not a rate against orders" in text
    assert "*Internal refund notes*" in text and "not customer reasons" in text
    assert "Refund volume is up" in text
    assert "OS_ALL_ORDERS" not in text
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
    assert "• *Item return rate:* 12.3% (1,234 of 10,032 eligible delivered units) — _provisional: not yet reconciled against live data_" in text
    assert "• *Item exchange rate:* 3.1% (311 of 10,032 eligible delivered units)" in text
    assert "• *Exchange share of returned units:* 25.2% (311 of 1,234 Redo returned units)" in text
    assert "deliveries Aug 21 – Sep 19, 2026 (ET), returns within 30 days of delivery, mature" in text
    assert "13 other or unexplained" in text
    assert "Not available yet" not in text


def test_formatter_without_rates_still_renders_volume():
    text = format_returns({"total_lines": 3, "total_qty_returned": 4})
    assert "*Return and exchange rates*" not in text
    assert "*Refund volume*" in text


def test_help_does_not_promise_a_return_rate():
    from scout.formatting.help import format_help

    line = next(l for l in format_help().splitlines() if "/returns" in l)
    assert "not available yet" in line
