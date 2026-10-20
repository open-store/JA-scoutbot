"""Return and exchange metric contract: ratios, scaling, statuses, and windows.

Pure functions only. Nothing here touches Snowflake.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from scout.returns import cohorts
from scout.returns.metrics import (
    COHORT_UNITS_DIMENSIONS,
    COHORT_UNITS_MEASURES,
    EXCHANGE_SHARE_OF_RETURNS,
    ITEM_EXCHANGE_RATE,
    ITEM_RETURN_RATE,
    RATE_DEFINITIONS,
    CohortScope,
    MetricStatus,
    UnitCounts,
    evaluate_rates,
    outcome_breakdown,
    ratio_pct,
    rollup_query,
    source_blockers,
)
from scout.returns.sources import (
    ALL_SOURCES,
    ReturnsSource,
    SourceRole,
    SourceStatus,
)


def _by_id(results):
    return {r.definition.metric_id: r for r in results}


def test_ratio_is_scaled_to_percent_once():
    assert ratio_pct(25, 1000) == pytest.approx(2.5)
    assert ratio_pct(1, 1) == pytest.approx(100.0)
    assert ratio_pct(0, 1000) == 0.0


@pytest.mark.parametrize("numerator, denominator", [(5, 0), (None, 10), (3, None), (0, 0)])
def test_ratio_is_unavailable_without_a_positive_denominator(numerator, denominator):
    assert ratio_pct(numerator, denominator) is None


def test_ratio_rejects_negative_counts():
    with pytest.raises(ValueError):
        ratio_pct(-1, 10)


def test_rates_pool_summed_counts_instead_of_averaging_row_rates():
    rows = [(1, 10), (90, 100)]
    pooled = ratio_pct(sum(r for r, _ in rows), sum(d for _, d in rows))
    averaged = sum(ratio_pct(r, d) or 0 for r, d in rows) / len(rows)
    assert pooled == pytest.approx(82.727, abs=0.001)
    assert averaged == pytest.approx(50.0)


def test_every_rate_is_unit_based_and_names_its_denominator():
    for d in RATE_DEFINITIONS:
        assert d.numerator in COHORT_UNITS_MEASURES and d.numerator.endswith("_units")
        assert d.denominator in COHORT_UNITS_MEASURES and d.denominator.endswith("_units")
        assert d.sql == f"100 * SUM({d.numerator}) / NULLIF(SUM({d.denominator}), 0)"
    assert ITEM_EXCHANGE_RATE.denominator == "eligible_delivered_units"
    assert EXCHANGE_SHARE_OF_RETURNS.denominator == "redo_returned_units"
    assert ITEM_EXCHANGE_RATE.label != EXCHANGE_SHARE_OF_RETURNS.label
    assert "exchange rate" not in EXCHANGE_SHARE_OF_RETURNS.label.lower()


COUNTS = UnitCounts(
    eligible_delivered_units=10_000,
    redo_returned_units=1_200,
    redo_exchanged_units=300,
    redo_refunded_units=700,
    redo_store_credit_units=50,
    redo_open_units=100,
)


def test_mature_unreconciled_counts_are_provisional_not_validated():
    results = _by_id(evaluate_rates(COUNTS, mature=True, reconciled=False))
    assert results["item_return_rate"].value_pct == pytest.approx(12.0)
    assert results["item_exchange_rate"].value_pct == pytest.approx(3.0)
    assert results["exchange_share_of_returned_units"].value_pct == pytest.approx(25.0)
    assert results["item_refund_rate"].value_pct == pytest.approx(7.0)
    for r in results.values():
        assert r.status is MetricStatus.PROVISIONAL
        assert r.reason == "not yet reconciled against live data"


def test_immature_cohort_stays_provisional_even_if_reconciled():
    for r in evaluate_rates(COUNTS, mature=False, reconciled=True):
        assert r.status is MetricStatus.PROVISIONAL
        assert r.reason == "cohort observation still open"


def test_validated_needs_both_mature_and_reconciled():
    for r in evaluate_rates(COUNTS, mature=True, reconciled=True):
        assert r.status is MetricStatus.VALIDATED


def test_blocked_sources_make_rates_unavailable_with_the_reason():
    blocked = {
        SourceRole.AUTHORITATIVE_RETURNS: "Redo returns not verified",
        SourceRole.ORDER_DENOMINATOR: "Shopify order lines not verified",
    }
    results = _by_id(evaluate_rates(COUNTS, blocked=blocked, mature=True, reconciled=True))
    for r in results.values():
        assert r.status is MetricStatus.UNAVAILABLE
        assert r.value_pct is None
    assert results["item_return_rate"].reason == "Redo returns not verified; Shopify order lines not verified"
    assert results["exchange_share_of_returned_units"].reason == "Redo returns not verified"


def test_exchange_share_needs_only_redo():
    blocked = {SourceRole.ORDER_DENOMINATOR: "Shopify order lines not verified"}
    results = _by_id(evaluate_rates(COUNTS, blocked=blocked, mature=True, reconciled=False))
    assert results["item_return_rate"].status is MetricStatus.UNAVAILABLE
    assert results["exchange_share_of_returned_units"].value_pct == pytest.approx(25.0)


def test_zero_denominator_is_unavailable_but_zero_numerator_is_zero_percent():
    none_delivered = UnitCounts(eligible_delivered_units=0, redo_returned_units=0,
                                redo_exchanged_units=0, redo_refunded_units=0)
    for r in evaluate_rates(none_delivered, mature=True, reconciled=False):
        assert r.status is MetricStatus.UNAVAILABLE
        assert r.value_pct is None

    no_returns = UnitCounts(eligible_delivered_units=500, redo_returned_units=0,
                            redo_exchanged_units=0, redo_refunded_units=0)
    results = _by_id(evaluate_rates(no_returns, mature=True, reconciled=False))
    assert results["item_return_rate"].value_pct == 0.0
    assert results["item_return_rate"].status is MetricStatus.PROVISIONAL
    assert results["exchange_share_of_returned_units"].status is MetricStatus.UNAVAILABLE
    assert results["exchange_share_of_returned_units"].reason == "no Redo returned units in scope"


def test_missing_counts_are_unavailable_not_zero():
    results = _by_id(evaluate_rates(UnitCounts(eligible_delivered_units=100), mature=True, reconciled=False))
    assert results["item_return_rate"].status is MetricStatus.UNAVAILABLE
    assert results["item_return_rate"].reason == "count missing"


def test_numerator_above_denominator_flags_a_population_mismatch():
    bad = UnitCounts(eligible_delivered_units=10, redo_returned_units=12,
                     redo_exchanged_units=1, redo_refunded_units=1)
    results = _by_id(evaluate_rates(bad, mature=True, reconciled=False))
    assert results["item_return_rate"].status is MetricStatus.UNAVAILABLE
    assert "populations do not match" in results["item_return_rate"].reason


def test_outcomes_are_not_assumed_to_be_exhaustive():
    o = outcome_breakdown(COUNTS)
    assert o is not None
    assert (o.refunded, o.exchanged, o.store_credit, o.open, o.unexplained) == (700, 300, 50, 100, 50)
    assert o.consistent

    over = outcome_breakdown(UnitCounts(redo_returned_units=10, redo_refunded_units=8, redo_exchanged_units=4,
                                        redo_store_credit_units=0, redo_open_units=0))
    assert over is not None and not over.consistent and over.unexplained == -2

    assert outcome_breakdown(UnitCounts(redo_returned_units=10)) is None


def test_unit_counts_from_row_keeps_missing_measures_missing():
    counts = UnitCounts.from_row({
        "ELIGIBLE_DELIVERED_UNITS": Decimal("10000"),
        "REDO_RETURNED_UNITS": 1200,
        "redo_exchanged_units": None,
    })
    assert counts.eligible_delivered_units == 10_000
    assert counts.redo_returned_units == 1_200
    assert counts.redo_exchanged_units is None
    assert counts.redo_refunded_units is None


def test_source_blockers_reflect_unverified_redo_and_orders():
    blocked = source_blockers()
    assert "ANALYTICS.RETURN_REDO" in blocked[SourceRole.AUTHORITATIVE_RETURNS]
    assert "FCT_SHOPIFY__ORDER_LINES" in blocked[SourceRole.ORDER_DENOMINATOR]

    verified = tuple(
        ReturnsSource(s.role, s.label, s.relation or "DB.SCHEMA.T", SourceStatus.VERIFIED, s.evidence)
        for s in ALL_SOURCES
    )
    assert source_blockers(verified) == {}


WINDOW = cohorts.mature_window(30, 30, date(2026, 10, 20))


def test_mature_window_ends_one_horizon_before_today():
    assert (WINDOW.start, WINDOW.end) == (date(2026, 8, 21), date(2026, 9, 20))
    assert WINDOW.days == 30 and WINDOW.mature and WINDOW.timezone == "America/New_York"
    assert WINDOW.describe() == (
        "deliveries Aug 21 – Sep 19, 2026 (ET), returns within 30 days of delivery, mature"
    )


def test_prior_mature_window_is_comparable_but_provisional_is_not():
    prior = cohorts.prior_window(WINDOW)
    assert (prior.start, prior.end) == (date(2026, 7, 22), date(2026, 8, 21))
    assert cohorts.comparable(WINDOW, prior)

    latest = cohorts.provisional_window(30, 30, date(2026, 10, 20))
    assert (latest.start, latest.end) == (date(2026, 9, 20), date(2026, 10, 20))
    assert not latest.mature
    assert not cohorts.comparable(latest, cohorts.prior_window(latest))
    assert not cohorts.comparable(WINDOW, cohorts.mature_window(30, 45, date(2026, 10, 20)))


def test_windows_reject_non_positive_lengths():
    with pytest.raises(ValueError):
        cohorts.mature_window(0, 30, date(2026, 10, 20))
    with pytest.raises(ValueError):
        cohorts.mature_window(30, 0, date(2026, 10, 20))


def test_today_is_the_new_york_date():
    late_utc = datetime(2026, 10, 21, 2, 30, tzinfo=timezone.utc)
    assert cohorts.today_et(late_utc) == date(2026, 10, 20)


def test_maturity_horizon_is_unknown_until_read_from_the_model():
    assert cohorts.MATURITY_DAYS is None


def test_rollup_query_sums_counts_and_binds_every_value():
    sql, params = rollup_query("COHORT_UNITS", CohortScope(window=WINDOW, skus=("SKU-1", "SKU-2")))
    for d in RATE_DEFINITIONS:
        assert f"{d.sql} AS {d.metric_id}" in sql
    for m in COHORT_UNITS_MEASURES:
        assert f"SUM({m}) AS {m}" in sql
    assert "AVG(" not in sql
    assert "is_cohort_mature = TRUE" in sql
    assert "sku IN (%(sku_0)s, %(sku_1)s)" in sql
    assert params == {
        "start": "2026-08-21", "end": "2026-09-20", "channel_group": "dtc", "channel": "online_store",
        "sku_0": "SKU-1", "sku_1": "SKU-2",
    }
    for value in ("2026-08-21", "online_store", "SKU-1"):
        assert value not in sql
    assert "GROUP BY" not in sql


def test_rollup_query_groups_only_by_contract_dimensions():
    sql, _ = rollup_query("COHORT_UNITS", CohortScope(window=WINDOW), group_by=("customer_trx_status", "sku"))
    assert sql.startswith("SELECT customer_trx_status, sku, SUM(")
    assert sql.endswith("GROUP BY customer_trx_status, sku")
    assert set(COHORT_UNITS_DIMENSIONS) >= {"customer_trx_status", "sku"}
    with pytest.raises(ValueError):
        rollup_query("COHORT_UNITS", CohortScope(window=WINDOW), group_by=("return_status",))


def test_provisional_window_does_not_filter_on_maturity():
    latest = cohorts.provisional_window(30, 30, date(2026, 10, 20))
    sql, _ = rollup_query("COHORT_UNITS", CohortScope(window=latest))
    assert "is_cohort_mature" not in sql
    assert ITEM_RETURN_RATE.metric_id in sql
