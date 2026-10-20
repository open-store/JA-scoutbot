"""Redo fields Scout needs, and the RETURN_REDO columns that will hold them.

This is the only place Scout names a Redo column. Every field is unverified
and has no column until ``python -m scout.returns.discovery`` has profiled
``ANALYTICS.RETURN_REDO``. Code that needs a field calls ``missing_fields``
and reports itself unavailable instead of guessing a column.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from scout.returns.sources import REDO_RETURNS


@dataclass(frozen=True)
class RedoField:
    name: str
    meaning: str
    column: str | None = None
    relation: str | None = None
    verified: bool = False

    @property
    def usable(self) -> bool:
        return self.verified and self.column is not None


_FIELDS = (
    RedoField("return_id", "Redo return; one return can hold several items"),
    RedoField("return_item_id", "One returned item within a return"),
    RedoField("shopify_order_id", "Original Shopify order of the returned item"),
    RedoField("shopify_line_item_id", "Original Shopify order line of the returned item"),
    RedoField("sku", "SKU of the returned item"),
    RedoField("quantity", "Units on the returned item"),
    RedoField("status", "Return lifecycle, such as requested, open, received, or settled; values unread"),
    RedoField("outcome", "Settlement per item, such as refund, exchange, or store credit; values unread"),
    RedoField("exchange_order_id", "Replacement order Redo created for an exchange"),
    RedoField("primary_reason", "Structured reason the customer chose"),
    RedoField("secondary_reason", "Structured follow-up reason the customer chose"),
    RedoField("customer_comment", "The customer's own free-text comment"),
    RedoField("internal_note", "Staff or operational note on the return"),
    RedoField("requested_at", "When the customer started the return"),
    RedoField("received_at", "When the item was received back"),
    RedoField("settled_at", "When the outcome was issued"),
)

REDO_FIELDS: dict[str, RedoField] = {f.name: f for f in _FIELDS}

REASON_FIELDS = ("return_id", "return_item_id", "quantity", "primary_reason", "secondary_reason")
COMMENT_FIELDS = ("return_id", "return_item_id", "sku", "customer_comment")


def unverified_reason(missing: Iterable[str]) -> str:
    return f"Redo fields not yet verified in `{REDO_RETURNS.relation}`: {', '.join(missing)}"


def missing_fields(names: Iterable[str], fields: dict[str, RedoField] | None = None) -> list[str]:
    """Logical fields in *names* that are not yet verified to a column."""
    table = REDO_FIELDS if fields is None else fields
    out = []
    for name in names:
        field = table.get(name)
        if field is None:
            raise KeyError(f"Unknown Redo field: {name}")
        if not field.usable:
            out.append(name)
    return out
