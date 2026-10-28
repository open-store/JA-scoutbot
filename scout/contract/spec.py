"""Metric specifications for the CX/VOC data contract (``cx-voc-contract/1``).

Each ``MetricSpec`` states the metric's ID, value kind and scale, numerator
and denominator with their sources, date basis, maturity, supported filters,
enrichment, freshness, evidence, and what blocks it. Specs are built from the
returns metric contract (``scout.returns``) and the customer-health config
(``scout.health.score``), so they cannot drift from the code that computes
the numbers.

Validation is separate from availability. A metric is validated only when
``VALIDATION_RECORDS`` holds a record of a live reconciliation for it. The
registry is empty: nothing in Scout has been reconciled against live data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Optional, Tuple

from scout.health.score import DEFAULT_CONFIG, HEALTH_SCORE_VERSION
from scout.returns import cohorts
from scout.returns.metrics import (
    COHORT_UNITS_DIMENSIONS,
    COHORT_UNITS_MEASURES,
    ITEM_RETURN_RATE,
    METRICS_VERSION,
    RATE_DEFINITIONS,
    source_blockers,
)
from scout.returns.period import (
    ORDER_DATE_COLUMN,
    ORDERS_SOURCE_LABEL,
    OUTCOME_RULE,
    PERIOD_LENGTHS,
    PERIOD_NOTE,
    RETURNS_SOURCE_LABEL,
    SUBMITTED_AT_COLUMN,
)
from scout.returns.sources import (
    OS_ALL_ORDERS,
    POST_PURCHASE_ENRICHMENT,
    REDO_RETURNS,
    REFUND_EXPORT,
    SHOPIFY_ORDER_LINES,
    SHOPIFY_ORDERS,
    SourceRole,
)

CONTRACT_VERSION = "cx-voc-contract/1"

# Value kinds. Percentages are already multiplied by 100: 12.5 means 12.5%.
RATE_PCT = "rate_pct"
SHARE_PCT = "share_pct"
NPS = "nps"
STARS = "stars"
SCORE = "score_0_100"
COUNT = "count"
DISTRIBUTION = "distribution"
THEMES = "themes"
RANKING = "ranking"

# Availability of an instance. Validation is reported separately.
AVAILABLE = "available"
PROVISIONAL = "provisional"
LOW_SAMPLE = "low_sample"
STALE = "stale"
UNAVAILABLE = "unavailable"
AVAILABILITY = (AVAILABLE, PROVISIONAL, LOW_SAMPLE, STALE, UNAVAILABLE)

VALIDATED = "validated"
NOT_VALIDATED = "not_validated"

# Status of the relation or API behind a quantity.
SOURCE_VERIFIED = "verified"
SOURCE_NAMED_UNVERIFIED = "named_unverified"
SOURCE_PRODUCTION = "production_query"
SOURCE_LIVE_API = "live_api"


@dataclass(frozen=True)
class Scale:
    minimum: Optional[float]
    maximum: Optional[float]
    note: str


SCALES: Dict[str, Scale] = {
    RATE_PCT: Scale(0.0, 100.0, "Percent of the denominator, 0 to 100. Computed once from summed counts."),
    SHARE_PCT: Scale(0.0, 100.0, "Percent share of the stated population, 0 to 100."),
    NPS: Scale(-100.0, 100.0, "% promoters minus % detractors."),
    STARS: Scale(1.0, 5.0, "Mean star rating."),
    SCORE: Scale(0.0, 100.0, "Weighted composite of normalized components; versioned and provisional."),
    COUNT: Scale(0.0, None, "Whole count."),
    DISTRIBUTION: Scale(None, None, "No single value; see evidence for the rows."),
    THEMES: Scale(None, None, "No single value; see evidence for themes and counts."),
    RANKING: Scale(None, None, "No single value; see evidence for the ranked products."),
}


@dataclass(frozen=True)
class Quantity:
    description: str
    source: str
    source_status: str
    expression: Optional[str] = None


@dataclass(frozen=True)
class ValidationRecord:
    metric_id: str
    validated_on: str
    method: str
    evidence: str


VALIDATION_RECORDS: Dict[str, ValidationRecord] = {}


@dataclass(frozen=True)
class MetricSpec:
    metric_id: str
    label: str
    family: str
    value_kind: str
    numerator: Optional[Quantity]
    denominator: Optional[Quantity]
    sql: Optional[str]
    date_basis: str
    maturity: str
    filters: Tuple[str, ...]
    filter_note: str
    enrichment: str
    freshness: str
    evidence: str
    producer: str
    in_health_score: bool = False
    health_weight: Optional[float] = None
    blockers: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        out = asdict(self)
        out["filters"] = list(self.filters)
        out["blockers"] = list(self.blockers)
        out["scale"] = asdict(SCALES[self.value_kind])
        out["validation"] = VALIDATED if self.metric_id in VALIDATION_RECORDS else NOT_VALIDATED
        return out


_WEIGHTS = {c.key: c.weight for c in DEFAULT_CONFIG.components}
_TICKET_SOURCE = "FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS"
_SURVEY_SOURCE = "ANALYTICS.KNOCOMMERCE__NPS___SURVEYS_ (RESPONSE, RESPONSE_ANSWER)"
_OKENDO_SOURCE = "Okendo reviews API"
_TICKET_DATES = "Ticket CREATED_AT, complete days in America/New_York"
_SURVEY_DATES = "RESPONSE.CREATED_AT, UTC dates, end excluded"
_FIVETRAN_FRESHNESS = "Latest _FIVETRAN_SYNCED on the source table; stale after 48 hours or no sync in 30 days."


def _rate_blockers() -> Tuple[str, ...]:
    out = list(dict.fromkeys(source_blockers().values()))
    if cohorts.MATURITY_DAYS is None:
        out.append("cohort maturity is not a single day horizon in SHOPIFY_AGG_POST_PURCHASE")
    out.append("cohort-units relation not built; order-line delivery columns are not selectable and item outcome is not one column")
    return tuple(out)


def _redo_quantity(measure: str) -> Quantity:
    return Quantity(COHORT_UNITS_MEASURES[measure], REDO_RETURNS.relation or "Redo", REDO_RETURNS.status.value,
                    f"SUM({measure})")


def _delivered_quantity(measure: str) -> Quantity:
    return Quantity(COHORT_UNITS_MEASURES[measure],
                    f"{SHOPIFY_ORDER_LINES.relation} joined to {SHOPIFY_ORDERS.relation}",
                    SHOPIFY_ORDER_LINES.status.value, f"SUM({measure})")


def _period_rate_specs() -> Tuple[MetricSpec, ...]:
    """Yankho's period rate. A return in the window may be for an earlier order."""
    numerator = Quantity(
        "Distinct orders with a return submitted in the window, excluding rejected and deleted returns",
        RETURNS_SOURCE_LABEL,
        SOURCE_VERIFIED,
        f"COUNT(DISTINCT ORDER_EXTERNAL_ID) where {SUBMITTED_AT_COLUMN} is in the America/New_York window and STATUS is not rejected or deleted",
    )
    denominator = Quantity(
        "Distinct orders placed in the same America/New_York window, excluding replacement types, test tags, voided, and cancelled",
        ORDERS_SOURCE_LABEL,
        OS_ALL_ORDERS.status.value,
        f"COUNT(DISTINCT SHOP_ORDER_ID). Order-date column is {ORDER_DATE_COLUMN}; the filter converts CREATED_AT from UTC to America/New_York",
    )
    specs = []
    for days in PERIOD_LENGTHS:
        specs.append(MetricSpec(
            metric_id=f"returns.period_order_return_rate_l{days}",
            label=f"Period order return rate (L{days})",
            family="returns",
            value_kind=RATE_PCT,
            numerator=numerator,
            denominator=denominator,
            sql="100 * COUNT(DISTINCT orders_with_return) / NULLIF(COUNT(DISTINCT orders_placed), 0)",
            date_basis=(
                f"Return {SUBMITTED_AT_COLUMN} converted to America/New_York; order {ORDER_DATE_COLUMN}. "
                f"{PERIOD_NOTE}"
            ),
            maturity=f"L{days}: {days} complete America/New_York days ending before today. Not a maturity horizon.",
            filters=(
                "exclude_replacement_order_types",
                "exclude_test_order_tags",
                "exclude_voided_and_cancelled",
                "exclude_rejected_and_deleted_returns",
            ),
            filter_note=(
                "Default: OS_ORDER_STATUS not in (redo_exchange, loop_exchange); tag is not exactly "
                "test or test order; not voided; ORDER_CANCELLED_AT is null; return STATUS is not "
                "rejected or deleted. No sales-channel column, so this is all channels. "
                "Comparison figures are not shown in Slack."
            ),
            enrichment="The post-purchase aggregate is not used and does not replace Redo outcomes.",
            freshness="Queried when /returns runs. Not a stored snapshot.",
            evidence=(
                "Raw return count and unmatched returns for the default. Comparison counts: numerator "
                "including rejected and deleted, denominator including replacement and test orders, "
                "and denominator including voided and cancelled orders."
            ),
            producer="queries.returns.run_period_rates via scout.returns.period.period_query",
        ))
        specs.append(MetricSpec(
            metric_id=f"returns.period_exchange_rate_l{days}",
            label=f"Period exchange rate (L{days})",
            family="returns",
            value_kind=RATE_PCT,
            numerator=Quantity(
                "Distinct orders with an exchanged item on a non-rejected, non-deleted return submitted in the window",
                RETURNS_SOURCE_LABEL,
                SOURCE_VERIFIED,
                "COUNT(DISTINCT ORDER_EXTERNAL_ID) where an item has EXCHANGE_ITEM_QUANTITY > 0",
            ),
            denominator=denominator,
            sql="100 * COUNT(DISTINCT orders_with_exchange) / NULLIF(COUNT(DISTINCT orders_placed), 0)",
            date_basis=(
                f"Same America/New_York window as the period order return rate. {OUTCOME_RULE}"
            ),
            maturity=f"L{days}: {days} complete America/New_York days ending before today. Not a maturity horizon.",
            filters=(
                "exclude_replacement_order_types",
                "exclude_test_order_tags",
                "exclude_voided_and_cancelled",
                "exclude_rejected_and_deleted_returns",
            ),
            filter_note="Same default as returns.period_order_return_rate. Not a delivered-unit rate.",
            enrichment="The post-purchase aggregate is not used and does not replace Redo outcomes.",
            freshness="Queried when /returns runs. Not a stored snapshot.",
            evidence="Outcome mix of returned items and the redo_exchange order cross-check for the same window.",
            producer="queries.returns.run_period_rates via scout.returns.period.period_query",
        ))
        specs.append(MetricSpec(
            metric_id=f"returns.exchange_share_of_submitted_returns_l{days}",
            label=f"Exchange share of submitted returns (L{days})",
            family="returns",
            value_kind=RATE_PCT,
            numerator=Quantity(
                "Returns submitted in the window with at least one exchanged item",
                RETURNS_SOURCE_LABEL,
                SOURCE_VERIFIED,
                "COUNT(DISTINCT RETURN.ID) where an item has EXCHANGE_ITEM_QUANTITY > 0",
            ),
            denominator=Quantity(
                "Returns submitted in the window, excluding rejected and deleted",
                RETURNS_SOURCE_LABEL,
                SOURCE_VERIFIED,
                "COUNT(*) of default submitted returns",
            ),
            sql="100 * COUNT(DISTINCT returns_with_exchange) / NULLIF(COUNT(submitted_returns), 0)",
            date_basis=f"Return {SUBMITTED_AT_COLUMN}, America/New_York. {OUTCOME_RULE}",
            maturity=f"L{days}: same complete-day window as the period order return rate.",
            filters=("exclude_rejected_and_deleted_returns",),
            filter_note=(
                "Replaces the unavailable returns.exchange_share_of_submitted_returns. "
                "Evidence also has exchanged items ÷ returned items."
            ),
            enrichment="Not used.",
            freshness="Queried when /returns runs.",
            evidence="Item exchange share and outcome mix: exchange, refund, store credit, none.",
            producer="queries.returns.run_period_rates",
        ))
    return tuple(specs)


