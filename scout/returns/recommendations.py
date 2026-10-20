"""Which products to act on: the monitoring view behind product recommendations.

The default view focuses on products in a known season other than ``Prior``
(the supplied proxy for the previous factory) and leaves out the products in
``RECOMMENDATION_EXCLUSIONS``. It filters recommendations only; official
rates still include every eligible product, and explicit questions about an
excluded or Prior product are still answered.

Ranking never uses an unavailable rate. A product is ranked by its excess
returned units over the official overall rate (returned units minus the
overall rate times its delivered units), which weighs how far its rate sits
above the overall rate by how much it sells, rather than by raw returned
units. Ties go to more recurring customer evidence, then the higher rate.
Products below ``MIN_DELIVERED_UNITS`` are listed separately as low sample.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from scout.returns.metrics import MetricStatus, RateResult
from scout.taxonomy.products import FAMILY, PHRASE, STYLE, ProductCatalog, match_key, resolve_product

PRIOR_SEASON = "prior"
MIN_DELIVERED_UNITS = 100


@dataclass(frozen=True)
class ExclusionRule:
    """A product kept out of default recommendations.

    ``product`` is resolved against the catalog and must land on one style.
    ``skus`` match exactly. ``subclass`` applies only together with
    ``family`` and ``product_class``, because a subclass such as "Tech" can
    span several products.
    """

    product: str
    skus: tuple[str, ...] = ()
    family: str | None = None
    product_class: str | None = None
    subclass: str | None = None
    reason: str = "Excluded from default recommendations."


RECOMMENDATION_EXCLUSIONS: tuple[ExclusionRule, ...] = (
    ExclusionRule(
        product="Jetsetter Tech Pants",
        reason="Excluded from default recommendations by request; still in overall metrics.",
    ),
)


@dataclass(frozen=True)
class ResolvedExclusion:
    rule: ExclusionRule
    style: str | None
    match_level: str | None
    applied_by_name: bool
    note: str


def resolve_exclusions(
    catalog: ProductCatalog,
    rules: Sequence[ExclusionRule] = RECOMMENDATION_EXCLUSIONS,
) -> tuple[ResolvedExclusion, ...]:
    """Resolve each rule's product name to one catalog style, or say why not."""
    out = []
    for rule in rules:
        res = resolve_product(rule.product, catalog)
        if res.is_resolved and res.level == STYLE:
            out.append(ResolvedExclusion(rule, res.canonical_name, STYLE, True,
                                         f"'{rule.product}' resolves to the style {res.canonical_name}."))
        elif res.is_resolved and res.level == PHRASE:
            out.append(ResolvedExclusion(rule, res.canonical_name, PHRASE, True,
                                         f"Catalog unavailable; '{rule.product}' is matched as an exact name."))
        elif res.is_resolved and res.level == FAMILY:
            out.append(ResolvedExclusion(rule, None, FAMILY, False,
                                         f"'{rule.product}' names the whole {res.family} family, so it does not "
                                         "exclude by name; only its SKU or subclass keys apply."))
        else:
            out.append(ResolvedExclusion(rule, None, None, False,
                                         f"'{rule.product}' is {res.status} in the catalog, so it does not "
                                         "exclude by name; only its SKU or subclass keys apply."))
    return tuple(out)


@dataclass(frozen=True)
class ProductCandidate:
    """One product at the grain the caller ranks (style, style and season, or SKU)."""

    product: str
    season: str | None
    item_return_rate: RateResult
    lifecycle: str | None = None
    sku: str | None = None
    family: str | None = None
    product_class: str | None = None
    subclass: str | None = None
    prior_return_rate: RateResult | None = None
    prior_comparable: bool = False
    recurring_themes: int = 0


