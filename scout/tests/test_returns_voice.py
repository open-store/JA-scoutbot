"""Phase 2 returns: Redo field map, reason coverage, comment themes, and the monitoring view.

Fixture data only. Snowflake and OpenAI are never called.
"""

from dataclasses import replace

import pytest

from queries import returns
from scout.formatting import format_returns
from scout.formatting.returns import (
    format_comment_synthesis,
    format_monitoring_view,
    format_reason_summary,
)
from scout.returns import comments as comments_mod
from scout.returns.comments import synthesize_return_comments, unavailable_comment_synthesis
from scout.returns.metrics import ITEM_RETURN_RATE, MetricStatus, RateResult
from scout.returns.reasons import NO_REASON, ReturnItem, summarize_reasons, unavailable_reason_summary
from scout.returns.recommendations import (
    ExclusionRule,
    ProductCandidate,
    build_monitoring_view,
    resolve_exclusions,
)
from scout.returns.redo_fields import REDO_FIELDS, missing_fields
from scout.taxonomy.products import FAMILY, PHRASE, STYLE, alias_only_catalog
from scout.tests.catalog_fixture import fixture_catalog
from scout.tests.test_returns_report import _Conn

POP = "Redo returned units, fixture"


@pytest.fixture
def export_queries(monkeypatch):
    log: list[str] = []
    monkeypatch.setattr(returns, "get_connection", lambda: _Conn(log))

    def _no_queries(*args, **kwargs):
        raise AssertionError("no rate, reason, or comment query may run while Redo is unverified")

    monkeypatch.setattr(returns, "execute_query_dict", _no_queries)
    return log


def _verified_fields():
    return {k: replace(v, column=k.upper(), verified=True) for k, v in REDO_FIELDS.items()}


# ── Redo field map ───────────────────────────────────────────────

def test_every_redo_field_is_unverified_with_no_guessed_column():
    for field in REDO_FIELDS.values():
        assert field.verified is False
        assert field.column is None
        assert not field.usable


def test_missing_fields_checks_the_map():
    assert missing_fields(["primary_reason"]) == ["primary_reason"]
    assert missing_fields(["primary_reason", "customer_comment"], _verified_fields()) == []
    with pytest.raises(KeyError):
        missing_fields(["return_reason_code"])


def test_reason_and_comment_paths_are_unavailable_until_fields_are_verified():
    reasons = unavailable_reason_summary(POP)
    assert reasons is not None and reasons.status is MetricStatus.UNAVAILABLE
    assert "`ANALYTICS.RETURN_REDO`" in reasons.reason and "primary_reason" in reasons.reason
    comment = unavailable_comment_synthesis(POP)
    assert comment is not None and "customer_comment" in comment.reason
    assert unavailable_reason_summary(POP, _verified_fields()) is None
    assert unavailable_comment_synthesis(POP, _verified_fields()) is None


# ── Structured reasons ───────────────────────────────────────────

ITEMS = [
    ReturnItem("R1", "I1", 2, "Atlas Short", "AS-32", "Too small", "Waist", "Waist cuts in when I sit"),
    ReturnItem("R1", "I1", 2, "Atlas Short", "AS-32", "Too small", "Waist", "Waist cuts in when I sit"),
    ReturnItem("R2", "I2", 1, "Atlas Short", "AS-34", "Too small", "Length", None),
    ReturnItem("R3", "I3", 1, "Charter Twill Pant", "CT-32", "Not as pictured", None, "Color is much darker"),
    ReturnItem("R4", "I4", 3, "Charter Twill Pant", "CT-34", "  ", None, None),
    ReturnItem("R5", "I5", 1, "Clubhouse Polo", "CP-M", None, None, "Changed my mind"),
]


def test_reason_shares_keep_missing_reasons_in_the_denominator():
    summary = summarize_reasons(ITEMS, POP)
    assert summary.status is MetricStatus.PROVISIONAL
    by = {r.reason: r for r in summary.reasons}
    assert by["Too small"].units == 3 and by["Too small"].returns == 2
    assert by["Too small"].share_of_units_pct == pytest.approx(37.5)
    assert by["Not as pictured"].share_of_units_pct == pytest.approx(12.5)
    assert by[NO_REASON].units == 4 and by[NO_REASON].returns == 2
    assert sum(r.units for r in summary.reasons) == 8
    assert summary.reasons[-1].reason == NO_REASON
    assert by["Too small"].secondary == (("Waist", 2), ("Length", 1))


def test_reason_coverage_counts_and_dropped_duplicates():
    c = summarize_reasons(ITEMS, POP).coverage
    assert c is not None
    assert (c.items, c.units, c.returns) == (5, 8, 5)
    assert (c.units_with_reason, c.units_with_comment, c.units_with_neither) == (4, 4, 3)
    assert (c.returns_with_reason, c.returns_with_comment) == (3, 3)
    assert c.duplicate_items_dropped == 1