def _returns_rate_specs() -> Tuple[MetricSpec, ...]:
    maturity_days = cohorts.MATURITY_DAYS
    horizon = f"{maturity_days} days" if maturity_days else "unset; IS_COHORT_MATURE is not a single day horizon"
    specs = []
    for d in RATE_DEFINITIONS:
        denominator = (_delivered_quantity(d.denominator) if d.denominator_role is SourceRole.ORDER_DENOMINATOR
                       else _redo_quantity(d.denominator))
        scored = d is ITEM_RETURN_RATE
        specs.append(MetricSpec(
            metric_id=f"returns.{d.metric_id}",
            label=d.label,
            family="returns",
            value_kind=RATE_PCT,
            numerator=_redo_quantity(d.numerator),
            denominator=denominator,
            sql=d.sql,
            date_basis="delivery_cohort_date of the Shopify order line, America/New_York",
            maturity=f"Mature cohorts only (is_cohort_mature = TRUE); Redo units counted within the horizon of delivery. Horizon: {horizon}.",
            filters=tuple(COHORT_UNITS_DIMENSIONS),
            filter_note=("Default scope sales_channel = online_store within sales_channel_group = dtc. "
                         "Season, lifecycle, and family come from enrichment by SKU."),
            enrichment=(f"Product context from {POST_PURCHASE_ENRICHMENT.relation} by SKU, only where a SKU maps "
                        "to one value; coverage reported as matched, ambiguous, and unmatched units, and unmatched "
                        "units stay in every total. Not built yet."),
            freshness="Latest LAST_ALTERED of the Redo and order-line relations; wired once they are verified.",
            evidence="Redo reasons and comment themes for the same population (returns.reason_share, returns.comment_themes).",
            producer="queries.returns.run_return_rates via scout.returns.metrics.rollup_query",
            in_health_score=scored,
            health_weight=_WEIGHTS.get("returns") if scored else None,
            blockers=_rate_blockers(),
        ))
    return tuple(specs)


