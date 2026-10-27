"""Contract instances from Scout's report outputs.

``contract_payload`` takes the dict ``queries.returns.run_returns`` returns
and the dict ``scout.services.health_service.build_health_report`` returns,
and describes each metric with the fields its ``MetricSpec`` promises. It
never recomputes a metric. An unavailable metric has ``value: null``.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from scout.contract.spec import (
    AVAILABILITY,
    AVAILABLE,
    CONTRACT_VERSION,
    NOT_VALIDATED,
    PROVISIONAL,
    REFUND_SHARE_FLAGS,
    SCALES,
    UNAVAILABLE,
    VALIDATED,
    VALIDATION_RECORDS,
    MetricSpec,
    all_specs,
)
from scout.returns.comments import CommentSynthesis
from scout.returns.metrics import MetricStatus, RatesReport
from scout.returns.period import PeriodReport
from scout.returns.reasons import ReasonSummary
from scout.returns.recommendations import MonitoringView, RankedProduct

_FROM_METRIC_STATUS = {
    MetricStatus.VALIDATED: AVAILABLE,
    MetricStatus.PROVISIONAL: PROVISIONAL,
    MetricStatus.UNAVAILABLE: UNAVAILABLE,
}
_HEALTH_KEYS = {
    "support_csat": "health.support_csat",
    "nps_survey": "health.nps_survey",
    "reviews": "health.okendo_rating",
    "returning_pps": "health.returning_pps_nps",
    "new_pps": "health.new_pps_nps",
    "attribution": "health.attribution",
}
_UNKNOWN_FRESHNESS = {"status": "unknown", "last_synced": None, "note": ""}


def _quantity(spec_q: Any, value: Any) -> Optional[Dict[str, object]]:
    if spec_q is None:
        return None
    return {"value": value, "description": spec_q.description, "source": spec_q.source,
            "source_status": spec_q.source_status, "expression": spec_q.expression}


def instance(
    spec: MetricSpec,
    *,
    availability: str,
    value: Optional[float] = None,
    numerator: Optional[float] = None,
    denominator: Optional[float] = None,
    window: Optional[str] = None,
    reasons: Optional[List[str]] = None,
    freshness: Optional[Mapping[str, Any]] = None,
    filters_applied: Optional[Mapping[str, str]] = None,
    enrichment_coverage: Optional[Mapping[str, Any]] = None,
    evidence: Optional[Mapping[str, Any]] = None,
) -> Dict[str, object]:
    if availability not in AVAILABILITY:
        raise ValueError(f"Unknown availability: {availability}")
    validated = spec.metric_id in VALIDATION_RECORDS and availability == AVAILABLE
    shown = None if availability == UNAVAILABLE else value
    return {
        "metric_id": spec.metric_id,
        "label": spec.label,
        "family": spec.family,
        "value": shown,
        "value_kind": spec.value_kind,
        "scale": {"minimum": SCALES[spec.value_kind].minimum, "maximum": SCALES[spec.value_kind].maximum},
        "numerator": _quantity(spec.numerator, numerator),
        "denominator": _quantity(spec.denominator, denominator),
        "date_basis": spec.date_basis,
        "window": window,
        "maturity": spec.maturity,
        "filters": {"supported": list(spec.filters), "applied": dict(filters_applied or {}), "note": spec.filter_note},
        "enrichment": {"description": spec.enrichment, "coverage": dict(enrichment_coverage) if enrichment_coverage else None},
        "freshness": dict(freshness or _UNKNOWN_FRESHNESS),
        "status": {
            "availability": availability,
            "validation": VALIDATED if validated else NOT_VALIDATED,
            "reasons": [r for r in (reasons or []) if r],
        },
        "evidence": dict(evidence) if evidence else None,
        "in_health_score": spec.in_health_score,
        "health_weight": spec.health_weight,
    }


def _returns_instances(data: Mapping[str, Any], specs: Mapping[str, MetricSpec]) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    product = data.get("product_filter")
    applied = {"product": str(product)} if product else {}
    period = data.get("period_rates")
    if isinstance(period, PeriodReport):
        for rate in period.rates:
            out.append(instance(
                specs[f"returns.period_order_return_rate_{rate.window.label.lower()}"],
                availability=_FROM_METRIC_STATUS[rate.status], value=rate.value_pct,
                numerator=rate.orders_with_return, denominator=rate.orders_placed,
                window=f"{rate.window.label}: {rate.window.describe()}",
                reasons=[rate.reason],
                filters_applied={
                    "replacement_order_types": "excluded",
                    "test_order_tags": "excluded",
                    "voided_and_cancelled": "excluded",
                    "rejected_and_deleted_returns": "excluded",
                },
                evidence={
                    "return_count": rate.return_count,
                    "unmatched_returns": rate.unmatched_returns,
                    "note": period.note,
                    "alternatives": {
                        "orders_with_return_including_rejected": rate.orders_with_return_including_rejected,
                        "return_count_including_rejected": rate.return_count_including_rejected,
                        "unmatched_including_rejected": rate.unmatched_including_rejected,
                        "orders_including_replacement_and_test": rate.orders_including_replacement_and_test,
                        "orders_including_voided_and_cancelled": rate.orders_including_voided_and_cancelled,
                    },
                },
            ))
        out.append(instance(
            specs["returns.exchange_share_of_submitted_returns"],
            availability=UNAVAILABLE, reasons=[period.exchange_share_reason],
        ))

    rates = data.get("rates")
    if isinstance(rates, RatesReport):
        window = rates.window.describe() if rates.window else None
        for r in rates.results:
            out.append(instance(
                specs[f"returns.{r.definition.metric_id}"],
                availability=_FROM_METRIC_STATUS[r.status], value=r.value_pct,
                numerator=r.numerator, denominator=r.denominator, window=window,
                reasons=[r.reason], filters_applied=applied,
                enrichment_coverage={"status": "not_built"},
            ))

    total = int(data.get("total_lines") or 0)
    refund_window = f"{data.get('period_start')} – {data.get('period_end')} (refund date, ET)"
    lines_status = AVAILABLE if total > 0 else UNAVAILABLE
    out.append(instance(specs["returns.refund_lines"], availability=lines_status, value=total,
                        numerator=total, window=refund_window, filters_applied=applied,
                        reasons=[] if total else ["no refund lines in the window"]))
    counts = {"exchange": data.get("exchanges"), "cancellation": data.get("cancellations"),
              "straight": data.get("straight_returns")}
    shares = {"exchange": data.get("exchange_pct"), "cancellation": data.get("cancel_pct"),
              "straight": data.get("straight_pct")}
    for key, _, _ in REFUND_SHARE_FLAGS:
        out.append(instance(specs[f"returns.refund_line_share_{key}"], availability=lines_status,
                            value=shares[key], numerator=counts[key], denominator=total,
                            window=refund_window, filters_applied=applied))

    reasons = data.get("reasons")
    if isinstance(reasons, ReasonSummary):
        c = reasons.coverage
        out.append(instance(
            specs["returns.reason_share"], availability=_FROM_METRIC_STATUS[reasons.status],
            denominator=c.units if c else None, reasons=[reasons.reason], filters_applied=applied,
            evidence={
                "population": reasons.population,
                "rows": [{"reason": r.reason, "units": r.units, "returns": r.returns,
                          "share_of_units_pct": r.share_of_units_pct, "secondary": [list(s) for s in r.secondary]}
                         for r in reasons.reasons],
                "coverage": asdict(c) if c else None,
            },
        ))
    comments = data.get("comments")
    if isinstance(comments, CommentSynthesis):
        out.append(instance(
            specs["returns.comment_themes"], availability=_FROM_METRIC_STATUS[comments.status],
            reasons=[comments.reason], filters_applied=applied,
            evidence={
                "population": comments.population,
                "comments_in_scope": comments.comments_in_scope,
                "comments_analysed": comments.comments_analysed,
                "sample_based": comments.sample_based,
                "themes": [{"label": t.label, "meaning": t.meaning, "returns": t.returns, "comments": t.comments,
                            "single_report": t.single_report, "products": [list(p) for p in t.products],
                            "quote": t.quote, "evidence_item_ids": list(t.evidence_item_ids)}
                           for t in comments.themes],
                "caveats": list(comments.caveats),
            },
        ))
    monitoring = data.get("monitoring")
    if isinstance(monitoring, MonitoringView):
        out.append(instance(
            specs["returns.products_to_watch"], availability=_FROM_METRIC_STATUS[monitoring.status],
            reasons=[monitoring.reason], filters_applied=applied,
            evidence={
                "view": monitoring.label,
                "needs_attention": [_ranked(r) for r in monitoring.needs_attention],
                "below_overall": [_ranked(r) for r in monitoring.performing_well],
                "low_sample": [_ranked(r) for r in monitoring.low_sample],
                "not_ranked": [{"product": c.product, "reason": why} for c, why in monitoring.not_ranked],
                "out_of_focus": [{"product": c.product, "reason": why} for c, why in monitoring.out_of_focus],
                "excluded": [{"product": c.product, "reason": why} for c, why in monitoring.excluded],
            },
        ))
    return out


def _ranked(r: RankedProduct) -> Dict[str, object]:
    return {"product": r.candidate.product, "season": r.candidate.season, "rate_pct": r.rate_pct,
            "returned_units": r.returned_units, "delivered_units": r.delivered_units,
            "excess_units": r.excess_units, "delta_pp": r.delta_pp, "trend_pp": r.trend_pp,
            "status": r.status.value, "low_sample": r.low_sample}


def _health_instances(report: Mapping[str, Any], specs: Mapping[str, MetricSpec],
                      include_returns: bool) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    filters = dict(report.get("filters") or {})
    for entry in report.get("scorecard") or []:
        key = entry.get("key")
        if key == "returns":
            if include_returns:
                out += _returns_from_health(entry, specs)
            continue
        metric_id = _HEALTH_KEYS.get(str(key))
        if metric_id is None:
            continue
        details = entry.get("details") or {}
        numerator = details.get("positive_ratings") if key == "support_csat" else None
        out.append(instance(
            specs[metric_id], availability=str(entry.get("status")), value=entry.get("value"),
            numerator=numerator, denominator=entry.get("sample"), window=entry.get("window"),
            reasons=list(entry.get("reasons") or []), freshness=entry.get("freshness"),
            filters_applied=filters if metric_id == "health.support_csat" else {},
            evidence={"population": entry.get("population"), "definition": entry.get("definition"),
                      "eligible": entry.get("eligible"), "eligible_label": entry.get("eligible_label"),
                      "comparison": entry.get("comparison"), "coverage_note": entry.get("coverage_note"),
                      "details": details},
        ))

    score = report.get("score") or {}
    if score:
        availability = PROVISIONAL if score.get("status") == PROVISIONAL else UNAVAILABLE
        out.append(instance(
            specs["health.customer_health_score"], availability=availability, value=score.get("value"),
            window=report.get("window"), reasons=list(score.get("notes") or []),
            evidence={k: score.get(k) for k in ("version", "coverage", "partial", "range_if_complete",
                                                 "prior_value", "delta", "comparison_note", "components")},
        ))

    themes = report.get("themes") or {}
    if themes:
        availability = AVAILABLE if themes.get("status") == "ok" else UNAVAILABLE
        out.append(instance(
            specs["health.cross_source_themes"], availability=availability, window=report.get("window"),
            reasons=[themes.get("error") or ("" if availability == AVAILABLE else str(themes.get("status")))],
            filters_applied=filters,
            evidence={"themes": themes.get("themes"), "samples": themes.get("samples"),
                      "dropped": themes.get("dropped")},
        ))
    return out


def _returns_from_health(entry: Mapping[str, Any], specs: Mapping[str, MetricSpec]) -> List[Dict[str, object]]:
    out = []
    for r in (entry.get("details") or {}).get("rates") or []:
        status = MetricStatus(str(r.get("status")))
        out.append(instance(
            specs[f"returns.{r.get('metric_id')}"], availability=_FROM_METRIC_STATUS[status],
            value=r.get("value_pct"), numerator=r.get("numerator"), denominator=r.get("denominator"),
            window=entry.get("window"), reasons=[str(r.get("reason") or "")],
            enrichment_coverage={"status": "not_built"},
        ))
    return out


def contract_payload(
    returns_data: Optional[Mapping[str, Any]] = None,
    health_report: Optional[Mapping[str, Any]] = None,
    generated_at: Optional[datetime] = None,
) -> Dict[str, object]:
    """One versioned contract document from whichever reports are given."""
    specs = {s.metric_id: s for s in all_specs()}
    metrics: List[Dict[str, object]] = []
    if returns_data is not None:
        metrics += _returns_instances(returns_data, specs)
    if health_report is not None:
        metrics += _health_instances(health_report, specs, include_returns=returns_data is None)
    moment = generated_at or datetime.now(timezone.utc)
    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": moment.isoformat(),
        "health_score_version": (health_report or {}).get("version"),
        "metrics": metrics,
    }