def test_empty_population_is_unavailable_not_zero_percent():
    summary = summarize_reasons([], POP)
    assert summary.status is MetricStatus.UNAVAILABLE
    assert summary.reasons == ()


def test_negative_units_are_rejected():
    with pytest.raises(ValueError):
        summarize_reasons([ReturnItem("R1", "I1", -1)], POP)


# ── Comment synthesis ────────────────────────────────────────────

COMMENT_ITEMS = [
    ReturnItem("R1", "I1", 1, "Atlas Short", comment="Waist runs small, email me at sam@example.com"),
    ReturnItem("R1", "I9", 1, "Atlas Short", comment="Waist runs small, email me at sam@example.com"),
    ReturnItem("R2", "I2", 1, "Atlas Short", comment="Waist runs small for my usual size"),
    ReturnItem("R3", "I3", 1, "Charter Twill Pant", comment="Waist runs small"),
    ReturnItem("R4", "I4", 1, "Charter Twill Pant", comment="Color is much darker than the photos"),
    ReturnItem("R5", "I5", 1, "Clubhouse Polo", comment=None),
]


def _fake(themes, sent=None):
    def run(comments, population):
        if sent is not None:
            sent.extend(comments)
        return {"themes": themes}
    return run


def test_counts_come_from_cited_returns_not_the_model():
    sent: list[str] = []
    themes = [
        {"label": "Waist runs small", "meaning": "fit", "evidence": [1, 2, 3], "mention_count": 40,
         "representative_quote": "Waist runs small for my usual size"},
        {"label": "Darker than pictured", "meaning": "expectation_gap", "evidence": [4],
         "representative_quote": "much darker than the photos"},
        {"label": "Invented", "meaning": "quality", "evidence": [99], "representative_quote": "x"},
    ]
    result = synthesize_return_comments(COMMENT_ITEMS, POP, synthesize=_fake(themes, sent))
    assert result.status is MetricStatus.PROVISIONAL
    assert result.comments_in_scope == 4 and result.comments_analysed == 4
    assert result.duplicates_removed == 1
    assert result.themes_dropped == 1
    waist, darker = result.themes
    assert (waist.label, waist.meaning, waist.returns, waist.comments) == ("Waist runs small", "fit", 3, 3)
    assert waist.products == (("Atlas Short", 2), ("Charter Twill Pant", 1))
    assert waist.quote == "Waist runs small for my usual size"
    assert darker.single_report and darker.meaning == "expectation_gap"
    assert any("emerging signals" in c for c in result.caveats)
    assert all("sam@example.com" not in c for c in sent)
    assert any("[email]" in c for c in sent)


def test_same_words_from_different_returns_stay_separate():
    items = [ReturnItem(f"R{i}", f"I{i}", 1, "Atlas Short", comment="Too small") for i in range(3)]
    themes = [{"label": "Too small", "meaning": "fit", "evidence": [1, 2, 3], "representative_quote": "Too small"}]
    result = synthesize_return_comments(items, POP, synthesize=_fake(themes))
    assert result.comments_in_scope == 3
    assert result.themes[0].returns == 3


def test_a_quote_not_in_a_cited_comment_is_dropped():
    themes = [{"label": "Fit", "meaning": "fit", "evidence": [1], "representative_quote": "The fabric pilled after one wash"}]
    result = synthesize_return_comments(COMMENT_ITEMS[2:3], POP, synthesize=_fake(themes))
    assert result.themes[0].quote is None


def test_unknown_meaning_becomes_other():
    themes = [{"label": "Misc", "meaning": "vibes", "evidence": [1], "representative_quote": "Waist runs small"}]
    result = synthesize_return_comments(COMMENT_ITEMS[3:4], POP, synthesize=_fake(themes))
    assert result.themes[0].meaning == "other"


def test_capped_analysis_is_labelled_sample_based():
    themes = [{"label": "Waist", "meaning": "fit", "evidence": [1, 2], "representative_quote": "Waist runs small"}]
    result = synthesize_return_comments(COMMENT_ITEMS, POP, synthesize=_fake(themes), max_comments=2)
    assert result.sample_based
    assert (result.comments_analysed, result.comments_in_scope) == (2, 4)
    assert any("sample-based: 2 of 4" in c for c in result.caveats)


def test_no_comments_gives_no_themes():
    result = synthesize_return_comments([ReturnItem("R1", "I1", 1)], POP, synthesize=_fake([]))
    assert result.themes == () and "No customer comments in scope." in result.caveats


def test_model_failure_becomes_a_caveat(monkeypatch):
    def boom():
        raise RuntimeError("no key")

    monkeypatch.setattr(comments_mod, "get_client", boom)
    result = synthesize_return_comments(COMMENT_ITEMS, POP)
    assert result.themes == ()
    assert any("Comment synthesis unavailable (RuntimeError)" in c for c in result.caveats)


