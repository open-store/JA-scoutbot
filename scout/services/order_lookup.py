"""
Order lookup for product attribution.

The order-linked and inferred attribution tiers need Shopify orders. The
chosen source is Snowflake (``JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDERS`` and
``FCT_SHOPIFY__ORDER_LINES``), not the Shopify Admin API, and lookups are
bounded per request.

``get_order_lookup()`` returns the Snowflake lookup only when
``SCOUT_ORDER_LOOKUP=snowflake``. Otherwise it returns
``UnavailableOrderLookup``, and attribution reports those two tiers as not
evaluated. The flag stays off until Scout's role and the order-table
columns in ``scout/repositories/order_repository.py`` are confirmed.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import List, Protocol, Sequence

from scout.models.orders import OrderRecord

logger = logging.getLogger("scout.services.order_lookup")

ORDER_LOOKUP_ENV = "SCOUT_ORDER_LOOKUP"


class OrderLookup(Protocol):
    available: bool
    source: str
    reason: str

    def orders_by_number(self, order_numbers: Sequence[str]) -> List[OrderRecord]:
        """Orders whose name digits match, any customer. Caller validates identity."""
        ...

    def orders_for_emails(self, emails: Sequence[str], start: datetime, end: datetime) -> List[OrderRecord]:
        """Reportable sales orders for these customer emails created in [start, end)."""
        ...


class UnavailableOrderLookup:
    """Order data not connected. Every lookup returns nothing."""

    available = False

    def __init__(self, reason: str, source: str = ""):
        self.reason = reason
        self.source = source

    def orders_by_number(self, order_numbers: Sequence[str]) -> List[OrderRecord]:
        return []

    def orders_for_emails(self, emails: Sequence[str], start: datetime, end: datetime) -> List[OrderRecord]:
        return []


class SafeOrderLookup:
    """Wraps a lookup so a query failure marks order data unavailable instead of failing the reply."""

    def __init__(self, inner: OrderLookup):
        self._inner = inner
        self.available = inner.available
        self.source = inner.source
        self.reason = inner.reason

    def _failed(self, e: Exception) -> List[OrderRecord]:
        logger.warning("Order lookup failed on %s: %s", self.source, type(e).__name__)
        self.available = False
        self.reason = f"Order lookup failed ({type(e).__name__})"
        return []

    def orders_by_number(self, order_numbers: Sequence[str]) -> List[OrderRecord]:
        if not self.available or not order_numbers:
            return []
        try:
            return self._inner.orders_by_number(order_numbers)
        except Exception as e:
            return self._failed(e)

    def orders_for_emails(self, emails: Sequence[str], start: datetime, end: datetime) -> List[OrderRecord]:
        if not self.available or not emails:
            return []
        try:
            return self._inner.orders_for_emails(emails, start, end)
        except Exception as e:
            return self._failed(e)


def get_order_lookup() -> OrderLookup:
    mode = os.environ.get(ORDER_LOOKUP_ENV, "").strip().lower()
    if mode == "snowflake":
        from scout.repositories.order_repository import SnowflakeOrderLookup
        return SafeOrderLookup(SnowflakeOrderLookup())
    return UnavailableOrderLookup(
        reason=(
            "Order data is not connected, so the order-linked and inferred tiers were not evaluated."
        ),
    )