def _same(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and a.strip().lower() == b.strip().lower()


def matching_exclusion(
    candidate: ProductCandidate,
    exclusions: Sequence[ResolvedExclusion],
) -> ResolvedExclusion | None:
    for ex in exclusions:
        rule = ex.rule
        if ex.applied_by_name and ex.style and match_key(candidate.product) == match_key(ex.style):
            return ex
        if candidate.sku and any(_same(candidate.sku, s) for s in rule.skus):
            return ex
        if rule.subclass and rule.family and rule.product_class and (
            _same(candidate.subclass, rule.subclass)
            and _same(candidate.family, rule.family)
            and _same(candidate.product_class, rule.product_class)
        ):
            return ex
    return None


@dataclass(frozen=True)
class RankedProduct:
    candidate: ProductCandidate
    rate_pct: float
    returned_units: int
    delivered_units: int
    excess_units: float
    delta_pp: float
    trend_pp: float | None
    status: MetricStatus
    low_sample: bool


@dataclass(frozen=True)
class MonitoringView:
    status: MetricStatus
    label: str
    overall: RateResult
    needs_attention: tuple[RankedProduct, ...] = ()
    performing_well: tuple[RankedProduct, ...] = ()
    low_sample: tuple[RankedProduct, ...] = ()
    not_ranked: tuple[tuple[ProductCandidate, str], ...] = ()
    out_of_focus: tuple[tuple[ProductCandidate, str], ...] = ()
    excluded: tuple[tuple[ProductCandidate, str], ...] = ()
    exclusions: tuple[ResolvedExclusion, ...] = ()
    reason: str = ""


def view_label(rules: Sequence[ExclusionRule] = RECOMMENDATION_EXCLUSIONS) -> str:
    names = ", ".join(r.product for r in rules) or "none"
    return (
        "Monitoring view: known seasons other than Prior; excluded from recommendations: "
        f"{names}. Overall rates include every eligible product."
    )


def build_monitoring_view(
    candidates: Sequence[ProductCandidate],
    overall: RateResult,
    catalog: ProductCatalog,
    rules: Sequence[ExclusionRule] = RECOMMENDATION_EXCLUSIONS,
    min_units: int = MIN_DELIVERED_UNITS,
) -> MonitoringView:
    exclusions = resolve_exclusions(catalog, rules)
    label = view_label(rules)
    if overall.status is MetricStatus.UNAVAILABLE or overall.value_pct is None:
        return MonitoringView(
            status=MetricStatus.UNAVAILABLE,
            label=label,
            overall=overall,
            not_ranked=tuple((c, "overall item return rate unavailable") for c in candidates),
            exclusions=exclusions,
            reason=f"Overall item return rate unavailable: {overall.reason or 'no rate'}",
        )

    baseline = overall.value_pct
    attention: list[RankedProduct] = []
    well: list[RankedProduct] = []
    low: list[RankedProduct] = []
    not_ranked: list[tuple[ProductCandidate, str]] = []
    out_of_focus: list[tuple[ProductCandidate, str]] = []
    excluded: list[tuple[ProductCandidate, str]] = []

    for c in candidates:
        ex = matching_exclusion(c, exclusions)
        if ex is not None:
            excluded.append((c, ex.rule.reason))
            continue
        season = (c.season or "").strip()
        if not season:
            out_of_focus.append((c, "season unknown"))
            continue
        if season.lower() == PRIOR_SEASON:
            out_of_focus.append((c, "Prior season"))
            continue
        rate = c.item_return_rate
        if (rate.status is MetricStatus.UNAVAILABLE or rate.value_pct is None
                or rate.numerator is None or rate.denominator is None):
            not_ranked.append((c, f"item return rate unavailable: {rate.reason or 'no rate'}"))
            continue
        prior = c.prior_return_rate
        trend = None
        if c.prior_comparable and prior is not None and prior.value_pct is not None \
                and prior.status is not MetricStatus.UNAVAILABLE:
            trend = rate.value_pct - prior.value_pct
        ranked = RankedProduct(
            candidate=c,
            rate_pct=rate.value_pct,
            returned_units=rate.numerator,
            delivered_units=rate.denominator,
            excess_units=rate.numerator - baseline / 100 * rate.denominator,
            delta_pp=rate.value_pct - baseline,
            trend_pp=trend,
            status=_weaker(rate.status, overall.status),
            low_sample=rate.denominator < min_units,
        )
        if ranked.low_sample:
            low.append(ranked)
        elif ranked.excess_units > 0:
            attention.append(ranked)
        else:
            well.append(ranked)

    attention.sort(key=lambda r: (-r.excess_units, -r.candidate.recurring_themes, -r.rate_pct, r.candidate.product))
    well.sort(key=lambda r: (r.excess_units, r.rate_pct, r.candidate.product))
    low.sort(key=lambda r: (-r.rate_pct, r.candidate.product))
    ranked_any = attention or well or low
    statuses = [r.status for r in (*attention, *well, *low)]
    return MonitoringView(
        status=_weakest(statuses) if ranked_any else MetricStatus.UNAVAILABLE,
        label=label,
        overall=overall,
        needs_attention=tuple(attention),
        performing_well=tuple(well),
        low_sample=tuple(low),
        not_ranked=tuple(not_ranked),
        out_of_focus=tuple(out_of_focus),
        excluded=tuple(excluded),
        exclusions=exclusions,
        reason="" if ranked_any else "no product in focus has an available item return rate",
    )


_ORDER = {MetricStatus.UNAVAILABLE: 0, MetricStatus.PROVISIONAL: 1, MetricStatus.VALIDATED: 2}


def _weaker(a: MetricStatus, b: MetricStatus) -> MetricStatus:
    return a if _ORDER[a] <= _ORDER[b] else b


def _weakest(statuses: Sequence[MetricStatus]) -> MetricStatus:
    out = MetricStatus.VALIDATED
    for s in statuses:
        out = _weaker(out, s)
    return out
