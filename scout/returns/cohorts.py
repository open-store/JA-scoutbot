"""Delivery-cohort windows for return and exchange rates.

An ``L{N}`` mature report covers the N delivery days that ended
``MATURITY_DAYS`` before today in America/New_York, and counts returns within
``MATURITY_DAYS`` of delivery. Every cohort in a window therefore has the same
observation age, and a prior window is comparable only when it uses the same
horizon. Returns processed in a period are a different population; see
``queries/returns.py`` for refund volume.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

BUSINESS_TZ_NAME = "America/New_York"
BUSINESS_TZ = ZoneInfo(BUSINESS_TZ_NAME)

# SHOPIFY_AGG_POST_PURCHASE.IS_COHORT_MATURE is not one day cutoff: immature
# rows exist on and before the latest mature cohort date. None until that flag
# can be written as a single horizon. Rates stay unavailable while it is None.
MATURITY_DAYS: int | None = None


@dataclass(frozen=True)
class CohortWindow:
    label: str
    start: date
    end: date
    observation_days: int
    as_of: date
    mature: bool
    timezone: str = BUSINESS_TZ_NAME

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    def describe(self) -> str:
        last = self.end - timedelta(days=1)
        state = "mature" if self.mature else "provisional, observation still open"
        return (
            f"deliveries {self.start:%b %d} – {last:%b %d, %Y} (ET), returns within "
            f"{self.observation_days} days of delivery, {state}"
        )


def today_et(now: datetime | None = None) -> date:
    moment = now.astimezone(BUSINESS_TZ) if now else datetime.now(BUSINESS_TZ)
    return moment.date()


def mature_window(days: int, maturity_days: int, as_of: date) -> CohortWindow:
    """Most recent ``days`` delivery days whose full observation horizon has passed."""
    if days <= 0 or maturity_days <= 0:
        raise ValueError("days and maturity_days must be positive")
    end = as_of - timedelta(days=maturity_days)
    return CohortWindow(
        label=f"L{days}",
        start=end - timedelta(days=days),
        end=end,
        observation_days=maturity_days,
        as_of=as_of,
        mature=True,
    )


def provisional_window(days: int, maturity_days: int, as_of: date) -> CohortWindow:
    """The latest ``days`` delivery days, still inside the observation horizon."""
    if days <= 0 or maturity_days <= 0:
        raise ValueError("days and maturity_days must be positive")
    return CohortWindow(
        label=f"L{days}",
        start=as_of - timedelta(days=days),
        end=as_of,
        observation_days=maturity_days,
        as_of=as_of,
        mature=False,
    )


def comparable(current: CohortWindow, other: CohortWindow) -> bool:
    """True only for two mature windows of equal length and observation horizon."""
    return (
        current.mature
        and other.mature
        and current.days == other.days
        and current.observation_days == other.observation_days
        and current.timezone == other.timezone
    )


def prior_window(window: CohortWindow) -> CohortWindow:
    """The equally long window just before *window*, with the same horizon.

    A provisional window's predecessor has had more time to collect returns,
    so ``comparable`` rejects that pair.
    """
    shift = timedelta(days=window.days)
    return CohortWindow(
        label=f"{window.label} prior",
        start=window.start - shift,
        end=window.start,
        observation_days=window.observation_days,
        as_of=window.as_of,
        mature=window.mature,
        timezone=window.timezone,
    )