REFUND_SHARE_FLAGS = (
    ("exchange", "Exchange share of refund lines", "NOT IS_CANCELLATION AND IS_EXCHANGE"),
    ("cancellation", "Cancellation share of refund lines", "IS_CANCELLATION"),
    ("straight", "Straight-refund share of refund lines", "NOT IS_CANCELLATION AND NOT IS_EXCHANGE"),
)


def _export_spec(metric_id: str, label: str, value_kind: str, numerator: Quantity,
                 denominator: Optional[Quantity], sql: str) -> MetricSpec:
    return MetricSpec(
        metric_id=metric_id, label=label, family="returns", value_kind=value_kind,
        numerator=numerator, denominator=denominator, sql=sql,
        date_basis="REFUND_DATE_ET (refund processed date, ET); window ends at Snowflake CURRENT_DATE()",
        maturity="Not cohort-based; refunds processed in the window.",
        filters=("product_title_contains",),
        filter_note="product: matches PRODUCT_TITLE as a substring.",
        enrichment="None.",
        freshness="Not read; LAST_ALTERED of the export is not queried.",
        evidence="Top refunded products and categories; internal REFUND_NOTE text, which is not a customer reason.",
        producer="queries.returns.run_returns",
    )


def _refund_export_specs() -> Tuple[MetricSpec, ...]:
    export = REFUND_EXPORT.relation or ""
    lines = Quantity("Refund-export lines in the window", export, SOURCE_VERIFIED, "COUNT(*)")
    specs = [_export_spec("returns.refund_lines", "Refund lines (Shopify refund export)", COUNT,
                          lines, None, "COUNT(*)")]
    for key, label, flag in REFUND_SHARE_FLAGS:
        numerator = Quantity(f"Refund lines where {flag}", export, SOURCE_VERIFIED,
                             f"SUM(CASE WHEN {flag} THEN 1 ELSE 0 END)")
        specs.append(_export_spec(f"returns.refund_line_share_{key}", label, SHARE_PCT, numerator, lines,
                                  f"100 * SUM(CASE WHEN {flag} THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0)"))
    return tuple(specs)


