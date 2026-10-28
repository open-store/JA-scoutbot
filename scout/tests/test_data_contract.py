"""CX/VOC data contract: specs stay in sync with the code, and instances report status honestly.

Every source is mocked. Nothing here opens a connection or calls a model.
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from queries import errors, returns
from scout.contract import payload as payload_mod
from scout.contract import spec as spec_mod
from scout.contract.__main__ import render_spec
from scout.contract.payload import contract_payload, instance
from scout.contract.spec import (
    CONTRACT_VERSION,
    NOT_VALIDATED,
    RATE_PCT,
    SCALES,
    SHARE_PCT,
    UNAVAILABLE,
    VALIDATED,
    ValidationRecord,
    all_specs,
)
from scout.health.error_rate import standard_rate_windows
from scout.health.score import DEFAULT_CONFIG
from scout.returns.metrics import COHORT_UNITS_MEASURES, RATE_DEFINITIONS
from scout.returns.sources import REDO_RETURNS
from scout.routing.command_parser import parse_command
from scout.services.health_service import build_health_report
from scout.taxonomy.issue_tags import ERROR_RATE_GROUP
from scout.tests.health_fixture import patched_sources, source_outputs
from scout.tests.test_error_rate import fake_error_rate_rows
from scout.tests.test_returns_report import _Conn, fake_period_query

CONTRACT_FILE = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "cx-voc-contract.v1.json"
SPECS = {s.metric_id: s for s in all_specs()}
FIXED_TIME = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def returns_data(monkeypatch):
    monkeypatch.setattr(returns, "get_connection", lambda: _Conn([]))
    monkeypatch.setattr(returns, "execute_query_dict", fake_period_query)
    return returns.run_returns(30)


@pytest.fixture
def error_rates(monkeypatch):
    monkeypatch.setattr(errors, "execute_query_dict", fake_error_rate_rows)
    return errors.run_error_rates(standard_rate_windows(date(2026, 10, 27)))


@pytest.fixture
def health_report():
    with patched_sources(source_outputs()):
        return build_health_report(parse_command("/voc L30"), voc_data={"volume": {"TOTAL_CONVERSATIONS": 4400}})


def _by_id(doc):
    return {m["metric_id"]: m for m in doc["metrics"]}


# ── Spec ─────────────────────────────────────────────────────────

def test_committed_contract_file_matches_the_code():
    assert CONTRACT_FILE.read_text(encoding="utf-8") == render_spec(), (
        "Regenerate with: python -m scout.contract > docs/contracts/cx-voc-contract.v1.json")


def test_metric_ids_are_unique_and_every_kind_has_a_scale():
    ids = [s.metric_id for s in all_specs()]
    assert len(ids) == len(set(ids)) == 31
    assert all(s.value_kind in SCALES for s in all_specs())
    assert {s.family for s in all_specs()} == {"returns", "customer_health"}


def test_returns_rate_specs_mirror_the_returns_contract():
    for d in RATE_DEFINITIONS:
        s = SPECS[f"returns.{d.metric_id}"]
        assert s.sql == d.sql and s.value_kind == RATE_PCT
        assert s.numerator is not None and s.numerator.expression == f"SUM({d.numerator})"
        assert s.numerator.source == REDO_RETURNS.relation and s.numerator.source_status == REDO_RETURNS.status.value
        assert s.denominator is not None and s.denominator.description == COHORT_UNITS_MEASURES[d.denominator]
    share = SPECS["returns.exchange_share_of_returned_units"]
    assert share.denominator is not None and share.denominator.source == REDO_RETURNS.relation
    rate = SPECS["returns.item_exchange_rate"]
    assert rate.denominator is not None and "FCT_SHOPIFY__ORDER_LINES" in rate.denominator.source
    assert any("not a single day horizon" in b for b in SPECS["returns.item_return_rate"].blockers)
    assert any("not built" in b for b in SPECS["returns.item_return_rate"].blockers)


def test_health_weights_come_from_the_score_config():
    weights = {c.key: c.weight for c in DEFAULT_CONFIG.components}
    assert SPECS["health.nps_survey"].health_weight == weights["nps_survey"]
    assert SPECS["health.support_csat"].health_weight == weights["support_csat"]
    assert SPECS["health.okendo_rating"].health_weight == weights["reviews"]
    assert SPECS["returns.item_return_rate"].health_weight == weights["returns"]
    scored = [s.health_weight or 0 for s in all_specs() if s.in_health_score]
    assert sum(scored) == pytest.approx(1.0)
    for unscored in ("health.returning_pps_nps", "health.new_pps_nps", "returns.item_exchange_rate"):
        assert not SPECS[unscored].in_health_score


def test_nothing_is_validated():
    assert spec_mod.VALIDATION_RECORDS == {}
    doc = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))
    assert doc["contract_version"] == CONTRACT_VERSION
    assert {m["validation"] for m in doc["metrics"]} == {NOT_VALIDATED}


# ── Instances ────────────────────────────────────────────────────

def test_payload_covers_returns_and_health_without_validating_anything(returns_data, health_report, error_rates):
    doc = contract_payload(returns_data, health_report, generated_at=FIXED_TIME, error_rates=error_rates)
    assert doc["contract_version"] == CONTRACT_VERSION and doc["generated_at"] == FIXED_TIME.isoformat()
    by = _by_id(doc)
    assert set(by) == set(SPECS)
    assert len(doc["metrics"]) == len(SPECS)
    assert {m["status"]["validation"] for m in doc["metrics"]} == {NOT_VALIDATED}
    json.dumps(doc)


def test_support_error_rate_spec_names_its_group_and_tags():
    for days in (7, 30, 180):
        s = SPECS[f"health.support_error_rate_l{days}"]
        assert s.value_kind == RATE_PCT and s.family == "customer_health" and not s.in_health_score
        assert ERROR_RATE_GROUP in s.label and f"(L{days})" in s.label
        assert s.numerator is not None and "storefront-experience" in (s.numerator.expression or "")
        assert s.denominator is not None and s.denominator.source.endswith("RICHPANEL_CONNECTOR.CONVERSATIONS")
        assert "America/New_York" in s.date_basis and "ERROR_RATE_GROUP" in s.filter_note


def test_support_error_rate_instances_are_provisional_with_counts(error_rates):
    by = _by_id(contract_payload(error_rates=error_rates, generated_at=FIXED_TIME))
    assert set(by) == {f"health.support_error_rate_l{d}" for d in (7, 30, 180)}
    l30 = by["health.support_error_rate_l30"]
    assert l30["status"]["availability"] == "provisional" and l30["status"]["validation"] == NOT_VALIDATED
    assert (l30["numerator"]["value"], l30["denominator"]["value"]) == (21, 3556)
    assert l30["value"] == pytest.approx(100 * 21 / 3556)
    assert l30["window"] == "L30: Sep 27 – Oct 26, 2026 (ET)"
    assert l30["filters"]["applied"] == {"error_group": ERROR_RATE_GROUP}
    assert l30["evidence"]["tags_matched"] == ["storefront-experience", "web_issues"]
    assert "Tech Issue - Website Error" in l30["evidence"]["tags_unresolved"]


def test_payload_rejects_an_error_rate_for_another_group(monkeypatch):
    monkeypatch.setattr(errors, "execute_query_dict", fake_error_rate_rows)
    other = errors.run_error_rates(standard_rate_windows(date(2026, 10, 27)), group="Fulfillment")
    with pytest.raises(ValueError):
        contract_payload(error_rates=other, generated_at=FIXED_TIME)


def test_period_rate_instances_keep_counts_and_leave_exchange_share_unavailable(returns_data):
    by = _by_id(contract_payload(returns_data, generated_at=FIXED_TIME))
    l30 = by["returns.period_order_return_rate_l30"]
    assert l30["status"]["availability"] == "provisional"
    assert l30["value"] == pytest.approx(10.0)
    assert (l30["numerator"]["value"], l30["denominator"]["value"]) == (40, 400)
    assert l30["evidence"]["return_count"] == 45
    assert l30["evidence"]["unmatched_returns"] == 2
    assert "Not a cohort rate" in l30["date_basis"]
    share = by["returns.exchange_share_of_submitted_returns_l30"]
    assert share["status"]["availability"] == "provisional"
    assert share["value"] == pytest.approx(20.0)
    assert (share["numerator"]["value"], share["denominator"]["value"]) == (9, 45)
    assert share["evidence"]["item_exchange_share_pct"] == pytest.approx(20.0)
    assert share["evidence"]["outcome_mix"] == {
        "exchange": 10, "refund": 20, "store_credit": 15, "none": 5,
    }
    exchange = by["returns.period_exchange_rate_l30"]
    assert exchange["status"]["availability"] == "provisional"
    assert exchange["value"] == pytest.approx(2.0)
    assert exchange["evidence"]["redo_exchange_orders_placed"] == 12
    assert exchange["evidence"]["exchange_order_ids_placed_in_window"] == 4
    for days in (7, 30, 180):
        assert f"returns.period_order_return_rate_l{days}" in by
        assert f"returns.period_exchange_rate_l{days}" in by
        assert f"returns.exchange_share_of_submitted_returns_l{days}" in by
    assert "returns.exchange_share_of_submitted_returns" not in by


def test_returns_rates_are_unavailable_with_null_values_and_reasons(returns_data):
    by = _by_id(contract_payload(returns_data, generated_at=FIXED_TIME))
    for d in RATE_DEFINITIONS:
        m = by[f"returns.{d.metric_id}"]
        assert m["status"]["availability"] == UNAVAILABLE and m["value"] is None
        assert any("FCT_SHOPIFY__ORDER_LINES" in r for r in m["status"]["reasons"])
        assert any("not a single day horizon" in r for r in m["status"]["reasons"])
        assert m["enrichment"]["coverage"] == {"status": "not_built"}
        assert m["denominator"]["value"] is None
    for key in ("returns.reason_share", "returns.comment_themes", "returns.products_to_watch"):
        assert by[key]["status"]["availability"] == UNAVAILABLE


def test_refund_export_instances_keep_lines_and_shares_apart(returns_data):
    by = _by_id(contract_payload(returns_data, generated_at=FIXED_TIME))
    lines = by["returns.refund_lines"]
    assert (lines["value"], lines["value_kind"], lines["status"]["availability"]) == (1000, "count", "available")
    exchange = by["returns.refund_line_share_exchange"]
    assert (exchange["value"], exchange["numerator"]["value"], exchange["denominator"]["value"]) == (8.0, 80, 1000)
    assert "refund date, ET" in exchange["window"]
    assert "CURRENT_DATE(), the UTC date" in exchange["date_basis"]


def test_health_instances_carry_sample_freshness_and_comparison(health_report):
    by = _by_id(contract_payload(health_report=health_report, generated_at=FIXED_TIME))
    csat = by["health.support_csat"]
    assert (csat["value"], csat["numerator"]["value"], csat["denominator"]["value"]) == (91.3, 376, 412)
    assert csat["freshness"]["status"] == "fresh"
    assert csat["evidence"]["comparison"]["prior_value"] == 90.1
    assert csat["window"].endswith("complete UTC days")
    assert csat["date_basis"].startswith("Ticket CREATED_AT, complete UTC days")
    nps = by["health.nps_survey"]
    assert nps["value"] == 51.6 and nps["value_kind"] == "nps" and nps["denominator"]["value"] == 310
    score = by["health.customer_health_score"]
    assert score["status"]["availability"] == "provisional" and score["evidence"]["partial"] is True
    themes = by["health.cross_source_themes"]
    assert themes["evidence"]["themes"][0]["counts"] == {"tickets": 2, "nps": 1, "reviews": 1}
    assert by["returns.item_return_rate"]["status"]["availability"] == UNAVAILABLE


def test_percent_values_stay_on_the_0_to_100_scale(returns_data, health_report):
    for m in contract_payload(returns_data, health_report, generated_at=FIXED_TIME)["metrics"]:
        if m["value_kind"] in (RATE_PCT, SHARE_PCT) and m["value"] is not None:
            assert 0 <= m["value"] <= 100, m["metric_id"]


def test_validation_needs_a_record_and_an_available_metric(monkeypatch, returns_data, health_report):
    records = {
        "health.support_csat": ValidationRecord("health.support_csat", "2026-11-01", "reconciled", "link"),
        "returns.item_return_rate": ValidationRecord("returns.item_return_rate", "2026-11-01", "reconciled", "link"),
    }
    monkeypatch.setattr(payload_mod, "VALIDATION_RECORDS", records)
    by = _by_id(contract_payload(returns_data, health_report, generated_at=FIXED_TIME))
    assert by["health.support_csat"]["status"]["validation"] == VALIDATED
    assert by["returns.item_return_rate"]["status"]["validation"] == NOT_VALIDATED
    assert by["health.nps_survey"]["status"]["validation"] == NOT_VALIDATED


def test_instance_hides_values_when_unavailable_and_rejects_unknown_states():
    spec = SPECS["health.support_csat"]
    assert instance(spec, availability=UNAVAILABLE, value=91.3)["value"] is None
    with pytest.raises(ValueError):
        instance(spec, availability="validated")
