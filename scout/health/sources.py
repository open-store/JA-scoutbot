"""
Scorecard entries built from the existing query modules.

Each builder takes a module's own output and describes it; none recomputes
a metric. Survey populations stay separate: the NPS Survey, Returning
Customer PPS, and New Customer PPS each get their own entry, and only the
NPS Survey feeds the health score. Returns come from the returns metric
contract (``queries.returns.run_return_rates``), never from local math.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

from scout.health.components import (
    AVAILABLE,
    FRESH,
    LIVE,
    PROVISIONAL,
    STALE,
    STALE_DATA,
    UNAVAILABLE,
    UNKNOWN,
    Comparison,
    Freshness,
    ScorecardEntry,
    unavailable,
)
from scout.integrations.knocommerce import date_range as knocommerce_date_range
from scout.returns.metrics import ITEM_RETURN_RATE, MetricStatus, RatesReport

RICHPANEL_SOURCE = "Richpanel tickets via Snowflake (FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR)"
KNOCOMMERCE_SOURCE = "KnoCommerce surveys via Snowflake (ANALYTICS.KNOCOMMERCE__NPS___SURVEYS_)"
OKENDO_SOURCE = "Okendo reviews API"
RETURNS_SOURCE = "Redo returns ÷ Shopify delivered units (returns metric contract)"


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


def _int(value: Any) -> int:
    n = _num(value)
    return int(n) if n is not None else 0


def freshness_from(row: Optional[Mapping[str, Any]], stale_after_hours: int,
                   now: Optional[datetime] = None, lookback_days: int = 30) -> Freshness:
    """Freshness from a ``fetch_sync_times`` row. ``None`` means the check itself failed."""
    if row is None:
        return Freshness(UNKNOWN, note="sync time could not be read")
    last = row.get("LAST_SYNCED")
    latest = row.get("LATEST_RECORD")
    latest_dt = latest if isinstance(latest, datetime) else None
    if not isinstance(last, datetime):
        return Freshness(STALE_DATA, latest_record=latest_dt, note=f"no sync in the last {lookback_days} days")
    now = now or datetime.now(timezone.utc)
    last_aware = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
    hours = (now - last_aware).total_seconds() / 3600
    status = STALE_DATA if hours > stale_after_hours else FRESH
    note = f"last synced {hours:.0f}h ago" + (f", over the {stale_after_hours}h limit" if status == STALE_DATA else "")
    return Freshness(status, last_synced=last_aware, latest_record=latest_dt, note=note)


def _status(base: str, freshness: Freshness) -> str:
    return STALE if base == AVAILABLE and freshness.status == STALE_DATA else base


def knocommerce_window(days: int) -> str:
    start, end = knocommerce_date_range(days)
    return f"{start} to {end}, UTC dates, end excluded"


# ---------------------------------------------------------------------------
# Support CSAT (queries/csat.py)
# ---------------------------------------------------------------------------

CSAT_DEFINITION = (
    "Tickets rated 4–5 ÷ tickets with a 1–5 rating × 100. Same as /csat; "
    "not the Richpanel API score in /daily."
)


def support_csat_entry(data: Optional[Mapping[str, Any]], window: str, freshness: Freshness,
                       filters_applied: bool, error: str = "") -> ScorecardEntry:
    key, label = "support_csat", "Support CSAT"
    population = "Support tickets created in the window that have a CSAT rating, all channels"
    if data is None:
        return unavailable(key, label, RICHPANEL_SOURCE, population, CSAT_DEFINITION,
                           error or "CSAT query did not return", window)
    cur = data.get("current") or {}
    prev = data.get("previous") or {}
    rated = _int(cur.get("TOTAL_RATED"))
    total = _int(cur.get("TOTAL_CONVERSATIONS"))
    value = _num(cur.get("CSAT_PCT"))
    if rated == 0 or value is None:
        return unavailable(key, label, RICHPANEL_SOURCE, population, CSAT_DEFINITION,
                           "no rated tickets in the window", window)
    return ScorecardEntry(
        key=key, label=label, source=RICHPANEL_SOURCE, population=population,
        definition=CSAT_DEFINITION, status=_status(AVAILABLE, freshness),
        value=value, unit="%", sample=rated, sample_label="rated tickets",
        eligible=total, eligible_label="tickets", window=window, freshness=freshness,
        comparison=Comparison("previous window of the same length",
                              _num(prev.get("CSAT_PCT")), _int(prev.get("TOTAL_RATED"))),
        coverage_note="All channels and tags; the /voc filters are not applied to CSAT." if filters_applied else "",
        details={
            "positive_ratings": _int(cur.get("POSITIVE_RATINGS")),
            "negative_ratings": _int(cur.get("NEGATIVE_RATINGS")),
            "response_rate_pct": round(rated / total * 100, 1) if total else None,
        },
    )


# ---------------------------------------------------------------------------
# NPS Survey (queries/nps.py)
# ---------------------------------------------------------------------------

NPS_DEFINITION = "% promoters (9–10) − % detractors (0–6) on the recommend question"


def nps_entry(scores: Optional[Mapping[str, Any]], trend: Optional[Mapping[str, Any]], window: str,
              freshness: Freshness, error: str = "") -> ScorecardEntry:
    key, label = "nps_survey", "NPS — NPS Survey"
    population = "NPS Survey respondents (NPS Survey and NPS Survey Copy merged), all customers"
    s = (scores or {}).get("NPS Survey")
    if scores is None:
        return unavailable(key, label, KNOCOMMERCE_SOURCE, population, NPS_DEFINITION,
                           error or "NPS query did not return", window)
    if not s or not s.get("total") or s.get("nps") is None:
        return unavailable(key, label, KNOCOMMERCE_SOURCE, population, NPS_DEFINITION,
                           "no NPS Survey responses in the window", window)
    t = (trend or {}).get("NPS Survey") or {}
    basis = "previous window of the same length"
    if t.get("prior_start"):
        basis += f" ({t['prior_start']} to {t['prior_end']}, UTC)"
    return ScorecardEntry(
        key=key, label=label, source=KNOCOMMERCE_SOURCE, population=population,
        definition=NPS_DEFINITION, status=_status(AVAILABLE, freshness),
        value=_num(s["nps"]), unit="NPS", sample=_int(s["total"]), sample_label="responses",
        window=window, freshness=freshness,
        comparison=Comparison(basis, _num(t.get("prior_nps")), _int(t.get("prior_total")) if t else None),
        coverage_note="Survey covers all customers; product and channel filters do not apply.",
        details={
            "promoter_pct": s.get("promoter_pct"),
            "passive_pct": s.get("passive_pct"),
            "detractor_pct": s.get("detractor_pct"),
        },
    )


# ---------------------------------------------------------------------------
# Returning and new-customer PPS, attribution (queries/pps.py)
# ---------------------------------------------------------------------------

def pps_entry(key: str, label: str, survey: str, data: Optional[Mapping[str, Any]], window: str,
              freshness: Freshness, error: str = "") -> ScorecardEntry:
    population = f"{survey} respondents"
    definition = f"Recommend-question NPS within {survey} only; not combined with other surveys"
    if data is None:
        return unavailable(key, label, KNOCOMMERCE_SOURCE, population, definition,
                           error or f"{survey} query did not return", window)
    breakdown = data.get("nps_breakdown") or {}
    responses = _int(data.get("response_count"))
    if responses == 0:
        return unavailable(key, label, KNOCOMMERCE_SOURCE, population, definition,
                           f"no {survey} responses in the window", window)
    details: Dict[str, object] = {
        "promoter_pct": breakdown.get("promoter_pct"),
        "detractor_pct": breakdown.get("detractor_pct"),
        "open_text_count": data.get("open_text_count", 0),
        "themes": [{"theme": t.get("theme"), "count": t.get("count")} for t in data.get("open_text_themes") or []],
    }
    reasons = data.get("return_reasons") or []
    if reasons:
        details["top_return_reason"] = reasons[0]
    if "almost_stopped_pct" in data:
        details["almost_stopped_pct"] = data.get("almost_stopped_pct")
    return ScorecardEntry(
        key=key, label=label, source=KNOCOMMERCE_SOURCE, population=population,
        definition=definition, status=_status(AVAILABLE, freshness),
        value=_num(breakdown.get("nps")), unit="NPS", sample=_int(breakdown.get("total")),
        sample_label="recommend answers", eligible=responses, eligible_label="responses",
        window=window, freshness=freshness,
        coverage_note="Separate survey population; shown, not scored.", details=details,
    )


def attribution_entry(data: Optional[Mapping[str, Any]], window: str, freshness: Freshness,
                      error: str = "") -> ScorecardEntry:
    key, label = "attribution", "Attribution — New Customer PPS"
    population = "New Customer PPS respondents who answered how they first heard about Jack Archer"
    definition = "Share of respondents per normalised first-heard channel"
    if data is None:
        return unavailable(key, label, KNOCOMMERCE_SOURCE, population, definition,
                           error or "attribution query did not return", window)
    respondents = _int(data.get("response_count"))
    if respondents == 0:
        return unavailable(key, label, KNOCOMMERCE_SOURCE, population, definition,
                           "no attribution answers in the window", window)
    return ScorecardEntry(
        key=key, label=label, source=KNOCOMMERCE_SOURCE, population=population,
        definition=definition, status=_status(AVAILABLE, freshness),
        sample=respondents, sample_label="respondents", window=window, freshness=freshness,
        coverage_note="Same survey as New Customer PPS; acquisition context, not scored.",
        details={"top_channels": list(data.get("how_heard") or [])[:3]},
    )


# ---------------------------------------------------------------------------
# Okendo reviews (queries/reviews.py)
# ---------------------------------------------------------------------------

REVIEWS_DEFINITION = "Mean star rating of approved reviews"


def reviews_entry(data: Optional[Mapping[str, Any]], days: int, error: str = "") -> ScorecardEntry:
    key, label = "reviews", "Okendo reviews"
    population = "Approved Okendo reviews, all products"
    window = f"last {days} days to now, UTC (Okendo API)"
    if data is None:
        return unavailable(key, label, OKENDO_SOURCE, population, REVIEWS_DEFINITION,
                           error or "Okendo request did not return", window)
    total = _int(data.get("total_reviews"))
    value = _num(data.get("avg_rating"))
    if total == 0 or value is None:
        return unavailable(key, label, OKENDO_SOURCE, population, REVIEWS_DEFINITION,
                           "no approved reviews in the window", window)
    reasons: List[str] = []
    if data.get("fetch_capped"):
        reasons.append("Fetch stopped at 2,000 reviews; the average covers the most recent 2,000.")
    return ScorecardEntry(
        key=key, label=label, source=OKENDO_SOURCE, population=population,
        definition=REVIEWS_DEFINITION, status=AVAILABLE, value=value, unit="stars",
        sample=total, sample_label="reviews", window=window,
        freshness=Freshness(LIVE, note="read from the API for this reply"),
        comparison=Comparison("previous window of the same length",
                              _num(data.get("prior_avg_rating")), _int(data.get("prior_total_reviews"))),
        coverage_note="Star rating, not CSAT; reviewers are a different population from ticket raters.",
        reasons=reasons,
        details={"sentiment": dict(data.get("sentiment_breakdown") or {})},
    )


# ---------------------------------------------------------------------------
# Returns (queries/returns.py rate contract)
# ---------------------------------------------------------------------------

_RATE_STATUS = {
    MetricStatus.VALIDATED: AVAILABLE,
    MetricStatus.PROVISIONAL: PROVISIONAL,
    MetricStatus.UNAVAILABLE: UNAVAILABLE,
}


def returns_entry(report: Optional[RatesReport], error: str = "") -> ScorecardEntry:
    key, label = "returns", "Returns — unit cohort item return rate"
    definition = f"{ITEM_RETURN_RATE.label}: Redo returned units ÷ eligible delivered Shopify units × 100"
    if report is None:
        return unavailable(key, label, RETURNS_SOURCE, "Eligible delivered Shopify units", definition,
                           error or "returns rates did not return")
    by_id = {r.definition.metric_id: r for r in report.results}
    rate = by_id.get(ITEM_RETURN_RATE.metric_id)
    status = _RATE_STATUS.get(rate.status, UNAVAILABLE) if rate else UNAVAILABLE
    reasons = list(report.blockers) or ([rate.reason] if rate and rate.reason else [])
    window = report.window.describe() if report.window else "delivery cohort window not set yet"
    return ScorecardEntry(
        key=key, label=label, source=RETURNS_SOURCE,
        population=f"Eligible delivered Shopify units, {report.scope_label}",
        definition=definition, status=status,
        value=rate.value_pct if rate and status != UNAVAILABLE else None, unit="%",
        sample=rate.denominator if rate else None, sample_label="eligible delivered units",
        window=window, freshness=Freshness(UNKNOWN, note="set by the returns contract"),
        coverage_note=("Delivery cohorts, which can be earlier than the ticket and survey windows. "
                       "Exchange metrics are shown, not scored."),
        reasons=reasons,
        details={
            "version": report.version,
            "rates": [
                {
                    "metric_id": r.definition.metric_id,
                    "label": r.definition.label,
                    "status": r.status.value,
                    "value_pct": r.value_pct,
                    "numerator": r.numerator,
                    "denominator": r.denominator,
                    "denominator_label": r.definition.denominator_label,
                    "reason": r.reason,
                }
                for r in report.results
            ],
        },
    )