# ── Monitoring view and exclusions ───────────────────────────────

def _rate(num, den, status=MetricStatus.PROVISIONAL):
    if status is MetricStatus.UNAVAILABLE:
        return RateResult(ITEM_RETURN_RATE, status, reason="Redo returns not verified")
    return RateResult(ITEM_RETURN_RATE, status, num, den, num / den * 100, "not yet reconciled")


OVERALL = _rate(1200, 10_000)  # 12.0%


def _candidate(product, season, num, den, **kw):
    rate = kw.pop("rate", None) or _rate(num, den)
    return ProductCandidate(product=product, season=season, item_return_rate=rate, **kw)


def test_jetsetter_tech_pants_resolves_to_one_style():
    (ex,) = resolve_exclusions(fixture_catalog())
    assert (ex.style, ex.match_level, ex.applied_by_name) == ("Jetsetter Tech Pant", STYLE, True)


def test_exclusion_spares_the_rest_of_the_family_and_keeps_overall_metrics():
    candidates = [
        _candidate("Jetsetter Tech Pant", "FA25", 300, 1000),
        _candidate("Jetsetter Travel Pant", "SP26", 200, 1000),
        _candidate("Jetsetter Short", "SU26", 150, 1000),
    ]
    view = build_monitoring_view(candidates, OVERALL, fixture_catalog())
    assert [c.product for c, _ in view.excluded] == ["Jetsetter Tech Pant"]
    assert [r.candidate.product for r in view.needs_attention] == ["Jetsetter Travel Pant", "Jetsetter Short"]
    assert view.overall is OVERALL
    assert "Overall rates include every eligible product" in view.label


def test_family_and_unknown_names_do_not_exclude_by_name():
    rules = (ExclusionRule("Jetsetter"), ExclusionRule("Nonexistent Pant"))
    family, unknown = resolve_exclusions(fixture_catalog(), rules)
    assert family.match_level == FAMILY and not family.applied_by_name
    assert not unknown.applied_by_name
    view = build_monitoring_view([_candidate("Jetsetter Short", "SU26", 150, 1000)], OVERALL, fixture_catalog(), rules)
    assert view.excluded == ()


def test_subclass_rule_needs_family_and_class_and_sku_rule_matches_exactly():
    tech_shorts = _candidate("Jetsetter Short", "SU26", 150, 1000, family="Jetsetter",
                             product_class="Shorts", subclass="Tech", sku="JS-30")
    tech_pant = _candidate("Jetsetter Tech Pant 2", "SP26", 150, 1000, family="Jetsetter",
                           product_class="Pants", subclass="Tech", sku="JT-32")
    loose = (ExclusionRule("Nonexistent Pant", subclass="Tech"),)
    assert build_monitoring_view([tech_shorts, tech_pant], OVERALL, fixture_catalog(), loose).excluded == ()
    keyed = (ExclusionRule("Nonexistent Pant", family="Jetsetter", product_class="Pants", subclass="Tech"),)
    view = build_monitoring_view([tech_shorts, tech_pant], OVERALL, fixture_catalog(), keyed)
    assert [c.product for c, _ in view.excluded] == ["Jetsetter Tech Pant 2"]
    by_sku = (ExclusionRule("Nonexistent Pant", skus=("js-30",)),)
    view = build_monitoring_view([tech_shorts, tech_pant], OVERALL, fixture_catalog(), by_sku)
    assert [c.product for c, _ in view.excluded] == ["Jetsetter Short"]


def test_without_a_catalog_the_exclusion_matches_the_exact_name():
    (ex,) = resolve_exclusions(alias_only_catalog())
    assert ex.match_level == PHRASE and ex.applied_by_name
    view = build_monitoring_view([_candidate("Jetsetter Tech Pants", "FA25", 1, 200)], OVERALL, alias_only_catalog())
    assert len(view.excluded) == 1


def test_ranking_uses_excess_over_the_overall_rate_not_raw_returned_units():
    candidates = [
        _candidate("Atlas Pant", "FA25", 300, 3000),     # most returned units, 10% < 12%
        _candidate("Atlas Short", "SP26", 60, 300),      # 20%, +24 excess
        _candidate("Charter Twill Pant", "SP26", 200, 1000),  # 20%, +80 excess
    ]
    view = build_monitoring_view(candidates, OVERALL, fixture_catalog())
    assert [r.candidate.product for r in view.needs_attention] == ["Charter Twill Pant", "Atlas Short"]
    top = view.needs_attention[0]
    assert top.excess_units == pytest.approx(80.0) and top.delta_pp == pytest.approx(8.0)
    assert [r.candidate.product for r in view.performing_well] == ["Atlas Pant"]
    assert all(r.status is MetricStatus.PROVISIONAL for r in view.needs_attention)
    assert view.status is MetricStatus.PROVISIONAL


