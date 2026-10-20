"""
Versioned customer-health composite.

``DEFAULT_CONFIG`` is provisional: the weights, minimum samples, and
staleness limit are proposed defaults, not validated business targets, and
no Jack Archer target or historical baseline is configured. Each metric is
scaled linearly over its full range (``Normalization``), so the score says
where a value sits on its own scale, not whether it beats a benchmark.

Missing-data rules:

- A component enters only if its entry is available, not stale, has a
  value, and meets ``min_sample``. Anything else is excluded with a reason.
  An excluded component is never scored as zero or imputed.
- The composite needs ``min_components`` included components covering at
  least ``min_weight_coverage`` of the total weight; otherwise it is
  unavailable and the scorecard is the answer.
- With partial coverage, weights are rescaled over the covered weight and
  the output says so, lists what was excluded, and gives the range the full
  score could take for any value of the missing components.
- A prior-period score is shown only when every included component has a
  prior value meeting its minimum sample; otherwise no trend is shown.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

from scout.health.components import AVAILABLE, STALE_DATA, UNAVAILABLE, ScorecardEntry

HEALTH_SCORE_VERSION = "customer-health/0.1"

PROVISIONAL = "provisional"
SCORE_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Normalization:
    """Linear map of a raw value onto 0–100: ``low`` → 0 and ``high`` → 100, clamped.

    Setting ``low`` above ``high`` encodes "lower is better".
    """

    label: str
    low: float
    high: float

    def apply(self, raw: float) -> float:
        scaled = (raw - self.low) / (self.high - self.low) * 100
        return max(0.0, min(100.0, scaled))

    @property
    def direction(self) -> str:
        return "higher is better" if self.high > self.low else "lower is better"


@dataclass(frozen=True)
class ComponentRule:
    key: str
    label: str
    weight: float
    normalization: Normalization
    min_sample: int
    sample_label: str
    rationale: str
    accept_statuses: Tuple[str, ...] = (AVAILABLE,)


@dataclass(frozen=True)
class HealthScoreConfig:
    version: str
    components: Tuple[ComponentRule, ...]
    min_weight_coverage: float = 0.70
    min_components: int = 3
    stale_after_hours: int = 48
    provisional: bool = True
    reference_note: str = (
        "No Jack Archer targets or historical baselines are configured, so each metric "
        "is scaled over its full range. Weights and minimums are proposed defaults."
    )

    @property
    def total_weight(self) -> float:
        return sum(c.weight for c in self.components)


DEFAULT_CONFIG = HealthScoreConfig(
    version=HEALTH_SCORE_VERSION,
    components=(
        ComponentRule(
            key="nps_survey",
            label="NPS (NPS Survey)",
            weight=0.30,
            normalization=Normalization("NPS −100 to +100 → 0 to 100", -100.0, 100.0),
            min_sample=30,
            sample_label="survey responses",
            rationale="Relationship loyalty. Only the NPS Survey; the PPS surveys are separate populations.",
        ),
        ComponentRule(
            key="support_csat",
            label="Support CSAT",
            weight=0.25,
            normalization=Normalization("CSAT 0% to 100% → 0 to 100", 0.0, 100.0),
            min_sample=30,
            sample_label="rated tickets",
            rationale="Support experience, from customers who rated a ticket.",
        ),
        ComponentRule(
            key="reviews",
            label="Okendo rating",
            weight=0.20,
            normalization=Normalization("1★ to 5★ → 0 to 100", 1.0, 5.0),
            min_sample=20,
            sample_label="approved reviews",
            rationale="Product satisfaction from reviewers.",
        ),
        ComponentRule(
            key="returns",
            label="Item return rate",
            weight=0.25,
            normalization=Normalization("return rate 0% to 100% → 100 to 0", 100.0, 0.0),
            min_sample=500,
            sample_label="eligible delivered units",
            rationale=(
                "Product fit and expectation, as Redo returned units over eligible delivered units. "
                "Only a validated rate counts. Exchange metrics are shown but not scored: a higher "
                "exchange share is not treated as better or worse."
            ),
        ),
    ),
)


@dataclass(frozen=True)
class ComponentScore:
    key: str
    label: str
    weight: float
    included: bool
    raw: Optional[float] = None
    normalized: Optional[float] = None
    reason: str = ""
    normalization: str = ""
    direction: str = ""
    prior_normalized: Optional[float] = None

    def to_dict(self) -> Dict[str, object]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class HealthScore:
    version: str
    status: str
    value: Optional[float]
    covered_weight: float
    total_weight: float
    components: Tuple[ComponentScore, ...]
    range_if_complete: Optional[Tuple[float, float]] = None
    prior_value: Optional[float] = None
    comparison_note: str = ""
    notes: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def coverage(self) -> float:
        return self.covered_weight / self.total_weight if self.total_weight else 0.0

    @property
    def partial(self) -> bool:
        return self.value is not None and self.coverage < 0.999

    @property
    def delta(self) -> Optional[float]:
        if self.value is None or self.prior_value is None:
            return None
        return round(self.value - self.prior_value, 1)

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": self.version,
            "status": self.status,
            "value": self.value,
            "coverage": round(self.coverage, 4),
            "covered_weight": self.covered_weight,
            "total_weight": self.total_weight,
            "partial": self.partial,
            "range_if_complete": list(self.range_if_complete) if self.range_if_complete else None,
            "prior_value": self.prior_value,
            "delta": self.delta,
            "comparison_note": self.comparison_note,
            "components": [c.to_dict() for c in self.components],
            "notes": list(self.notes),
        }


def _exclusion(rule: ComponentRule, entry: Optional[ScorecardEntry]) -> str:
    if entry is None:
        return "not collected"
    if entry.status == UNAVAILABLE or entry.value is None:
        return "unavailable" + (f": {entry.reasons[0]}" if entry.reasons else "")
    if entry.freshness.status == STALE_DATA:
        return "stale data" + (f" ({entry.freshness.note})" if entry.freshness.note else "")
    if entry.status not in rule.accept_statuses:
        detail = f": {entry.reasons[0]}" if entry.reasons else ""
        return f"{entry.status}{detail}"
    if entry.sample is None or entry.sample < rule.min_sample:
        return f"below minimum sample ({entry.sample or 0} of {rule.min_sample} {rule.sample_label})"
    return ""


def _prior(rule: ComponentRule, entry: ScorecardEntry) -> Optional[float]:
    c = entry.comparison
    if c is None or c.prior_value is None or c.prior_sample is None or c.prior_sample < rule.min_sample:
        return None
    return rule.normalization.apply(c.prior_value)


def compute_health_score(
    entries: Mapping[str, ScorecardEntry],
    config: HealthScoreConfig = DEFAULT_CONFIG,
) -> HealthScore:
    parts: List[ComponentScore] = []
    for rule in config.components:
        entry = entries.get(rule.key)
        reason = _exclusion(rule, entry)
        if reason or entry is None or entry.value is None:
            parts.append(ComponentScore(
                key=rule.key, label=rule.label, weight=rule.weight, included=False,
                raw=entry.value if entry else None, reason=reason or "not collected",
                normalization=rule.normalization.label, direction=rule.normalization.direction,
            ))
            continue
        parts.append(ComponentScore(
            key=rule.key, label=rule.label, weight=rule.weight, included=True,
            raw=entry.value, normalized=round(rule.normalization.apply(entry.value), 1),
            normalization=rule.normalization.label, direction=rule.normalization.direction,
            prior_normalized=_prior(rule, entry),
        ))

    included = [p for p in parts if p.included]
    covered = round(sum(p.weight for p in included), 4)
    total = round(config.total_weight, 4)
    notes: List[str] = [config.reference_note]

    if len(included) < config.min_components or covered < config.min_weight_coverage * total - 1e-9:
        notes.insert(0, (
            f"Not enough usable components: {len(included)} cover {covered / total:.0%} of the weight; "
            f"the score needs {config.min_components} components and {config.min_weight_coverage:.0%}."
        ))
        return HealthScore(config.version, SCORE_UNAVAILABLE, None, covered, total, tuple(parts),
                           notes=tuple(notes))

    weighted = sum(p.weight * (p.normalized or 0.0) for p in included)
    value = round(weighted / covered, 1)
    missing = round(total - covered, 4)
    bounds = None
    if missing > 0:
        bounds = (round(weighted / total, 1), round((weighted + missing * 100) / total, 1))
        notes.insert(0, (
            f"Partial: weights rescaled over the {covered / total:.0%} of the weight with usable data. "
            "Excluded components are not scored as zero."
        ))

    prior_value = None
    comparison_note = ""
    if all(p.prior_normalized is not None for p in included):
        prior_value = round(sum(p.weight * (p.prior_normalized or 0.0) for p in included) / covered, 1)
        comparison_note = "Previous window, same components and weights."
    else:
        lacking = [p.label for p in included if p.prior_normalized is None]
        comparison_note = f"No trend: no comparable prior value for {', '.join(lacking)}."

    status = PROVISIONAL if config.provisional else AVAILABLE
    return HealthScore(config.version, status, value, covered, total, tuple(parts),
                       range_if_complete=bounds, prior_value=prior_value,
                       comparison_note=comparison_note, notes=tuple(notes))
