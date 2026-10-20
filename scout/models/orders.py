"""
Shopify order records used for product attribution.

Pure data containers. Rows come from an ``OrderLookup`` implementation
(``scout/services/order_lookup.py``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple


@dataclass(frozen=True)
class OrderLine:
    line_id: str
    product_title: str
    quantity: Optional[int] = None


@dataclass(frozen=True)
class OrderRecord:
    order_id: str
    order_name: Optional[str]
    customer_email: Optional[str]
    created_at: Optional[datetime]
    lines: Tuple[OrderLine, ...] = ()

    @property
    def order_number(self) -> str:
        """Digits of the order name (``#12345`` → ``12345``)."""
        return "".join(ch for ch in (self.order_name or "") if ch.isdigit())
