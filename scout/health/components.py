"""
Scorecard entries for the customer-health report.

Each entry states where a number comes from and what it covers: source,
reporting population, definition, sample and eligible counts, window,
freshness, comparison basis, and availability. Entries never borrow a
value from another source; a missing value stays ``None``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

# Availability of one scorecard entry.
AVAILABLE = "available"
LOW_SAMPLE = "low_sample"
STALE = "stale"
PROVISIONAL = "provisional"
UNAVAILABLE = "unavailable"

# Freshness of the data behind an entry.
FRESH = "fresh"
STALE_DATA = "stale"
UNKNOWN = "unknown"
LIVE = "live"  # read from an API at request time


@dataclass(frozen=True)
class Freshness:
    status: str
    last_synced: Optional[datetime] = None
    latest_record: Optional[datetime] = None
    note: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "last_synced": self.last_synced.isoformat() if self.last_synced else None,
            "latest_record": self.latest_record.isoformat() if self.latest_record else None,
            "note": self.note,
        }


@dataclass(frozen=True)
class Comparison:
    """The same definition over an earlier window. ``None`` values mean no comparison."""

    basis: str
    prior_value: Optional[float] = None
    prior_sample: Optional[int] = None

    def delta(self, current: Optional[float]) -> Optional[float]:
        if current is None or self.prior_value is None:
            return None
        return round(current - self.prior_value, 2)


@dataclass(frozen=True)
class ScorecardEntry:
    key: str
    label: str
    source: str
    population: str
    definition: str
    status: str
    value: Optional[float] = None
    unit: str = ""
    sample: Optional[int] = None
    sample_label: str = ""
    eligible: Optional[int] = None
    eligible_label: str = ""
    window: str = ""
    freshness: Freshness = field(default_factory=lambda: Freshness(UNKNOWN))
    comparison: Optional[Comparison] = None
    coverage_note: str = ""
    reasons: List[str] = field(default_factory=list)
    details: Dict[str, object] = field(default_factory=dict)

    @property
    def has_value(self) -> bool:
        return self.value is not None and self.status != UNAVAILABLE

    def to_dict(self) -> Dict[str, object]:
        comparison = None
        if self.comparison is not None:
            comparison = {
                "basis": self.comparison.basis,
                "prior_value": self.comparison.prior_value,
                "prior_sample": self.comparison.prior_sample,
                "delta": self.comparison.delta(self.value),
            }
        return {
            "key": self.key,
            "label": self.label,
            "source": self.source,
            "population": self.population,
            "definition": self.definition,
            "status": self.status,
            "value": self.value,
            "unit": self.unit,
            "sample": self.sample,
            "sample_label": self.sample_label,
            "eligible": self.eligible,
            "eligible_label": self.eligible_label,
            "window": self.window,
            "freshness": self.freshness.to_dict(),
            "comparison": comparison,
            "coverage_note": self.coverage_note,
            "reasons": list(self.reasons),
            "details": dict(self.details),
        }


def unavailable(key: str, label: str, source: str, population: str, definition: str,
                reason: str, window: str = "") -> ScorecardEntry:
    return ScorecardEntry(key=key, label=label, source=source, population=population,
                          definition=definition, status=UNAVAILABLE, window=window, reasons=[reason])
