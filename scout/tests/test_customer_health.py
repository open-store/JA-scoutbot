"""Customer-health report: scorecard, versioned score, missing-data rules, and cited themes.

Every source is mocked (``health_fixture``). The returns component uses the
real returns metric contract, which reports unavailable until Redo and the
order lines are verified.
"""
import re
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from scout.formatting import format_voc
from scout.health import sources
from scout.health.components import (
    AVAILABLE,
    FRESH,
    PROVISIONAL,
    STALE,
    STALE_DATA,
    UNAVAILABLE,
    UNKNOWN,
    Comparison,
    Freshness,
    ScorecardEntry,
)
from scout.health.score import (
    DEFAULT_CONFIG,
    HEALTH_SCORE_VERSION,
    SCORE_UNAVAILABLE,
    Normalization,
    compute_health_score,
)
from scout.health.themes import EvidenceItem, link_themes
from scout.repositories import health_repository
from scout.returns.metrics import UnitCounts, RatesReport, evaluate_rates
from scout.routing.command_parser import parse_command
from scout.services.health_service import build_health_report
from scout.tests.catalog_fixture import fixture_catalog
from scout.tests.health_fixture import NOW, llm_client, patched_sources, source_outputs

FRESH_SYNC = Freshness(FRESH, last_synced=NOW)


def _entry(key, value, sample, status=AVAILABLE, freshness=FRESH_SYNC, prior=None, prior_n=None):
    return ScorecardEntry(key=key, label=key, source="s", population="p", definition="d", status=status,
                          value=value, sample=sample, freshness=freshness,
                          comparison=Comparison("prev", prior, prior_n) if prior is not None else None)