def test_unavailable_rates_are_never_ranked():
    blocked = _candidate("Atlas Short", "SP26", 0, 0, rate=_rate(0, 0, MetricStatus.UNAVAILABLE))
    view = build_monitoring_view([blocked], OVERALL, fixture_catalog())
    assert view.needs_attention == () and view.status is MetricStatus.UNAVAILABLE
    assert "item return rate unavailable" in view.not_ranked[0][1]

    no_overall = build_monitoring_view([_candidate("Atlas Short", "SP26", 60, 300)],
                                       _rate(0, 0, MetricStatus.UNAVAILABLE), fixture_catalog())
    assert no_overall.status is MetricStatus.UNAVAILABLE
    assert no_overall.needs_attention == () and len(no_overall.not_ranked) == 1


def test_low_samples_prior_and_unknown_seasons_are_set_apart():
    candidates = [
        _candidate("Atlas Short", "SP26", 9, 30),          # 30%, low sample
        _candidate("Clubhouse Polo", "Prior", 300, 1000),
        _candidate("Anytime Crewneck", None, 300, 1000),
    ]
    view = build_monitoring_view(candidates, OVERALL, fixture_catalog())
    assert view.needs_attention == ()
    assert [r.candidate.product for r in view.low_sample] == ["Atlas Short"] and view.low_sample[0].low_sample
    assert dict((c.product, why) for c, why in view.out_of_focus) == {
        "Clubhouse Polo": "Prior season", "Anytime Crewneck": "season unknown",
    }


def test_trend_only_against_a_comparable_prior():
    comparable = _candidate("Atlas Short", "SP26", 200, 1000, prior_return_rate=_rate(150, 1000), prior_comparable=True)
    not_comparable = _candidate("Charter Twill Pant", "SP26", 200, 1000, prior_return_rate=_rate(150, 1000))
    view = build_monitoring_view([comparable, not_comparable], OVERALL, fixture_catalog())
    trends = {r.candidate.product: r.trend_pp for r in view.needs_attention}
    assert trends["Atlas Short"] == pytest.approx(5.0)
    assert trends["Charter Twill Pant"] is None


# ── Formatting and /returns ──────────────────────────────────────

def test_formatters_show_populations_coverage_and_citations():
    reason_text = "\n".join(format_reason_summary(summarize_reasons(ITEMS, POP)))
    assert "*Return reasons* (share of 8 Redo returned units, fixture) — _provisional_" in reason_text
    assert "• *Too small:* 3 units (37.5%), 2 returns — Waist 2, Length 1" in reason_text
    assert "_Coverage: 4 of 8 units have a reason, 4 have a comment, 3 have neither (5 returns)._" in reason_text

    themes = [{"label": "Waist runs small", "meaning": "fit", "evidence": [1, 2, 3],
               "representative_quote": "Waist runs small for my usual size"}]
    comment_text = "\n".join(format_comment_synthesis(
        synthesize_return_comments(COMMENT_ITEMS, POP, synthesize=_fake(themes))))
    assert "(4 of 4 distinct comments analysed) — _provisional_" in comment_text
    assert "• *Waist runs small* (fit) — 3 returns; Atlas Short (2), Charter Twill Pant (1)" in comment_text
    assert '_"Waist runs small for my usual size"_' in comment_text

    view = build_monitoring_view([_candidate("Charter Twill Pant", "SP26", 200, 1000),
                                  _candidate("Atlas Short", "SP26", 9, 30)], OVERALL, fixture_catalog())
    view_text = "\n".join(format_monitoring_view(view))
    assert "• *Charter Twill Pant* (SP26): 20.0% item return rate, +8.0 pp vs overall, +80 returned units" in view_text
    assert "_Low sample, directional only:_" in view_text
    assert "validated" not in view_text


def test_returns_report_shows_voice_sections_as_unavailable(export_queries):
    data = returns.run_returns(30)
    assert len(export_queries) == 6
    assert data["reasons"].status is MetricStatus.UNAVAILABLE
    assert data["comments"].status is MetricStatus.UNAVAILABLE
    assert data["monitoring"].status is MetricStatus.UNAVAILABLE
    text = format_returns(data)
    voice = text.index("*Customer reasons and products to watch*")
    assert voice < text.index("*Internal refund notes*")
    assert "• *Redo return reasons:* unavailable — Redo fields not yet verified in `ANALYTICS.RETURN_REDO`" in text
    assert "• *Customer return comments:* unavailable" in text
    assert "• *Products to watch:* unavailable" in text
    assert "excluded from recommendations: Jetsetter Tech Pants" in text
    assert "validated" not in text.lower().replace("not yet verified", "")
