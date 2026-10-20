"""Structured return reasons from Redo, with coverage.

Shares are returned units with a reason over all returned units in the
population, so items without a reason stay in the denominator as
``NO_REASON``. Items are deduplicated by return and item ID first, so a join
to comments or events cannot multiply units. Until the Redo reason fields
are verified (``scout.returns.redo_fields``), the summary is unavailable.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from scout.returns.metrics import MetricStatus, ratio_pct
from scout.returns.redo_fields import REASON_FIELDS, RedoField, missing_fields, unverified_reason

NO_REASON = "No reason recorded"


@dataclass(frozen=True)
class ReturnItem:
    return_id: str
    item_id: str
    units: int
    product: str | None = None
    sku: str | None = None
    primary_reason: str | None = None
    secondary_reason: str | None = None
    comment: str | None = None


@dataclass(frozen=True)
class ReasonCount:
    reason: str
    units: int
    returns: int
    share_of_units_pct: float | None
    secondary: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class ReasonCoverage:
    items: int
    units: int
    returns: int
    units_with_reason: int
    units_with_comment: int
    units_with_neither: int
    returns_with_reason: int
    returns_with_comment: int
    duplicate_items_dropped: int


@dataclass(frozen=True)
class ReasonSummary:
    status: MetricStatus
    population: str
    reasons: tuple[ReasonCount, ...] = ()
    coverage: ReasonCoverage | None = None
    reason: str = ""


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = " ".join(value.split())
    return stripped or None


def unique_items(items: Iterable[ReturnItem]) -> tuple[list[ReturnItem], int]:
    """First occurrence of each (return, item) pair, and how many repeats were dropped."""
    seen: set[tuple[str, str]] = set()
    kept: list[ReturnItem] = []
    dropped = 0
    for item in items:
        key = (str(item.return_id), str(item.item_id))
        if key in seen:
            dropped += 1
            continue
        if item.units < 0:
            raise ValueError("Returned units cannot be negative.")
        seen.add(key)
        kept.append(item)
    return kept, dropped


def unavailable_reason_summary(
    population: str,
    fields: dict[str, RedoField] | None = None,
) -> ReasonSummary | None:
    """An unavailable summary naming the unverified fields, or None when all are verified."""
    missing = missing_fields(REASON_FIELDS, fields)
    if not missing:
        return None
    return ReasonSummary(
        status=MetricStatus.UNAVAILABLE,
        population=population,
        reason=unverified_reason(missing),
    )


def summarize_reasons(items: Sequence[ReturnItem], population: str) -> ReasonSummary:
    """Reason counts and unit shares for *items*. Never validated: Redo is not reconciled yet."""
    kept, dropped = unique_items(items)
    total_units = sum(i.units for i in kept)
    units_by_reason: Counter[str] = Counter()
    returns_by_reason: dict[str, set[str]] = defaultdict(set)
    secondary_by_reason: dict[str, Counter[str]] = defaultdict(Counter)
    returns_with_reason: set[str] = set()
    returns_with_comment: set[str] = set()
    units_with_reason = units_with_comment = units_with_neither = 0

    for item in kept:
        primary = _text(item.primary_reason)
        comment = _text(item.comment)
        label = primary or NO_REASON
        units_by_reason[label] += item.units
        returns_by_reason[label].add(item.return_id)
        secondary = _text(item.secondary_reason)
        if primary and secondary:
            secondary_by_reason[label][secondary] += item.units
        if primary:
            units_with_reason += item.units
            returns_with_reason.add(item.return_id)
        if comment:
            units_with_comment += item.units
            returns_with_comment.add(item.return_id)
        if not primary and not comment:
            units_with_neither += item.units

    reasons = sorted(
        (
            ReasonCount(
                reason=label,
                units=units,
                returns=len(returns_by_reason[label]),
                share_of_units_pct=ratio_pct(units, total_units),
                secondary=tuple(secondary_by_reason[label].most_common()),
            )
            for label, units in units_by_reason.items()
        ),
        key=lambda r: (r.reason == NO_REASON, -r.units, r.reason),
    )
    coverage = ReasonCoverage(
        items=len(kept),
        units=total_units,
        returns=len({i.return_id for i in kept}),
        units_with_reason=units_with_reason,
        units_with_comment=units_with_comment,
        units_with_neither=units_with_neither,
        returns_with_reason=len(returns_with_reason),
        returns_with_comment=len(returns_with_comment),
        duplicate_items_dropped=dropped,
    )
    status = MetricStatus.PROVISIONAL if kept else MetricStatus.UNAVAILABLE
    return ReasonSummary(
        status=status,
        population=population,
        reasons=tuple(reasons),
        coverage=coverage,
        reason="" if kept else "no returned items in scope",
    )