_REDO_FIELD_BLOCKER = "Redo reason and comment query is not built; outcome and settled_at are still unverified"


def _returns_voice_specs() -> Tuple[MetricSpec, ...]:
    redo = REDO_RETURNS.relation or "Redo"
    status = REDO_RETURNS.status.value
    window = "Redo returns requested in the window (RETURN.CREATED_AT)"
    return (
        MetricSpec(
            metric_id="returns.reason_share", label="Return reasons (share of returned units)",
            family="returns", value_kind=DISTRIBUTION,
            numerator=Quantity("Redo returned units with this primary reason", redo, status),
            denominator=Quantity("All Redo returned units in the population, including no reason", redo, status),
            sql=None, date_basis=window, maturity="Not cohort-based.",
            filters=("product", "sku"), filter_note="Product scope resolves through the catalog.",
            enrichment="Product context by SKU, as for the rates.",
            freshness="Same as the Redo source.",
            evidence="Reason rows (units, distinct returns, share, secondary reasons) and coverage: units with a "
                     "reason, a comment, or neither, and duplicate item rows dropped.",
            producer="scout.returns.reasons.summarize_reasons",
            blockers=(_REDO_FIELD_BLOCKER,),
        ),
        MetricSpec(
            metric_id="returns.comment_themes", label="What customers say in return comments",
            family="returns", value_kind=THEMES,
            numerator=None, denominator=None, sql=None, date_basis=window, maturity="Not cohort-based.",
            filters=("product", "sku"), filter_note="Product scope resolves through the catalog.",
            enrichment="Affected products from each cited item.",
            freshness="Same as the Redo source.",
            evidence="Per theme: meaning, distinct returns and comments computed from citations, affected "
                     "products, a verbatim quote with personal details scrubbed, and cited item IDs. Analysed "
                     "and in-scope comment counts; sample-based when capped.",
            producer="scout.returns.comments.synthesize_return_comments",
            blockers=(_REDO_FIELD_BLOCKER,),
        ),
        MetricSpec(
            metric_id="returns.products_to_watch", label="Products to watch (monitoring view)",
            family="returns", value_kind=RANKING,
            numerator=Quantity("Product returned units minus overall rate × product delivered units",
                               redo, status, "redo_returned_units - overall_pct / 100 * eligible_delivered_units"),
            denominator=None, sql=None,
            date_basis="Same delivery cohorts as returns.item_return_rate",
            maturity="Same as returns.item_return_rate.",
            filters=("season", "lifecycle", "sku"),
            filter_note=("Known seasons other than Prior. Jetsetter Tech Pants excluded by catalog style "
                         "(scout.returns.recommendations.RECOMMENDATION_EXCLUSIONS). Overall rates include every product."),
            enrichment="Season and lifecycle from enrichment; catalog resolution for exclusions.",
            freshness="Same as returns.item_return_rate.",
            evidence="Needs attention, below overall, and low sample (under 100 delivered units) lists; "
                     "not-ranked, out-of-focus, and excluded products with reasons.",
            producer="scout.returns.recommendations.build_monitoring_view",
            blockers=("needs available item return rates by product",) + _rate_blockers(),
        ),
    )


