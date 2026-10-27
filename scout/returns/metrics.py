"""Return and exchange rate definitions.

Every rate is ``100 * SUM(numerator) / NULLIF(SUM(denominator), 0)`` over one
population of delivered units, written against the columns of a cohort-units
relation (``COHORT_UNITS_COLUMNS``). Scout runs ``rollup_query`` against that
relation; the Rocky dashboard can run the same SQL against the same relation,
so there is one returns calculation. Python only decides availability and
status from the summed counts; it never averages row-level rates.

The cohort-units relation is a contract here, not a warehouse object. Its SQL
would join Redo items to Shopify order lines. It stays unset: the named order
facts are not selectable, item outcome is not one column, and cohort maturity
is not a single day horizon. Every rate therefore stays unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Mapping, Sequence

from scout.returns.cohorts import CohortWindow
from scout.returns.sources import ALL_SOURCES, ReturnsSource, SourceRole

METRICS_VERSION = "returns-metrics/1"

# Contract for the cohort-units relation: one row per delivery cohort date,
# channel, customer status, and SKU. Redo measures count only returns within
# the window's observation horizon of delivery, so cohorts are comparable.
COHORT_UNITS_DIMENSIONS: dict[str, str] = {
    "delivery_cohort_date": "Delivery date of the Shopify order line, America/New_York",
    "sales_channel_group": "Channel group of the order, e.g. dtc",
    "sales_channel": "Sales channel of the order, e.g. online_store",
    "is_cohort_mature": "True when the cohort's observation horizon has passed",
    "customer_trx_status": "Customer status at the order, e.g. new or returning",
    "sku": "SKU of the delivered line",
}
COHORT_UNITS_MEASURES: dict[str, str] = {
    "eligible_delivered_units": "Shopify units on eligible delivered lines; replacement and exchange orders excluded",
    "redo_returned_units": "Redo units on qualifying returns of those lines",
    "redo_exchanged_units": "Redo returned units settled as an exchange",
    "redo_refunded_units": "Redo returned units settled as a refund",
    "redo_store_credit_units": "Redo returned units settled as store credit",
    "redo_open_units": "Redo returned units not yet settled",
}
COHORT_UNITS_COLUMNS: dict[str, str] = {**COHORT_UNITS_DIMENSIONS, **COHORT_UNITS_MEASURES}

# Relation or subquery that satisfies the contract. A shared warehouse view
# needs sign-off before it is created.
COHORT_UNITS_RELATION: str | None = None


class MetricStatus(str, Enum):
    VALIDATED = "validated"
    PROVISIONAL = "provisional"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class RateDefinition:
    metric_id: str
    label: str
    numerator: str
    denominator: str
    numerator_role: SourceRole
    denominator_role: SourceRole
    denominator_label: str

    @property
    def sql(self) -> str:
        return f"100 * SUM({self.numerator}) / NULLIF(SUM({self.denominator}), 0)"


ITEM_RETURN_RATE = RateDefinition(
    metric_id="item_return_rate",
    label="Item return rate",
    numerator="redo_returned_units",
    denominator="eligible_delivered_units",
    numerator_role=SourceRole.AUTHORITATIVE_RETURNS,
    denominator_role=SourceRole.ORDER_DENOMINATOR,
    denominator_label="eligible delivered units",
)
ITEM_EXCHANGE_RATE = RateDefinition(
    metric_id="item_exchange_rate",
    label="Item exchange rate",
    numerator="redo_exchanged_units",
    denominator="eligible_delivered_units",
    numerator_role=SourceRole.AUTHORITATIVE_RETURNS,
    denominator_role=SourceRole.ORDER_DENOMINATOR,
    denominator_label="eligible delivered units",
)
EXCHANGE_SHARE_OF_RETURNS = RateDefinition(
    metric_id="exchange_share_of_returned_units",
    label="Exchange share of returned units",
    numerator="redo_exchanged_units",
    denominator="redo_returned_units",
    numerator_role=SourceRole.AUTHORITATIVE_RETURNS,
    denominator_role=SourceRole.AUTHORITATIVE_RETURNS,
    denominator_label="Redo returned units",
)
ITEM_REFUND_RATE = RateDefinition(
    metric_id="item_refund_rate",
    label="Item refund rate",
    numerator="redo_refunded_units",
    denominator="eligible_delivered_units",
    numerator_role=SourceRole.AUTHORITATIVE_RETURNS,
    denominator_role=SourceRole.ORDER_DENOMINATOR,
    denominator_label="eligible delivered units",
)
RATE_DEFINITIONS: tuple[RateDefinition, ...] = (
    ITEM_RETURN_RATE,
    ITEM_EXCHANGE_RATE,
    EXCHANGE_SHARE_OF_RETURNS,
    ITEM_REFUND_RATE,
)


def _as_count(raw: object) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise TypeError("A unit count cannot be a boolean.")
    if isinstance(raw, (int, float, Decimal)):
        return int(raw)
    if isinstance(raw, str):
        return int(raw)
    raise TypeError(f"Unexpected unit count type: {type(raw).__name__}")


def ratio_pct(numerator: int | None, denominator: int | None) -> float | None:
    """Percent from summed counts. ``None`` for a missing count or a zero denominator."""
    if numerator is None or denominator is None or denominator <= 0:
        return None
    if numerator < 0:
        raise ValueError("A unit count cannot be negative.")
    return numerator / denominator * 100


@dataclass(frozen=True)
class UnitCounts:
    eligible_delivered_units: int | None = None
    redo_returned_units: int | None = None
    redo_exchanged_units: int | None = None
    redo_refunded_units: int | None = None
    redo_store_credit_units: int | None = None
    redo_open_units: int | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> "UnitCounts":
        lowered = {str(k).lower(): v for k, v in row.items()}
        return cls(**{name: _as_count(lowered.get(name)) for name in COHORT_UNITS_MEASURES})

    def get(self, column: str) -> int | None:
        return getattr(self, column)


@dataclass(frozen=True)
class OutcomeBreakdown:
    returned: int
    refunded: int
    exchanged: int
    store_credit: int
    open: int
    unexplained: int

    @property
    def consistent(self) -> bool:
        return self.unexplained >= 0


def outcome_breakdown(counts: UnitCounts) -> OutcomeBreakdown | None:
    """Split returned units by outcome without assuming the outcomes are exhaustive."""
    returned = counts.redo_returned_units
    refunded = counts.redo_refunded_units
    exchanged = counts.redo_exchanged_units
    credit = counts.redo_store_credit_units
    open_units = counts.redo_open_units
    if returned is None or refunded is None or exchanged is None or credit is None or open_units is None:
        return None
    return OutcomeBreakdown(
        returned=returned,
        refunded=refunded,
        exchanged=exchanged,
        store_credit=credit,
        open=open_units,
        unexplained=returned - refunded - exchanged - credit - open_units,
    )


@dataclass(frozen=True)
class RateResult:
    definition: RateDefinition
    status: MetricStatus
    numerator: int | None = None
    denominator: int | None = None
    value_pct: float | None = None
    reason: str = ""


@dataclass(frozen=True)
class RatesReport:
    results: tuple[RateResult, ...]
    window: CohortWindow | None
    scope_label: str
    blockers: tuple[str, ...] = ()
    outcomes: OutcomeBreakdown | None = None
    version: str = METRICS_VERSION
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def available(self) -> bool:
        return any(r.status is not MetricStatus.UNAVAILABLE for r in self.results)


def unavailable_results(reason: str) -> tuple[RateResult, ...]:
    return tuple(RateResult(d, MetricStatus.UNAVAILABLE, reason=reason) for d in RATE_DEFINITIONS)


def source_blockers(sources: Sequence[ReturnsSource] = ALL_SOURCES) -> dict[SourceRole, str]:
    """For each role a rate needs, why it cannot be used yet."""
    needed = (SourceRole.AUTHORITATIVE_RETURNS, SourceRole.ORDER_DENOMINATOR)
    blockers: dict[SourceRole, str] = {}
    for role in needed:
        pending = [s for s in sources if s.role is role and not s.usable]
        if pending:
            names = ", ".join(f"`{s.relation}`" if s.relation else s.label for s in pending)
            blockers[role] = f"{pending[0].label} not verified for Scout ({names})"
    return blockers


def evaluate_rates(
    counts: UnitCounts,
    *,
    blocked: Mapping[SourceRole, str] | None = None,
    mature: bool,
    reconciled: bool,
) -> tuple[RateResult, ...]:
    """Turn summed counts into rate results with an explicit status each."""
    blocked = blocked or {}
    results = []
    for definition in RATE_DEFINITIONS:
        reasons = [blocked[r] for r in (definition.numerator_role, definition.denominator_role) if r in blocked]
        if reasons:
            results.append(RateResult(definition, MetricStatus.UNAVAILABLE, reason="; ".join(dict.fromkeys(reasons))))
            continue
        numerator = counts.get(definition.numerator)
        denominator = counts.get(definition.denominator)
        if numerator is None or denominator is None:
            results.append(RateResult(definition, MetricStatus.UNAVAILABLE, numerator, denominator,
                                      reason="count missing"))
            continue
        if denominator <= 0:
            results.append(RateResult(definition, MetricStatus.UNAVAILABLE, numerator, denominator,
                                      reason=f"no {definition.denominator_label} in scope"))
            continue
        if numerator > denominator:
            results.append(RateResult(definition, MetricStatus.UNAVAILABLE, numerator, denominator,
                                      reason="numerator exceeds denominator; populations do not match"))
            continue
        if not mature:
            status, reason = MetricStatus.PROVISIONAL, "cohort observation still open"
        elif not reconciled:
            status, reason = MetricStatus.PROVISIONAL, "not yet reconciled against live data"
        else:
            status, reason = MetricStatus.VALIDATED, ""
        results.append(RateResult(definition, status, numerator, denominator,
                                  ratio_pct(numerator, denominator), reason))
    return tuple(results)


@dataclass(frozen=True)
class CohortScope:
    window: CohortWindow
    sales_channel_group: str = "dtc"
    sales_channel: str = "online_store"
    skus: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        product = f", {len(self.skus)} SKUs" if self.skus else ""
        return f"{self.sales_channel} ({self.sales_channel_group}){product}"


def rollup_query(relation: str, scope: CohortScope, group_by: Sequence[str] = ()) -> tuple[str, dict]:
    """SQL and bind parameters that sum the measures and compute every rate."""
    unknown = [c for c in group_by if c not in COHORT_UNITS_DIMENSIONS]
    if unknown:
        raise ValueError(f"Not a cohort-units dimension: {unknown}")
    select = list(group_by)
    select += [f"SUM({m}) AS {m}" for m in COHORT_UNITS_MEASURES]
    select += [f"{d.sql} AS {d.metric_id}" for d in RATE_DEFINITIONS]
    where = [
        "delivery_cohort_date >= %(start)s",
        "delivery_cohort_date < %(end)s",
        "sales_channel_group = %(channel_group)s",
        "sales_channel = %(channel)s",
    ]
    params: dict[str, object] = {
        "start": scope.window.start.isoformat(),
        "end": scope.window.end.isoformat(),
        "channel_group": scope.sales_channel_group,
        "channel": scope.sales_channel,
    }
    if scope.window.mature:
        where.append("is_cohort_mature = TRUE")
    if scope.skus:
        keys = [f"sku_{i}" for i in range(len(scope.skus))]
        where.append(f"sku IN ({', '.join(f'%({k})s' for k in keys)})")
        params.update(dict(zip(keys, scope.skus)))
    sql = f"SELECT {', '.join(select)} FROM {relation} WHERE {' AND '.join(where)}"
    if group_by:
        sql += f" GROUP BY {', '.join(group_by)}"
    return sql, params