def _entries(**overrides):
    base = {
        "nps_survey": _entry("nps_survey", 50.0, 300, prior=40.0, prior_n=280),
        "support_csat": _entry("support_csat", 90.0, 400, prior=88.0, prior_n=390),
        "reviews": _entry("reviews", 4.6, 230, prior=4.4, prior_n=200),
        "returns": _entry("returns", 20.0, 10_000),
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


# ---------------------------------------------------------------------------
# Normalization and weights
# ---------------------------------------------------------------------------

def test_normalization_puts_each_scale_on_0_to_100_with_direction():
    rules = {c.key: c.normalization for c in DEFAULT_CONFIG.components}
    assert rules["nps_survey"].apply(-100) == 0 and rules["nps_survey"].apply(100) == 100
    assert rules["nps_survey"].apply(50) == 75
    assert rules["support_csat"].apply(91.3) == pytest.approx(91.3)
    assert rules["reviews"].apply(1) == 0 and rules["reviews"].apply(5) == 100
    assert rules["returns"].apply(0) == 100 and rules["returns"].apply(25) == 75
    assert rules["returns"].direction == "lower is better"
    assert Normalization("x", 0, 10).apply(15) == 100  # clamped


def test_config_is_versioned_and_weights_sum_to_one():
    assert DEFAULT_CONFIG.version == HEALTH_SCORE_VERSION
    assert DEFAULT_CONFIG.provisional is True
    assert sum(c.weight for c in DEFAULT_CONFIG.components) == pytest.approx(1.0)
    keys = {c.key for c in DEFAULT_CONFIG.components}
    assert keys == {"nps_survey", "support_csat", "reviews", "returns"}
    assert not any("exchange" in k or "pps" in k for k in keys)


# ---------------------------------------------------------------------------
# Composite and missing-data rules
# ---------------------------------------------------------------------------

def test_full_coverage_is_a_weighted_mean_of_normalized_components():
    score = compute_health_score(_entries())
    expected = 0.30 * 75 + 0.25 * 90 + 0.20 * 90 + 0.25 * 80
    assert score.value == pytest.approx(round(expected, 1))
    assert score.status == PROVISIONAL and not score.partial
    assert score.range_if_complete is None


def test_missing_returns_is_excluded_not_zeroed_and_reweighting_is_disclosed():
    entries = _entries(returns=_entry("returns", None, None, status=UNAVAILABLE))
    score = compute_health_score(entries)
    covered = 0.30 * 75 + 0.25 * 90 + 0.20 * 90
    assert score.value == pytest.approx(round(covered / 0.75, 1))
    assert score.value != pytest.approx(round(covered, 1))  # not scored as zero
    assert score.partial and score.coverage == pytest.approx(0.75)
    assert score.range_if_complete == (round(covered, 1), round(covered + 25, 1))
    returns = next(c for c in score.components if c.key == "returns")
    assert not returns.included and returns.reason.startswith("unavailable")
    assert any("rescaled" in n and "not scored as zero" in n for n in score.notes)


def test_insufficient_coverage_makes_the_score_unavailable():
    entries = _entries(nps_survey=None, returns=None)
    score = compute_health_score(entries)
    assert score.status == SCORE_UNAVAILABLE and score.value is None
    assert "needs 3 components and 70%" in score.notes[0]


@pytest.mark.parametrize("override, reason", [
    (_entry("support_csat", 90.0, 400, status=STALE, freshness=Freshness(STALE_DATA, note="last synced 80h ago")),
     "stale data"),
    (_entry("support_csat", 90.0, 12), "below minimum sample (12 of 30 rated tickets)"),
    (_entry("support_csat", None, None, status=UNAVAILABLE), "unavailable"),
])
def test_unusable_components_are_excluded_with_a_reason(override, reason):
    score = compute_health_score(_entries(support_csat=override))
    csat = next(c for c in score.components if c.key == "support_csat")
    assert not csat.included and csat.reason.startswith(reason)
    assert score.value is not None and score.partial


def test_provisional_returns_rate_shows_but_does_not_enter_the_score():
    entries = _entries(returns=replace(_entry("returns", 20.0, 10_000), status=PROVISIONAL,
                                       reasons=["not yet reconciled against live data"]))
    returns = next(c for c in compute_health_score(entries).components if c.key == "returns")
    assert not returns.included and "provisional" in returns.reason


def test_trend_only_when_every_included_component_has_a_comparable_prior():
    full = compute_health_score(_entries(returns=None))
    assert full.prior_value is not None and full.delta is not None
    no_prior = compute_health_score(_entries(returns=None, reviews=_entry("reviews", 4.6, 230)))
    assert no_prior.prior_value is None and "Okendo rating" in no_prior.comparison_note
    thin_prior = compute_health_score(_entries(returns=None, nps_survey=_entry("nps_survey", 50.0, 300, prior=40.0,
                                                                               prior_n=10)))
    assert thin_prior.prior_value is None


# ---------------------------------------------------------------------------
# Source adapters
# ---------------------------------------------------------------------------

def test_freshness_rules():
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    assert sources.freshness_from({"LAST_SYNCED": now - timedelta(hours=3)}, 48, now).status == FRESH
    stale = sources.freshness_from({"LAST_SYNCED": now - timedelta(hours=60)}, 48, now)
    assert stale.status == STALE_DATA and "60h" in stale.note
    assert sources.freshness_from({"LAST_SYNCED": None}, 48, now).status == STALE_DATA
    assert sources.freshness_from(None, 48, now).status == UNKNOWN


def test_csat_entry_matches_csat_definition_and_discloses_filters():
    out = source_outputs()
    entry = sources.support_csat_entry(out["csat"], "w", FRESH_SYNC, filters_applied=True)
    assert entry.value == pytest.approx(91.3) and isinstance(entry.value, float)
    assert entry.sample == 412 and entry.eligible == 4400
    assert "not the Richpanel API score in /daily" in entry.definition
    assert "filters are not applied to CSAT" in entry.coverage_note
    assert entry.comparison.prior_value == pytest.approx(90.1)


def test_stale_richpanel_marks_csat_stale():
    out = source_outputs()
    stale = Freshness(STALE_DATA, note="last synced 80h ago")
    assert sources.support_csat_entry(out["csat"], "w", stale, False).status == STALE


def test_survey_populations_stay_separate():
    out = source_outputs()
    nps = sources.nps_entry(out["nps_scores"], out["nps_trend"], "w", FRESH_SYNC)
    rpps = sources.pps_entry("returning_pps", "Returning Customer PPS", "Returning Customer PPS",
                             out["returning_pps"], "w", FRESH_SYNC)
    npps = sources.pps_entry("new_pps", "New Customer PPS", "New Customer PPS", out["new_pps"], "w", FRESH_SYNC)
    assert (nps.value, rpps.value, npps.value) == (51.6, 61.0, 57.9)
    assert nps.sample == 310 and "NPS Survey Copy merged" in nps.population
    assert "not combined with other surveys" in rpps.definition
    assert rpps.details["top_return_reason"]["label"] == "Product quality"


def test_returns_entry_consumes_the_contract():
    from queries.returns import run_return_rates
    pending = sources.returns_entry(run_return_rates(30))
    assert pending.status == UNAVAILABLE and pending.value is None
    assert pending.reasons and "FCT_SHOPIFY__ORDER_LINES" in pending.reasons[0]

    counts = UnitCounts(eligible_delivered_units=10_000, redo_returned_units=2_000, redo_exchanged_units=500,
                        redo_refunded_units=1_300, redo_store_credit_units=100, redo_open_units=100)
    validated = RatesReport(results=evaluate_rates(counts, mature=True, reconciled=True), window=None,
                            scope_label="online store, DTC")
    entry = sources.returns_entry(validated)
    assert entry.status == AVAILABLE and entry.value == pytest.approx(20.0) and entry.sample == 10_000
    rates = {r["metric_id"]: r["value_pct"] for r in entry.details["rates"]}
    assert rates["exchange_share_of_returned_units"] == pytest.approx(25.0)
    unreconciled = RatesReport(results=evaluate_rates(counts, mature=True, reconciled=False), window=None,
                               scope_label="online store, DTC")
    assert sources.returns_entry(unreconciled).status == PROVISIONAL


def test_reviews_cap_is_disclosed():
    data = dict(source_outputs()["reviews"], fetch_capped=True)
    entry = sources.reviews_entry(data, 30)
    assert any("2,000" in r for r in entry.reasons)


# ---------------------------------------------------------------------------
# Cited themes
# ---------------------------------------------------------------------------

def test_theme_counts_are_per_source_and_never_summed():
    items = [
        EvidenceItem("ticket:m1", "tickets", "c1", "Waist runs small on the Atlas Short"),
        EvidenceItem("ticket:m2", "tickets", "c1", "Still small after exchange"),
        EvidenceItem("review:r1", "reviews", "r1", "Waist runs small", rating=2, product="Atlas Short"),
    ]
    themes, dropped = link_themes([
        {"label": "Waist runs small", "kind": "friction", "evidence": [1, 2, 3, 99], "quote": "Waist runs small"},
        {"label": "General inquiry", "kind": "friction", "evidence": [1]},
        {"label": "No refs", "kind": "positive", "evidence": []},
    ], items, {"tickets": 10, "reviews": 4}, fixture_catalog())
    assert dropped == {"generic_label": 1, "no_valid_citations": 1}
    (theme,) = themes
    assert theme.counts == {"tickets": 1, "reviews": 1}  # two messages, one ticket
    assert theme.shares == {"tickets": 10.0, "reviews": 25.0}
    assert theme.products == ("Atlas Short",)
    assert theme.quote_ref == "ticket:m1"


def test_build_report_end_to_end():
    cmd = parse_command("/voc L30")
    with patched_sources(source_outputs()) as llm:
        report = build_health_report(cmd, voc_data={"volume": {"TOTAL_CONVERSATIONS": 4400}})
    assert report["errors"] == {}
    keys = [e["key"] for e in report["scorecard"]]
    assert keys == ["support_csat", "nps_survey", "reviews", "returns", "returning_pps", "new_pps", "attribution"]
    score = report["score"]
    assert score["version"] == HEALTH_SCORE_VERSION and score["partial"] and score["coverage"] == 0.75
    assert score["delta"] is not None
    themes = report["themes"]
    assert [t["label"] for t in themes["themes"]] == [
        "Bottoms run small in the waist", "Fabric quality and durability", "Order tracking delays"]
    waist = themes["themes"][0]
    assert waist["counts"] == {"tickets": 2, "nps": 1, "reviews": 1} and waist["cross_source"]
    assert themes["dropped"] == {"generic_label": 1}
    samples = {s["source"]: s for s in themes["samples"]}
    assert samples["tickets"]["eligible"] == 4400 and samples["tickets"]["analysed"] == 3
    prompt = llm.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "(reviews, 2★, Atlas Short)" in prompt and "(NPS, score 3/10)" in prompt


def test_failed_source_is_unavailable_and_can_make_the_score_unavailable():
    cmd = parse_command("/voc L30")
    with patched_sources(source_outputs(), failures={"nps_scores": RuntimeError("boom"),
                                                     "reviews": ConnectionError("down")}):
        report = build_health_report(cmd)
    entries = {e["key"]: e for e in report["scorecard"]}
    assert entries["nps_survey"]["status"] == UNAVAILABLE
    assert entries["nps_survey"]["reasons"] == ["RuntimeError reading this source"]
    assert report["score"]["status"] == SCORE_UNAVAILABLE and report["score"]["value"] is None
    out = format_voc({"health": report, "days": 30, "timeframe_label": cmd.timeframe_label, "volume": {}})
    assert "*Health score:* unavailable" in out and "The scorecard below is the data." in out


def test_slow_source_times_out_without_failing_the_reply():
    cmd = parse_command("/voc L30")
    out = source_outputs()

    def slow(*args, **kwargs):
        time.sleep(1.0)
        return out["reviews"]

    with patched_sources(out), patch("scout.services.health_service.run_reviews", side_effect=slow):
        report = build_health_report(cmd, timeout=0.3)
    reviews = next(e for e in report["scorecard"] if e["key"] == "reviews")
    assert reviews["status"] == UNAVAILABLE and "timed out" in reviews["reasons"][0]


def test_synthesis_failure_keeps_the_scorecard():
    cmd = parse_command("/voc L30")
    with patched_sources(source_outputs(), llm=llm_client(raises=True)):
        report = build_health_report(cmd)
    assert report["themes"]["status"] == "unavailable"
    assert report["score"]["value"] is not None
    out = format_voc({"health": report, "days": 30, "timeframe_label": "x", "volume": {}})
    assert "Theme synthesis unavailable (RuntimeError)" in out


def test_filters_reach_ticket_evidence_and_are_disclosed_elsewhere():
    cmd = parse_command("/voc L30 channel:email")
    with patched_sources(source_outputs()):
        with patch("scout.services.health_service.fetch_opening_messages", return_value=[]) as fetch:
            report = build_health_report(cmd)
    assert fetch.call_args.args[2] == {"channel": "email"}
    entries = {e["key"]: e for e in report["scorecard"]}
    assert "filters are not applied to CSAT" in entries["support_csat"]["coverage_note"]
    assert "apply to tickets only" in entries["nps_survey"]["coverage_note"]


# ---------------------------------------------------------------------------
# Repository SQL
# ---------------------------------------------------------------------------

def test_opening_messages_sql_is_customer_authored_bounded_and_filtered():
    sql = health_repository.build_opening_messages_sql("2026-09-20 00:00:00", "2026-10-20 00:00:00",
                                                       {"channel": "email", "agent": "abc"}, 120)
    assert "m.AUTHOR_ID = c.CUSTOMER_ID" in sql
    assert "QUALIFY ROW_NUMBER() OVER (PARTITION BY m.CONVERSATION_ID ORDER BY m.CREATED_AT) = 1" in sql
    assert "ORDER BY HASH(m.CONVERSATION_ID)" in sql and "LIMIT 120" in sql
    assert "c.CREATED_AT >= '2026-09-20 00:00:00'" in sql
    assert "LOWER(c.CHANNEL) = 'email'" in sql and "c.ASSIGNEE_ID" in sql
    assert "NOT LIKE '%out of office%'" in sql
    for kw in ("INSERT", "UPDATE", "DELETE", "MERGE"):
        assert not re.search(rf"\b{kw}\b", sql.upper())


def test_sync_sql_is_bounded():
    sql = health_repository.build_sync_sql(health_repository.RICHPANEL_CONVERSATIONS, 30)
    assert "MAX(_FIVETRAN_SYNCED)" in sql and "DATEADD('day', -30" in sql


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

def test_general_voc_reply_is_the_health_report_not_the_generic_tag_summary():
    cmd = parse_command("/voc L30")
    with patched_sources(source_outputs()):
        report = build_health_report(cmd, voc_data={"volume": {"TOTAL_CONVERSATIONS": 4400}})
    data = {
        "health": report, "days": 30, "timeframe_label": cmd.timeframe_label, "filter_label": "",
        "volume": {"TOTAL_CONVERSATIONS": 4400}, "prev_volume": {"TOTAL_CONVERSATIONS": 4180},
        "by_channel": [{"CHANNEL": "email", "CNT": 2640}],
        "tags": [{"TAG_UUID": "x", "TAG_NAME": "product_inquiry", "CNT": 410}], "status": [],
    }
    out = format_voc(data)
    assert "*Health score:* " in out and "provisional, customer-health/0.1" in out
    assert "Unit cohort item return rate (25%) excluded, not scored as zero" in out
    assert "*Support CSAT:* 91.3% · 412 rated of 4,400 tickets" in out
    assert "*NPS — NPS Survey:* +51.6 · 310 responses" in out
    assert "*Returns — unit cohort item return rate:* unavailable" in out
    assert "_not scored_" in out
    assert "1. *Bottoms run small in the waist* — tickets 2 of 3 tickets (67%); NPS 1 of 2 responses (50%)" in out
    assert "_What's working_" in out
    assert "The dominant customer contact theme is" not in out
    assert "never averaged" in out
    assert "tickets use complete UTC days" in out and "complete ET days" not in out
    assert "→ Add waist guidance to the bottoms size chart." in out