def _health_specs() -> Tuple[MetricSpec, ...]:
    return (
        MetricSpec(
            metric_id="health.support_csat", label="Support CSAT", family="customer_health", value_kind=RATE_PCT,
            numerator=Quantity("Tickets rated 4 or 5", _TICKET_SOURCE, SOURCE_PRODUCTION, "SATISFACTION_RATING IN ('4','5')"),
            denominator=Quantity("Tickets with a 1–5 rating", _TICKET_SOURCE, SOURCE_PRODUCTION, "SATISFACTION_RATING IS NOT NULL"),
            sql=None, date_basis=_TICKET_DATES, maturity="Not cohort-based.",
            filters=(), filter_note="All channels; /voc filters are not applied to CSAT. Not the /daily Richpanel API score.",
            enrichment="None.", freshness=_FIVETRAN_FRESHNESS,
            evidence="Rated and total tickets, response rate, previous window of the same length.",
            producer="queries.csat.run_csat via scout.health.sources.support_csat_entry",
            in_health_score=True, health_weight=_WEIGHTS.get("support_csat"),
        ),
        MetricSpec(
            metric_id="health.nps_survey", label="NPS — NPS Survey", family="customer_health", value_kind=NPS,
            numerator=Quantity("Promoters (9–10) minus detractors (0–6)", _SURVEY_SOURCE, SOURCE_PRODUCTION),
            denominator=Quantity("Responses to the recommend question, NPS Survey and NPS Survey Copy merged",
                                 _SURVEY_SOURCE, SOURCE_PRODUCTION),
            sql=None, date_basis=_SURVEY_DATES, maturity="Not cohort-based.",
            filters=(), filter_note="All customers; product and channel filters do not apply.",
            enrichment="None.", freshness=_FIVETRAN_FRESHNESS,
            evidence="Promoter, passive, and detractor shares; previous window.",
            producer="queries.nps.get_nps_scores via scout.health.sources.nps_entry",
            in_health_score=True, health_weight=_WEIGHTS.get("nps_survey"),
        ),
        MetricSpec(
            metric_id="health.okendo_rating", label="Okendo review rating", family="customer_health", value_kind=STARS,
            numerator=Quantity("Sum of star ratings", _OKENDO_SOURCE, SOURCE_LIVE_API),
            denominator=Quantity("Approved reviews, most recent 2,000 at most", _OKENDO_SOURCE, SOURCE_LIVE_API),
            sql=None, date_basis="Review date, last N days to now, UTC", maturity="Not cohort-based.",
            filters=("product",), filter_note="All products in /voc.",
            enrichment="None.", freshness="Read live from the API for each reply.",
            evidence="Review count, sentiment split, previous window; review text feeds health.cross_source_themes.",
            producer="queries.reviews.run_reviews via scout.health.sources.reviews_entry",
            in_health_score=True, health_weight=_WEIGHTS.get("reviews"),
        ),
        MetricSpec(
            metric_id="health.returning_pps_nps", label="Returning Customer PPS recommend NPS",
            family="customer_health", value_kind=NPS,
            numerator=Quantity("Promoters minus detractors within Returning Customer PPS", _SURVEY_SOURCE, SOURCE_PRODUCTION),
            denominator=Quantity("Recommend answers in Returning Customer PPS", _SURVEY_SOURCE, SOURCE_PRODUCTION),
            sql=None, date_basis=_SURVEY_DATES, maturity="Not cohort-based.",
            filters=(), filter_note="Separate survey population; never averaged with other surveys.",
            enrichment="None.", freshness=_FIVETRAN_FRESHNESS,
            evidence="Top reason for coming back, share who almost stopped, open-text themes.",
            producer="queries.pps.get_returning_pps via scout.health.sources.pps_entry",
        ),
        MetricSpec(
            metric_id="health.new_pps_nps", label="New Customer PPS recommend NPS",
            family="customer_health", value_kind=NPS,
            numerator=Quantity("Promoters minus detractors within New Customer PPS", _SURVEY_SOURCE, SOURCE_PRODUCTION),
            denominator=Quantity("Recommend answers in New Customer PPS", _SURVEY_SOURCE, SOURCE_PRODUCTION),
            sql=None, date_basis=_SURVEY_DATES, maturity="Not cohort-based.",
            filters=(), filter_note="Separate survey population; never averaged with other surveys.",
            enrichment="None.", freshness=_FIVETRAN_FRESHNESS,
            evidence="Open-text themes.",
            producer="queries.pps.get_new_pps via scout.health.sources.pps_entry",
        ),
        MetricSpec(
            metric_id="health.attribution", label="Attribution — New Customer PPS",
            family="customer_health", value_kind=DISTRIBUTION,
            numerator=Quantity("Respondents per first-heard channel", _SURVEY_SOURCE, SOURCE_PRODUCTION),
            denominator=Quantity("New Customer PPS respondents who answered the question", _SURVEY_SOURCE, SOURCE_PRODUCTION),
            sql=None, date_basis=_SURVEY_DATES, maturity="Not cohort-based.",
            filters=(), filter_note="Same survey as New Customer PPS; acquisition context.",
            enrichment="None.", freshness=_FIVETRAN_FRESHNESS,
            evidence="Top three channels with counts and shares.",
            producer="queries.pps.get_attribution via scout.health.sources.attribution_entry",
        ),
        MetricSpec(
            metric_id="health.customer_health_score", label=f"Customer health score ({HEALTH_SCORE_VERSION})",
            family="customer_health", value_kind=SCORE,
            numerator=None, denominator=None, sql=None,
            date_basis="Each component's own window", maturity="Returns component uses mature cohorts.",
            filters=(), filter_note="Components cover all customers.",
            enrichment="None.", freshness="Stale or below-minimum components are excluded.",
            evidence=("Component weights, normalization, inclusion and reasons, covered weight, partial flag, the "
                      "range the full score could take, and the previous-window score when comparable."),
            producer="scout.health.score.compute_health_score",
            blockers=("weights and normalization are proposed defaults; no targets or baselines configured",),
        ),
        MetricSpec(
            metric_id="health.cross_source_themes", label="What customers are saying (cross-source themes)",
            family="customer_health", value_kind=THEMES,
            numerator=None, denominator=None, sql=None,
            date_basis="Each source's own window", maturity="Not cohort-based.",
            filters=("channel", "tag", "agent"), filter_note="Filters apply to ticket evidence only.",
            enrichment="Products named in cited items, resolved through the catalog.",
            freshness="Same as each source.",
            evidence=("Per theme and source: distinct tickets, responses, or reviews and share of analysed items, "
                      "never summed across sources; sources count; products; verbatim quote; evidence refs. "
                      "Eligible, retrieved, and analysed counts per source."),
            producer="scout.health.themes.synthesize",
        ),
    )


def all_specs() -> Tuple[MetricSpec, ...]:
    return (
        _period_rate_specs()
        + _returns_rate_specs()
        + _refund_export_specs()
        + _returns_voice_specs()
        + _health_specs()
    )


def spec_document() -> Dict[str, object]:
    """The full contract spec as plain data, for ``docs/contracts`` and Rocky."""
    return {
        "contract_version": CONTRACT_VERSION,
        "returns_metrics_version": METRICS_VERSION,
        "health_score_version": HEALTH_SCORE_VERSION,
        "value_scales": {k: asdict(v) for k, v in SCALES.items()},
        "availability_values": list(AVAILABILITY),
        "validation_values": [VALIDATED, NOT_VALIDATED],
        "validation_rule": "validated only with a ValidationRecord of a live reconciliation; none exist",
        "cohort_units_columns": {
            "dimensions": dict(COHORT_UNITS_DIMENSIONS),
            "measures": dict(COHORT_UNITS_MEASURES),
        },
        "metrics": [s.to_dict() for s in all_specs()],
    }
